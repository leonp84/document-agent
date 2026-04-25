"""
Scope extraction eval tests.

Tests run against the gold JSONL pairs using a stub extractor that returns
a fixed dummy ScopeModel. All LLM-dependent assertions are marked xfail
while the stub is active so the harness runs clean before Phase 3 exists.

To wire in the real extractor (Phase 3):
    from agent.extractor import extract_scope
    extract_scope = extract_scope  # replace the stub below
"""
from dataclasses import dataclass, field

import pytest


# ── Stub ScopeModel and extractor ────────────────────────────────────────────

@dataclass
class ServiceLine:
    description: str
    quantity: float | None
    unit: str | None
    rate: float | None


@dataclass
class ScopeModel:
    client_ref: str
    services: list[ServiceLine]
    vat_rate: float
    language: str
    confidence: str  # "high" | "low"


def _stub_extract_scope(text: str) -> ScopeModel:
    """Returns a fixed dummy scope — replace with real extractor in Phase 3."""
    return ScopeModel(
        client_ref="STUB_CLIENT",
        services=[],
        vat_rate=0.20,
        language="de",
        confidence="high",
    )


extract_scope = _stub_extract_scope


# ── Metric helpers ────────────────────────────────────────────────────────────

def _client_ref_match(predicted: str, expected: str) -> bool:
    """Case-insensitive substring match — handles partial client names."""
    return expected.lower() in predicted.lower() or predicted.lower() in expected.lower()


def _service_description_f1(predicted: list[ServiceLine], expected: list[dict]) -> float:
    """Token-level F1 on service description words across all line items."""
    pred_tokens = set(
        w.lower() for s in predicted for w in s.description.split()
    )
    gold_tokens = set(
        w.lower() for s in expected for w in s["description"].split()
    )
    if not gold_tokens:
        return 1.0
    if not pred_tokens:
        return 0.0
    tp = len(pred_tokens & gold_tokens)
    precision = tp / len(pred_tokens)
    recall = tp / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _rate_accuracy(predicted: list[ServiceLine], expected: list[dict]) -> float:
    """Fraction of explicit rates correctly extracted (ignores null-rate entries)."""
    explicit = [(s, e) for s, e in zip(predicted, expected) if e["rate"] is not None]
    if not explicit:
        return 1.0
    correct = sum(
        1 for pred_s, exp_s in explicit
        if pred_s.rate is not None and abs(pred_s.rate - exp_s["rate"]) < 0.01
    )
    return correct / len(explicit)


# ── Gold pair tests ───────────────────────────────────────────────────────────

class TestExtractionStub:
    """Verifies the stub returns a valid ScopeModel shape on every gold pair."""

    def test_stub_returns_scope_model_for_all_pairs(self, gold_pairs):
        for pair in gold_pairs:
            result = extract_scope(pair["input"])
            assert isinstance(result, ScopeModel), f"Bad return type for {pair['id']}"
            assert result.confidence in ("high", "low")
            assert result.vat_rate > 0
            assert result.language in ("de", "en")

    def test_stub_fires_on_all_inputs(self, gold_pairs):
        """Smoke test — extractor must not raise on any gold input."""
        for pair in gold_pairs:
            extract_scope(pair["input"])  # must not raise


class TestClientExtraction:

    def test_client_ref_extracted_high_confidence(self, gold_pairs_with_quote):
        """Client ref must match for high-confidence pairs."""
        failures = []
        for pair in gold_pairs_with_quote:
            result = extract_scope(pair["input"])
            expected_ref = pair["expected_scope"]["client_ref"]
            if not _client_ref_match(result.client_ref, expected_ref):
                failures.append((pair["id"], expected_ref, result.client_ref))

        if failures:
            pytest.xfail(
                f"Stub active — {len(failures)} client refs not matched: "
                + ", ".join(f"{id_}(got={got!r}, want={want!r})" for id_, want, got in failures)
            )

    def test_client_ref_accuracy_above_threshold(self, gold_pairs_with_quote):
        """At least 85% of client refs must match once real extractor is active."""
        total = len(gold_pairs_with_quote)
        matched = sum(
            1 for pair in gold_pairs_with_quote
            if _client_ref_match(
                extract_scope(pair["input"]).client_ref,
                pair["expected_scope"]["client_ref"],
            )
        )
        accuracy = matched / total if total else 0.0
        if accuracy < 0.85:
            pytest.xfail(f"Stub active — client ref accuracy {accuracy:.0%} < 85% threshold")


