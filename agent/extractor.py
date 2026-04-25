"""Scope extraction agent — parses a job description into a structured ScopeModel."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from agent.models import ScopeModel, ServiceLine

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "scope_extraction"
_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(version: str = "v1") -> str:
    if version not in _PROMPT_CACHE:
        _PROMPT_CACHE[version] = (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")
    return _PROMPT_CACHE[version]


def _parse_raw(raw: str) -> ScopeModel:
    """Strip code fences, parse JSON, validate into ScopeModel."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)
    return ScopeModel.model_validate(data)


def _apply_confidence_override(scope: ScopeModel) -> ScopeModel:
    """Deterministic override: force low confidence when key fields are missing."""
    if not scope.client_ref.strip() or not scope.services:
        return scope.model_copy(update={"confidence": "low", "services": []})
    return scope


def _fallback(reason: str) -> ScopeModel:
    return ScopeModel(client_ref="", services=[], confidence="low")


def _extract_via_openai(system: str, user: str) -> str:
    """Call an OpenAI-compatible local endpoint and return raw text."""
    from openai import OpenAI

    disable_thinking = os.environ.get("LLM_DISABLE_THINKING", "").lower() == "true"

    # Models with extended thinking (e.g. Qwen3) consume tokens reasoning before
    # producing output — 512 is exhausted by the reasoning chain alone.
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "512"))

    kwargs: dict = dict(
        model=os.environ.get("LOCAL_LLM_MODEL", "gemma-4-26b-a4b-it-mlx"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
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
    return response.choices[0].message.content or ""


def _extract_via_anthropic(system: str, user: str) -> str:
    """Call the Anthropic Messages API and return raw text."""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.0,
    )
    return response.content[0].text


def extract_scope(text: str, prompt_version: str = "v3") -> ScopeModel:
    """
    Extract a structured ScopeModel from a plain-text job description.

    Provider is selected via DOCASSIST_PROVIDER env var:
      - "anthropic" → Anthropic Messages API (ANTHROPIC_MODEL, ANTHROPIC_API_KEY)
      - anything else → OpenAI-compatible local endpoint (LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL)
    """
    system = _load_prompt(prompt_version)
    provider = os.environ.get("DOCASSIST_PROVIDER", "local").lower()

    try:
        raw = (
            _extract_via_anthropic(system, text)
            if provider == "anthropic"
            else _extract_via_openai(system, text)
        )
        scope = _parse_raw(raw)
        return _apply_confidence_override(scope)

    except (json.JSONDecodeError, ValidationError, KeyError):
        return _fallback("parse_error")
    except Exception:
        return _fallback("llm_error")
