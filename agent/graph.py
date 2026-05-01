"""LangGraph assembly — DocAssist document generation pipeline.

Entry point:
    from agent.graph import build_graph
    graph = build_graph()
    config = {"configurable": {"thread_id": "1"}}
    result = graph.invoke({"raw_input": "Rechnung an Müller GmbH ..."}, config=config)
"""
import json
import operator
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent.client_lookup import load_clients, lookup_client
from agent.compliance_engine import compliance_check
from agent.extractor import extract_scope
from agent.invoice_generator import build_invoice
from agent.models import (
    BusinessProfile,
    ClientRecord,
    ComplianceResult,
    InvoiceModel,
    QuoteModel,
    ResolvedScope,
    ResolvedServiceLine,
    ScopeModel,
)
from agent.observability import persist_run
from agent.quote_generator import generate_quote
from agent.rate_resolver import resolve_rates

load_dotenv()

_ROOT = Path(__file__).parent.parent
_CONFIG_DIR = _ROOT / "config"
_DATA_DIR = _ROOT / "data"
_PROMPTS_DIR = _ROOT / "prompts"

# ---------------------------------------------------------------------------
# Cached resource loading
# ---------------------------------------------------------------------------

_model_cfg: dict = {}
_profile: BusinessProfile | None = None
_clients: list[ClientRecord] = []
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_model_cfg() -> dict:
    global _model_cfg
    if not _model_cfg:
        _model_cfg = yaml.safe_load((_CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    return _model_cfg


def _get_profile() -> BusinessProfile:
    global _profile
    if _profile is None:
        path = _CONFIG_DIR / "business_profile.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        _profile = BusinessProfile(**data)
    return _profile


def _get_clients() -> list[ClientRecord]:
    global _clients
    if not _clients:
        _clients = load_clients()
    return _clients


def _apply_node_env(node_name: str) -> None:
    """Set DOCASSIST_PROVIDER and ANTHROPIC_MODEL from models.yaml for this node."""
    cfg = _get_model_cfg().get("nodes", {}).get(node_name, {})
    if "provider" in cfg:
        os.environ["DOCASSIST_PROVIDER"] = cfg["provider"]
    if "model" in cfg:
        os.environ["ANTHROPIC_MODEL"] = cfg["model"]


def _current_model() -> str | None:
    """Return the active model name after _apply_node_env() has been called."""
    provider = os.environ.get("DOCASSIST_PROVIDER", "local").lower()
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL")
    return os.environ.get("LOCAL_LLM_MODEL")


# ---------------------------------------------------------------------------
# Invoice number sequence (replaced by SQLite in Phase 9)
# ---------------------------------------------------------------------------

_COUNTER_FILE = _DATA_DIR / "invoice_counter.json"


def _next_invoice_number() -> str:
    today = date.today()
    if _COUNTER_FILE.exists():
        data = json.loads(_COUNTER_FILE.read_text(encoding="utf-8"))
        year = data.get("year", today.year)
        seq = data.get("seq", 0)
        if year != today.year:
            year, seq = today.year, 0
    else:
        year, seq = today.year, 0
    seq += 1
    _COUNTER_FILE.write_text(json.dumps({"year": year, "seq": seq}), encoding="utf-8")
    return f"RE-{year}-{seq:03d}"


# ---------------------------------------------------------------------------
# Compliance correction helpers
# ---------------------------------------------------------------------------

_correction_prompt_cache: dict[str, str] = {}


def _load_correction_prompt(version: str = "v1") -> str:
    if version not in _correction_prompt_cache:
        _correction_prompt_cache[version] = (
            _PROMPTS_DIR / "compliance_correction" / f"{version}.md"
        ).read_text(encoding="utf-8")
    return _correction_prompt_cache[version]


def _call_correction_llm(system: str, user: str) -> tuple[str, int | None, int | None]:
    """Return (text, input_tokens, output_tokens). Tokens are None for local models."""
    provider = os.environ.get("DOCASSIST_PROVIDER", "local").lower()
    if provider == "anthropic":
        client = _get_anthropic_client()
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.0,
        )
        return resp.content[0].text, resp.usage.input_tokens, resp.usage.output_tokens
    else:
        from openai import OpenAI
        client = OpenAI(
            base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://192.168.1.181:1234/v1"),
            api_key=os.environ.get("LOCAL_LLM_API_KEY", "local"),
        )
        resp = client.chat.completions.create(
            model=os.environ.get("LOCAL_LLM_MODEL", "gemma-4-26b-a4b-it-mlx"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=256,
        )
        usage = resp.usage
        return (
            resp.choices[0].message.content or "",
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
        )


def _parse_correction_patch(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _apply_correction_patch(invoice: InvoiceModel, patch: dict) -> InvoiceModel:
    updates: dict = {}
    for date_field in ("delivery_date", "service_period_from", "service_period_to"):
        val = patch.get(date_field)
        if val:
            try:
                updates[date_field] = date.fromisoformat(val)
            except ValueError:
                pass
    for str_field in ("recipient_name", "recipient_address_line1", "recipient_address_line2", "recipient_uid"):
        val = patch.get(str_field)
        if val:
            updates[str_field] = val
    return invoice.model_copy(update=updates) if updates else invoice


# ---------------------------------------------------------------------------
# Metadata helper
# ---------------------------------------------------------------------------

def _meta(
    node: str,
    start: float,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict:
    return {
        "node": node,
        "timestamp": start,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class DocAssistState(TypedDict):
    request_id: str                  # UUID assigned at initial_state(); used as SQLite FK
    raw_input: str
    language_override: str | None    # "de" | "en" — set by frontend switcher, overrides LLM detection
    rate_overrides: dict | None      # e.g. {"labor_hourly": 90.0} — overrides profile default_rates for this run
    # Pydantic models serialised as dicts — safe for JSON checkpointing in Phase 9
    scope: dict | None               # ScopeModel
    client: dict | None              # ClientRecord
    resolved_scope: dict | None      # ResolvedScope
    quote: dict | None               # QuoteModel
    approval_status: Literal["pending", "approved", "rejected"] | None
    approval_feedback: str | None
    invoice: dict | None             # InvoiceModel
    compliance_result: dict | None   # ComplianceResult
    pdf_bytes: bytes | None
    # Accumulating fields — each node appends, never overwrites
    clarifications_needed: Annotated[list[str], operator.add]
    per_node_metadata: Annotated[list[dict], operator.add]
    # Compliance correction loop counter
    correction_attempts: int
    error: str | None


def initial_state(raw_input: str, language_override: str | None = None) -> DocAssistState:
    """Return a fully initialised state dict for a new run."""
    return DocAssistState(
        request_id=str(uuid.uuid4()),
        raw_input=raw_input,
        language_override=language_override,
        rate_overrides=None,
        scope=None,
        client=None,
        resolved_scope=None,
        quote=None,
        approval_status=None,
        approval_feedback=None,
        invoice=None,
        compliance_result=None,
        pdf_bytes=None,
        clarifications_needed=[],
        per_node_metadata=[],
        correction_attempts=0,
        error=None,
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_extract(state: DocAssistState) -> dict:
    t = time.monotonic()
    _apply_node_env("extract")
    scope, in_tok, out_tok = extract_scope(state["raw_input"])
    if state.get("language_override") in ("de", "en"):
        scope = scope.model_copy(update={"language": state["language_override"]})
    return {
        "scope": scope.model_dump(mode="json"),
        "per_node_metadata": [_meta("node_extract", t, _current_model(), in_tok, out_tok)],
    }


def node_scope_clarify(state: DocAssistState) -> dict:
    """Suspend graph — ask owner to provide a clearer job description."""
    answer = interrupt({
        "type": "scope_clarification",
        "message": (
            "The job description is too vague to generate a quote. "
            "Please describe the specific services, quantities, and client name."
        ),
        "original_input": state["raw_input"],
    })
    return {
        "raw_input": answer.get("clarified_input", state["raw_input"]),
        "clarifications_needed": ["scope: owner re-described the job"],
    }


def node_client_lookup(state: DocAssistState) -> dict:
    t = time.monotonic()
    scope = ScopeModel.model_validate(state["scope"])
    client = lookup_client(scope.client_ref, _get_clients())
    return {
        "client": client.model_dump(mode="json") if client else None,
        "per_node_metadata": [_meta("node_client_lookup", t)],
    }


def node_resolve_rates(state: DocAssistState) -> dict:
    t = time.monotonic()
    scope = ScopeModel.model_validate(state["scope"])
    client = ClientRecord.model_validate(state["client"]) if state["client"] else None
    profile = _get_profile()
    overrides = state.get("rate_overrides") or {}
    if overrides:
        valid = {k: float(v) for k, v in overrides.items()
                 if v is not None and hasattr(profile.default_rates, k)}
        if valid:
            profile = profile.model_copy(
                update={"default_rates": profile.default_rates.model_copy(update=valid)}
            )
    rs = resolve_rates(scope, profile, client)
    return {
        "resolved_scope": rs.model_dump(mode="json"),
        "per_node_metadata": [_meta("node_resolve_rates", t)],
    }


def node_rate_clarify(state: DocAssistState) -> dict:
    """Suspend graph — ask owner to provide rates for unresolved service lines."""
    rs = ResolvedScope.model_validate(state["resolved_scope"])
    unresolved_descriptions = [u.description for u in rs.unresolved]

    answer = interrupt({
        "type": "rate_clarification",
        "message": "The following services have no configured rate. Please provide a price (€) for each:",
        "services": unresolved_descriptions,
    })
    provided_rates: dict[str, float] = answer.get("rates", {})

    newly_resolved = []
    for svc in rs.unresolved:
        rate = provided_rates.get(svc.description)
        if rate is not None:
            newly_resolved.append(ResolvedServiceLine(
                description=svc.description,
                quantity=svc.quantity,
                unit=svc.unit,
                rate=float(rate),
            ))

    # Clear unresolved after one clarification round — proceed with what we have
    patched_rs = rs.model_copy(update={
        "resolved": rs.resolved + newly_resolved,
        "unresolved": [],
    })
    return {
        "resolved_scope": patched_rs.model_dump(mode="json"),
        "clarifications_needed": [f"rates: {', '.join(unresolved_descriptions)}"],
    }


def node_generate_quote(state: DocAssistState) -> dict:
    t = time.monotonic()
    _apply_node_env("quote_generate")
    rs = ResolvedScope.model_validate(state["resolved_scope"])
    quote, in_tok, out_tok = generate_quote(rs, rejection_feedback=state.get("approval_feedback"))
    return {
        "quote": quote.model_dump(mode="json") if quote else None,
        "approval_status": "pending",
        "approval_feedback": None,
        "per_node_metadata": [_meta("node_generate_quote", t, _current_model(), in_tok, out_tok)],
    }


def node_human_review(state: DocAssistState) -> dict:
    """Suspend graph — owner reviews the draft quote and approves or rejects it."""
    decision = interrupt({
        "type": "human_review",
        "message": "Please review the quote and approve or reject it.",
        "quote": state["quote"],
    })
    return {
        "approval_status": decision.get("status", "pending"),
        "approval_feedback": decision.get("feedback"),
    }


def node_build_invoice(state: DocAssistState) -> dict:
    t = time.monotonic()
    quote = QuoteModel.model_validate(state["quote"])
    invoice = build_invoice(
        quote=quote,
        profile=_get_profile(),
        invoice_number=_next_invoice_number(),
        invoice_date=date.today(),
        delivery_date=None,         # compliance correction agent fills if needed
        service_period_from=None,
        service_period_to=None,
    )
    return {
        "invoice": invoice.model_dump(mode="json"),
        "correction_attempts": 0,
        "per_node_metadata": [_meta("node_build_invoice", t)],
    }


def node_check_compliance(state: DocAssistState) -> dict:
    t = time.monotonic()
    invoice = InvoiceModel.model_validate(state["invoice"])
    result = compliance_check(invoice)
    return {
        "compliance_result": result.model_dump(mode="json"),
        "per_node_metadata": [_meta("node_check_compliance", t)],
    }


def node_correct_compliance(state: DocAssistState) -> dict:
    t = time.monotonic()
    _apply_node_env("compliance_correct")
    invoice = InvoiceModel.model_validate(state["invoice"])
    result = ComplianceResult.model_validate(state["compliance_result"])

    system = _load_correction_prompt("v1")
    failure_lines = "\n".join(f"- {f.field}: {f.reason}" for f in result.failures)
    user = (
        f"Original job description:\n{state['raw_input']}\n\n"
        f"Current invoice data:\n"
        f"{json.dumps(invoice.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n\n"
        f"Compliance failures to fix:\n{failure_lines}"
    )

    provider = os.environ.get("DOCASSIST_PROVIDER", "local").lower()
    model_name = (
        os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        if provider == "anthropic"
        else os.environ.get("LOCAL_LLM_MODEL", "local")
    )
    in_tok = out_tok = None
    try:
        raw, in_tok, out_tok = _call_correction_llm(system, user)
        patch = _parse_correction_patch(raw)
        patched_invoice = _apply_correction_patch(invoice, patch)
    except Exception:
        patched_invoice = invoice  # no change — next compliance check will still fail → END

    return {
        "invoice": patched_invoice.model_dump(mode="json"),
        "correction_attempts": state["correction_attempts"] + 1,
        "per_node_metadata": [_meta("node_correct_compliance", t, model_name, in_tok, out_tok)],
    }


def render_pdf(invoice: InvoiceModel, profile: BusinessProfile) -> bytes:
    """Render an InvoiceModel to PDF bytes using the Jinja2 template for invoice.language."""
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML

    template_dir = _ROOT / "templates" / (invoice.language or "de")
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    tmpl = env.get_template("invoice.html")

    service_period = None
    if invoice.service_period_from and invoice.service_period_to:
        service_period = f"{invoice.service_period_from} – {invoice.service_period_to}"
    elif invoice.delivery_date:
        service_period = str(invoice.delivery_date)

    html_str = tmpl.render(
        doc_type="Rechnung" if invoice.language == "de" else "Invoice",
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        supplier={
            "name": profile.name,
            "address_line1": profile.address_line1,
            "address_line2": profile.address_line2,
            "uid": profile.uid,
            "iban": profile.bank_iban,
            "bic": profile.bank_bic,
        },
        recipient={
            "name": invoice.recipient_name,
            "address_line1": invoice.recipient_address_line1,
            "address_line2": invoice.recipient_address_line2,
            "uid": invoice.recipient_uid,
        },
        service_period=service_period,
        line_items=invoice.line_items,
        net_total=f"{invoice.net_total:.2f}",
        vat_rate=int(invoice.vat_rate * 100),
        vat_amount=f"{invoice.vat_amount:.2f}",
        gross_total=f"{invoice.gross_total:.2f}",
        payment_terms=invoice.payment_terms,
        brand_color=profile.brand_color,
    )

    return HTML(string=html_str, base_url=str(_ROOT)).write_pdf()


def node_render_pdf(state: DocAssistState) -> dict:
    invoice = InvoiceModel.model_validate(state["invoice"])
    return {"pdf_bytes": render_pdf(invoice, _get_profile())}


def node_persist(state: DocAssistState) -> dict:
    """Write per_node_metadata to SQLite. Failure here must never break the pipeline."""
    t = time.monotonic()
    result = state.get("compliance_result")
    compliance_passed = ComplianceResult.model_validate(result).passed if result else None
    try:
        persist_run(
            request_id=state["request_id"],
            per_node_metadata=state["per_node_metadata"],
            industry_type=_get_profile().industry,
            compliance_passed=compliance_passed,
            error=state.get("error"),
        )
    except Exception:
        pass
    return {"per_node_metadata": [_meta("node_persist", t)]}


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

def route_after_extract(state: DocAssistState) -> str:
    scope = ScopeModel.model_validate(state["scope"])
    return "node_scope_clarify" if scope.confidence == "low" else "node_client_lookup"


def route_after_resolve(state: DocAssistState) -> str:
    rs = ResolvedScope.model_validate(state["resolved_scope"])
    return "node_rate_clarify" if rs.unresolved else "node_generate_quote"


def route_after_quote(state: DocAssistState) -> str:
    return "node_human_review" if state["quote"] is not None else END


def route_after_review(state: DocAssistState) -> str:
    return "node_build_invoice" if state["approval_status"] == "approved" else "node_generate_quote"


# Fields a user can supply to fix a compliance failure — everything else is a
# system-level data error that the LLM correction loop already tried to fix.
_USER_CLARIFIABLE = {"delivery_date", "recipient_uid", "recipient_name_address"}

# Maps a compliance failure field name to one or more user-facing input specs.
_CLARIFY_FIELD_MAP: dict[str, list[dict]] = {
    "delivery_date": [
        {"name": "delivery_date", "input_type": "date"},
    ],
    "recipient_uid": [
        {"name": "recipient_uid", "input_type": "text", "placeholder": "ATU12345678"},
    ],
    "recipient_name_address": [
        {"name": "recipient_name",         "input_type": "text"},
        {"name": "recipient_address_line1", "input_type": "text"},
        {"name": "recipient_address_line2", "input_type": "text"},
    ],
}


def _build_clarify_fields(result: ComplianceResult) -> list[dict]:
    seen: set[str] = set()
    fields: list[dict] = []
    for failure in result.failures:
        for spec in _CLARIFY_FIELD_MAP.get(failure.field, []):
            if spec["name"] not in seen:
                fields.append(spec)
                seen.add(spec["name"])
    return fields


def node_compliance_clarify(state: DocAssistState) -> dict:
    """Suspend graph — ask owner for data the LLM correction loop could not supply."""
    invoice = InvoiceModel.model_validate(state["invoice"])
    result  = ComplianceResult.model_validate(state["compliance_result"])
    fields  = _build_clarify_fields(result)

    answer: dict = interrupt({
        "type": "compliance_clarification",
        "message": (
            "The invoice cannot be completed automatically. "
            "Please provide the missing information:"
        ),
        "fields": fields,
    })

    patch: dict = {}
    for spec in fields:
        val = answer.get(spec["name"])
        if not val:
            continue
        if spec["input_type"] == "date":
            try:
                patch[spec["name"]] = date.fromisoformat(str(val))
            except ValueError:
                pass
        else:
            patch[spec["name"]] = str(val).strip()

    patched = invoice.model_copy(update=patch) if patch else invoice
    return {
        "invoice": patched.model_dump(mode="json"),
        "correction_attempts": 0,   # reset so the LLM loop gets two fresh attempts
        "clarifications_needed": [f"compliance: {[s['name'] for s in fields]}"],
    }


def route_after_compliance(state: DocAssistState) -> str:
    result = ComplianceResult.model_validate(state["compliance_result"])
    if result.passed:
        return "node_render_pdf"
    if state["correction_attempts"] < 2:
        return "node_correct_compliance"
    if any(f.field in _USER_CLARIFIABLE for f in result.failures):
        return "node_compliance_clarify"
    return END  # system-level failure the user cannot resolve


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None) -> StateGraph:
    builder = StateGraph(DocAssistState)

    builder.add_node("node_extract", node_extract)
    builder.add_node("node_scope_clarify", node_scope_clarify)
    builder.add_node("node_client_lookup", node_client_lookup)
    builder.add_node("node_resolve_rates", node_resolve_rates)
    builder.add_node("node_rate_clarify", node_rate_clarify)
    builder.add_node("node_generate_quote", node_generate_quote)
    builder.add_node("node_human_review", node_human_review)
    builder.add_node("node_build_invoice", node_build_invoice)
    builder.add_node("node_check_compliance", node_check_compliance)
    builder.add_node("node_correct_compliance", node_correct_compliance)
    builder.add_node("node_compliance_clarify", node_compliance_clarify)
    builder.add_node("node_render_pdf", node_render_pdf)
    builder.add_node("node_persist", node_persist)

    builder.add_edge(START, "node_extract")

    builder.add_conditional_edges("node_extract", route_after_extract)
    builder.add_edge("node_scope_clarify", "node_extract")

    builder.add_edge("node_client_lookup", "node_resolve_rates")

    builder.add_conditional_edges("node_resolve_rates", route_after_resolve)
    builder.add_edge("node_rate_clarify", "node_generate_quote")

    builder.add_conditional_edges("node_generate_quote", route_after_quote)

    builder.add_conditional_edges("node_human_review", route_after_review)

    builder.add_edge("node_build_invoice", "node_check_compliance")
    builder.add_conditional_edges("node_check_compliance", route_after_compliance)
    builder.add_edge("node_correct_compliance", "node_check_compliance")
    builder.add_edge("node_compliance_clarify", "node_check_compliance")

    builder.add_edge("node_render_pdf", "node_persist")
    builder.add_edge("node_persist", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
