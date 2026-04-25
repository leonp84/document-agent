"""Scope extraction eval tests — Phase 3: real extractor wired in."""
import pytest

from agent.extractor import extract_scope
from agent.models import ScopeModel, ServiceLine


# ── Metric helpers ────────────────────────────────────────────────────────────

def _client_ref_match(predicted: str, expected: str) -> bool:
    """Case-insensitive substring match — handles partial client names."""
    return expected.lower() in predicted.lower() or predicted.lower() in expected.lower()


def _service_description_f1(predicted: list[ServiceLine], expected: list[dict]) -> float:
    """Token-level F1 on service description words across all line items."""
    pred_tokens = set(w.lower() for s in predicted for w in s.description.split())
    gold_tokens = set(w.lower() for s in expected for w in s["description"].split())
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


# ── Shape / smoke tests (no LLM calls — use cached results) ──────────────────

class TestExtractionShape:
    """Verifies the extractor returns a valid ScopeModel shape on every gold pair."""

    def test_returns_scope_model_for_all_pairs(self, gold_pairs, extraction_results):
        for pair in gold_pairs:
            result = extraction_results.get(pair["id"])
            assert result is not None, f"No result cached for {pair['id']}"
            assert isinstance(result, ScopeModel), f"Bad return type for {pair['id']}"
            assert result.confidence in ("high", "low")
            assert result.vat_rate > 0
            assert result.language in ("de", "en")

    def test_no_extraction_raises(self, gold_pairs, extraction_results):
        """All pairs must have a cached result — none should have crashed."""
        missing = [p["id"] for p in gold_pairs if p["id"] not in extraction_results]
        assert not missing, f"Extraction crashed for: {missing}"


# ── LLM-quality tests (use cached results — zero extra LLM calls) ─────────────

class TestClientExtraction:

    def test_client_ref_extracted_high_confidence(self, gold_pairs_with_quote, extraction_results):
        failures = []
        for pair in gold_pairs_with_quote:
            result = extraction_results.get(pair["id"])
            expected_ref = pair["expected_scope"]["client_ref"]
            if result and not _client_ref_match(result.client_ref, expected_ref):
                failures.append((pair["id"], expected_ref, result.client_ref))

        if failures:
            pytest.fail(
                f"{len(failures)} client refs not matched:\n"
                + "\n".join(f"  {id_}: got={got!r}, want={want!r}" for id_, want, got in failures)
            )

    def test_client_ref_accuracy_above_threshold(self, gold_pairs_with_quote, extraction_results):
        total = len(gold_pairs_with_quote)
        matched = sum(
            1 for pair in gold_pairs_with_quote
            if extraction_results.get(pair["id"])
            and _client_ref_match(
                extraction_results[pair["id"]].client_ref,
                pair["expected_scope"]["client_ref"],
            )
        )
        accuracy = matched / total if total else 0.0
        assert accuracy >= 0.85, f"Client ref accuracy {accuracy:.0%} < 85% threshold"


class TestServiceExtraction:

    def test_service_count_matches(self, gold_pairs_with_quote, extraction_results):
        mismatches = []
        for pair in gold_pairs_with_quote:
            result = extraction_results.get(pair["id"])
            expected_count = len(pair["expected_scope"]["services"])
            if result and len(result.services) != expected_count:
                mismatches.append((pair["id"], expected_count, len(result.services)))

        if mismatches:
            detail = "\n".join(f"  {id_}: expected={exp}, got={got}" for id_, exp, got in mismatches)
            pytest.fail(f"{len(mismatches)} service count mismatches:\n{detail}")

    def test_service_description_f1_above_threshold(self, gold_pairs_with_quote, extraction_results):
        scores = []
        for pair in gold_pairs_with_quote:
            result = extraction_results.get(pair["id"])
            if result:
                f1 = _service_description_f1(result.services, pair["expected_scope"]["services"])
                scores.append(f1)

        mean_f1 = sum(scores) / len(scores) if scores else 0.0
        assert mean_f1 >= 0.85, f"Mean service description F1 {mean_f1:.2f} < 0.85"

    def test_explicit_rates_extracted_correctly(self, gold_pairs_with_quote, extraction_results):
        rates_explicit = [p for p in gold_pairs_with_quote if p["scenario"] == "rates_explicit"]
        accuracies = []
        for pair in rates_explicit:
            result = extraction_results.get(pair["id"])
            if result:
                acc = _rate_accuracy(result.services, pair["expected_scope"]["services"])
                accuracies.append(acc)

        mean_acc = sum(accuracies) / len(accuracies) if accuracies else 0.0
        assert mean_acc >= 0.90, f"Explicit rate accuracy {mean_acc:.0%} < 90%"

    def test_missing_rates_left_null(self, gold_pairs_with_quote, extraction_results):
        rates_missing = [p for p in gold_pairs_with_quote if p["scenario"] == "rates_missing"]
        hallucinated = []
        for pair in rates_missing:
            result = extraction_results.get(pair["id"])
            if result:
                for svc in result.services:
                    if svc.rate is not None:
                        hallucinated.append(pair["id"])
                        break

        assert not hallucinated, f"Rates hallucinated for: {hallucinated}"


class TestClarificationBranch:

    def test_low_confidence_pairs_trigger_clarification(self, gold_pairs_low_confidence, extraction_results):
        assert gold_pairs_low_confidence, "No low_confidence pairs found in gold set"
        wrong = [
            pair["id"] for pair in gold_pairs_low_confidence
            if extraction_results.get(pair["id"]) and extraction_results[pair["id"]].confidence != "low"
        ]
        assert not wrong, f"Vague inputs not flagged low-confidence: {wrong}"

    def test_low_confidence_services_empty(self, gold_pairs_low_confidence, extraction_results):
        hallucinated = [
            pair["id"] for pair in gold_pairs_low_confidence
            if extraction_results.get(pair["id"])
            and extraction_results[pair["id"]].confidence == "low"
            and extraction_results[pair["id"]].services
        ]
        assert not hallucinated, f"Services hallucinated on low-confidence input: {hallucinated}"


class TestLanguageDetection:

    def test_english_inputs_detected(self, gold_pairs_with_quote, extraction_results):
        english_pairs = [p for p in gold_pairs_with_quote if p["scenario"] == "english_input"]
        assert english_pairs, "No english_input scenarios found in gold set"
        wrong = [
            p["id"] for p in english_pairs
            if extraction_results.get(p["id"]) and extraction_results[p["id"]].language != "en"
        ]
        assert not wrong, f"EN not detected for: {wrong}"

    def test_german_inputs_detected(self, gold_pairs_with_quote, extraction_results):
        german_pairs = [p for p in gold_pairs_with_quote if p["expected_scope"]["language"] == "de"]
        wrong = [
            p["id"] for p in german_pairs
            if extraction_results.get(p["id"]) and extraction_results[p["id"]].language != "de"
        ]
        assert not wrong, f"DE not detected for: {wrong}"
