"""Scope extraction agent — parses a job description into a structured ScopeModel."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from agent.models import ScopeModel, ServiceLine

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "scope_extraction"
_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(version: str = "v1") -> str:
    if version not in _PROMPT_CACHE:
        _PROMPT_CACHE[version] = (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")
    return _PROMPT_CACHE[version]


def _build_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://192.168.1.181:1234/v1"),
        api_key=os.environ.get("LOCAL_LLM_API_KEY", "local"),
    )


def _apply_confidence_override(scope: ScopeModel) -> ScopeModel:
    """Deterministic override: force low confidence when key fields are missing."""
    if not scope.client_ref.strip() or not scope.services:
        return scope.model_copy(update={"confidence": "low", "services": []})
    return scope


def _fallback(reason: str) -> ScopeModel:
    """Return a safe low-confidence scope when extraction fails entirely."""
    return ScopeModel(client_ref="", services=[], confidence="low")


def extract_scope(text: str, prompt_version: str = "v1") -> ScopeModel:
    """
    Extract a structured ScopeModel from a plain-text job description.

    Uses the local LLM (OpenAI-compatible API) during development.
    Swap base_url + api_key env vars for Anthropic SDK in production.
    """
    prompt = _load_prompt(prompt_version)
    system_message = prompt
    user_message = text

    try:
        client = _build_client()
        model = os.environ.get("LOCAL_LLM_MODEL", "gemma-4-26b-a4b-it-mlx")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            max_tokens=512,
        )

        raw = response.choices[0].message.content or ""

        # Strip markdown code fences if model wraps output despite instructions
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw) # string -> dict
        scope = ScopeModel.model_validate(data) # dict -> object
        return _apply_confidence_override(scope)

    except (json.JSONDecodeError, ValidationError, KeyError):
        return _fallback("parse_error")
    except Exception:
        return _fallback("llm_error")
