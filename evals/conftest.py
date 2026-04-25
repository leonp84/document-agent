"""
Pytest fixtures for the DocAssist eval harness.

Loads both manifests once per session and exposes typed subsets
so individual test modules don't repeat IO or filtering logic.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
GOLD_MANIFEST = ROOT / "evals" / "gold" / "ground_truth.json"
TEST_MANIFEST = ROOT / "evals" / "test_invoices" / "manifest.json"
GOLD_JSONL_DIR = ROOT / "evals" / "gold"


# ── Raw manifest loading ─────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def gold_entries() -> list[dict]:
    return json.loads(GOLD_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def test_entries() -> list[dict]:
    return json.loads(TEST_MANIFEST.read_text(encoding="utf-8"))


# ── Filtered subsets ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def gold_valid(gold_entries) -> list[dict]:
    return [e for e in gold_entries if not e["defects"]]


@pytest.fixture(scope="session")
def gold_adversarial(gold_entries) -> list[dict]:
    return [e for e in gold_entries if e["defects"]]


@pytest.fixture(scope="session")
def test_valid(test_entries) -> list[dict]:
    return [e for e in test_entries if not e["defects"]]


@pytest.fixture(scope="session")
def test_adversarial(test_entries) -> list[dict]:
    return [e for e in test_entries if e["defects"]]


# ── Gold JSONL pairs (for extraction evals) ──────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="session")
def gold_pairs() -> list[dict]:
    """All annotated job-description → quote pairs from the three industry JSONL files."""
    pairs = []
    for jsonl_path in sorted(GOLD_JSONL_DIR.glob("*.jsonl")):
        pairs.extend(_load_jsonl(jsonl_path))
    return pairs


@pytest.fixture(scope="session")
def gold_pairs_with_quote(gold_pairs) -> list[dict]:
    """Only pairs where expected_quote is not None (excludes low_confidence entries)."""
    return [p for p in gold_pairs if p["expected_quote"] is not None]


@pytest.fixture(scope="session")
def gold_pairs_low_confidence(gold_pairs) -> list[dict]:
    """Only pairs where expected_quote is None — clarification branch must fire."""
    return [p for p in gold_pairs if p["expected_quote"] is None]


# ── Pre-computed extraction results (one LLM call per pair, cached for session) ──

@pytest.fixture(scope="session")
def extraction_results(gold_pairs) -> dict[str, object]:
    """
    Run extract_scope once per gold pair and cache by pair id.
    All test functions must use this fixture rather than calling
    extract_scope() directly — prevents N_tests × N_pairs LLM calls.
    """
    try:
        from agent.extractor import extract_scope
    except ImportError:
        return {}

    print(f"\nRunning extraction on {len(gold_pairs)} gold pairs...")
    results = {}
    for pair in gold_pairs:
        results[pair["id"]] = extract_scope(pair["input"])
        print(f"  {pair['id']}: done")
    return results
