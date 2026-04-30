"""Quote generation agent — formats resolved scope into a QuoteModel."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from agent.models import QuoteLineItem, QuoteModel, ResolvedScope

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "quote_generation"
_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(version: str = "v1") -> str:
    if version not in _PROMPT_CACHE:
        _PROMPT_CACHE[version] = (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")
    return _PROMPT_CACHE[version]


def _build_user_message(scope: ResolvedScope, rejection_feedback: str | None = None) -> str:
    lines = "\n".join(
        f"{i + 1}. {svc.description}" for i, svc in enumerate(scope.resolved)
    )
    msg = f"Input language: {scope.language}\nDescriptions:\n{lines}"
    if rejection_feedback:
        msg += f"\n\nPrevious quote was rejected. Owner feedback: {rejection_feedback}"
    return msg


def _parse_llm_response(raw: str, expected_count: int) -> tuple[list[str], str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)
    descriptions = data["line_descriptions"]
    if len(descriptions) != expected_count:
        raise ValueError(f"expected {expected_count} descriptions, got {len(descriptions)}")
    return descriptions, data["payment_terms"]


def _call_openai(system: str, user: str) -> tuple[str, int | None, int | None]:
    from openai import OpenAI

    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "512"))
    disable_thinking = os.environ.get("LLM_DISABLE_THINKING", "").lower() == "true"
    kwargs: dict = dict(
        model=os.environ.get("LOCAL_LLM_MODEL", "gemma-4-26b-a4b-it-mlx"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    if disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    client = OpenAI(
        base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://192.168.1.181:1234/v1"),
        api_key=os.environ.get("LOCAL_LLM_API_KEY", "local"),
    )
    response = client.chat.completions.create(**kwargs)
    usage = response.usage
    return (
        response.choices[0].message.content or "",
        usage.prompt_tokens if usage else None,
        usage.completion_tokens if usage else None,
    )


def _call_anthropic(system: str, user: str) -> tuple[str, int | None, int | None]:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.0,
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


def _assemble_quote(
    scope: ResolvedScope,
    descriptions: list[str],
    payment_terms: str,
) -> QuoteModel:
    line_items = []
    for svc, desc in zip(scope.resolved, descriptions):
        qty = svc.quantity if svc.quantity is not None else 1.0
        amount = round(qty * svc.rate, 2)
        line_items.append(QuoteLineItem(
            description=desc,
            qty=qty,
            unit=svc.unit or "pauschal",
            rate=svc.rate,
            amount=amount,
        ))
    net_total = round(sum(item.amount for item in line_items), 2)
    vat_amount = round(net_total * scope.vat_rate, 2)
    gross_total = round(net_total + vat_amount, 2)
    return QuoteModel(
        client=scope.client,
        client_ref=scope.client_ref,
        line_items=line_items,
        net_total=net_total,
        vat_rate=scope.vat_rate,
        vat_amount=vat_amount,
        gross_total=gross_total,
        payment_terms=payment_terms,
        language=scope.language,
    )


def generate_quote(
    scope: ResolvedScope,
    prompt_version: str = "v1",
    rejection_feedback: str | None = None,
) -> tuple[QuoteModel | None, int | None, int | None]:
    """
    Generate a QuoteModel from a fully resolved scope.

    Returns (QuoteModel | None, in_tokens, out_tokens). Tokens are None on error.
    Returns (None, None, None) if any service lines remain unresolved (missing rates).
    Provider selected via DOCASSIST_PROVIDER env var.
    rejection_feedback is appended to the user message when re-generating after owner rejection.
    """
    if scope.unresolved:
        return None, None, None

    system = _load_prompt(prompt_version)
    user = _build_user_message(scope, rejection_feedback)
    provider = os.environ.get("DOCASSIST_PROVIDER", "local").lower()

    try:
        raw, in_tok, out_tok = (
            _call_anthropic(system, user)
            if provider == "anthropic"
            else _call_openai(system, user)
        )
        descriptions, payment_terms = _parse_llm_response(raw, len(scope.resolved))
        return _assemble_quote(scope, descriptions, payment_terms), in_tok, out_tok
    except (json.JSONDecodeError, ValidationError, KeyError, ValueError):
        return None, None, None
    except Exception:
        return None, None, None