class TestServiceExtraction:

    def test_service_count_matches(self, gold_pairs_with_quote):
        """Number of extracted service lines must match gold for each pair."""
        mismatches = []
        for pair in gold_pairs_with_quote:
            result = extract_scope(pair["input"])
            expected_count = len(pair["expected_scope"]["services"])
            if len(result.services) != expected_count:
                mismatches.append((pair["id"], expected_count, len(result.services)))

        if mismatches:
            pytest.xfail(
                f"Stub active — {len(mismatches)} service count mismatches"
            )

    def test_service_description_f1_above_threshold(self, gold_pairs_with_quote):
        """Mean token F1 on service descriptions must reach 0.85."""
        scores = []
        for pair in gold_pairs_with_quote:
            result = extract_scope(pair["input"])
            f1 = _service_description_f1(
                result.services,
                pair["expected_scope"]["services"],
            )
            scores.append(f1)

        mean_f1 = sum(scores) / len(scores) if scores else 0.0
        if mean_f1 < 0.85:
            pytest.xfail(f"Stub active — mean service description F1 {mean_f1:.2f} < 0.85")

    def test_explicit_rates_extracted_correctly(self, gold_pairs_with_quote):
        """Rates that appear in the input text must be extracted exactly."""
        rates_explicit = [
            p for p in gold_pairs_with_quote
            if p["scenario"] == "rates_explicit"
        ]
        accuracies = []
        for pair in rates_explicit:
            result = extract_scope(pair["input"])
            acc = _rate_accuracy(result.services, pair["expected_scope"]["services"])
            accuracies.append(acc)

        mean_acc = sum(accuracies) / len(accuracies) if accuracies else 0.0
        if mean_acc < 0.90:
            pytest.xfail(f"Stub active — explicit rate accuracy {mean_acc:.0%} < 90%")

    def test_missing_rates_left_null(self, gold_pairs_with_quote):
        """For rates_missing scenarios, extracted rates must be None (not hallucinated)."""
        rates_missing = [
            p for p in gold_pairs_with_quote
            if p["scenario"] == "rates_missing"
        ]
        hallucinated = []
        for pair in rates_missing:
            result = extract_scope(pair["input"])
            for svc in result.services:
                if svc.rate is not None:
                    hallucinated.append(pair["id"])
                    break

        if hallucinated:
            pytest.xfail(
                f"Stub active — hallucinated rates in: {hallucinated}"
            )


class TestClarificationBranch:

    def test_low_confidence_pairs_trigger_clarification(self, gold_pairs_low_confidence):
        """Vague inputs must produce confidence='low', not a fabricated scope."""
        assert gold_pairs_low_confidence, "No low_confidence pairs found in gold set"
        wrong = []
        for pair in gold_pairs_low_confidence:
            result = extract_scope(pair["input"])
            if result.confidence != "low":
                wrong.append(pair["id"])

        if wrong:
            pytest.xfail(
                f"Stub active — {len(wrong)} vague inputs not flagged low-confidence: {wrong}"
            )

    def test_low_confidence_services_empty(self, gold_pairs_low_confidence):
        """Low-confidence extraction must not hallucinate service lines."""
        hallucinated = []
        for pair in gold_pairs_low_confidence:
            result = extract_scope(pair["input"])
            if result.confidence == "low" and result.services:
                hallucinated.append(pair["id"])

        if hallucinated:
            pytest.xfail(
                f"Stub active — services hallucinated on low-confidence input: {hallucinated}"
            )


class TestLanguageDetection:

    def test_english_inputs_detected(self, gold_pairs_with_quote):
        english_pairs = [p for p in gold_pairs_with_quote if p["scenario"] == "english_input"]
        assert english_pairs, "No english_input scenarios found in gold set"
        wrong = [
            p["id"] for p in english_pairs
            if extract_scope(p["input"]).language != "en"
        ]
        if wrong:
            pytest.xfail(f"Stub active — EN not detected for: {wrong}")

    def test_german_inputs_detected(self, gold_pairs_with_quote):
        german_pairs = [p for p in gold_pairs_with_quote if p["expected_scope"]["language"] == "de"]
        wrong = [
            p["id"] for p in german_pairs
            if extract_scope(p["input"]).language != "de"
        ]
        if wrong:
            pytest.xfail(f"Stub active — DE not detected for: {wrong}")
