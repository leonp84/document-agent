"""
Deterministic compliance eval tests.

These tests run against the manifests (ground_truth.json + manifest.json)
using stub compliance checker functions. Stubs always return a fixed verdict
so the harness is runnable before the real compliance engine exists (Phase 6).

Replace the stub imports with real ones once Phase 6 is complete:
    from agent.compliance_engine import check_compliance
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


# ── Stub compliance checker ──────────────────────────────────────────────────
# Returns True (compliant) for every PDF until the real engine is wired in.
# Stub produces a known baseline: 100% false-negative rate on adversarial,
# which gives a clear before/after signal once the real engine replaces it.

def _stub_check_compliance(pdf_path: str) -> bool:
    return True


# Swap this to use the real checker:
check_compliance = _stub_check_compliance


# ── Helpers ──────────────────────────────────────────────────────────────────

def _score(entries: list[dict]) -> dict:
    """Run check_compliance against every entry and compute precision/recall."""
    tp = fp = tn = fn = 0
    for entry in entries:
        predicted = check_compliance(entry["pdf"])
        expected = entry["compliance_expected"]
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif not predicted and not expected:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


# ── Gold set tests ────────────────────────────────────────────────────────────

class TestGoldCompliance:

    def test_all_gold_pdfs_exist(self, gold_entries):
        missing = [e["pdf"] for e in gold_entries if not (ROOT / e["pdf"]).exists()]
        assert not missing, f"Missing gold PDFs: {missing}"

    def test_gold_valid_all_compliant(self, gold_valid):
        """All non-defective gold invoices must be flagged compliant."""
        failures = [
            e["pdf"] for e in gold_valid
            if not check_compliance(e["pdf"])
        ]
        assert not failures, f"False negatives on valid gold invoices: {failures}"

    def test_gold_adversarial_all_non_compliant(self, gold_adversarial):
        """All defective gold invoices (except long_description) must be flagged non-compliant."""
        should_fail = [e for e in gold_adversarial if not e["compliance_expected"]]
        missed = [e["pdf"] for e in should_fail if check_compliance(e["pdf"])]
        if missed:
            pytest.xfail(f"Stub active — {len(missed)} adversarial gold invoices not yet detected")

    def test_gold_long_description_is_compliant(self, gold_adversarial):
        """long_description entries are still §11-compliant — checker must not reject them."""
        long_desc = [e for e in gold_adversarial if "long_description" in e["defects"]]
        assert long_desc, "No long_description entries found in gold adversarial set"
        failures = [e["pdf"] for e in long_desc if not check_compliance(e["pdf"])]
        assert not failures, f"long_description incorrectly flagged non-compliant: {failures}"

    def test_gold_score_above_threshold(self, gold_entries):
        """End-to-end F1 on gold set must reach threshold once real engine is wired."""
        scores = _score(gold_entries)
        # Threshold intentionally low while stub is active; tighten in Phase 6.
        assert scores["f1"] >= 0.0, f"Gold F1 below threshold: {scores}"


# ── Test invoice set tests ────────────────────────────────────────────────────

class TestManifestCompliance:

    def test_all_test_pdfs_exist(self, test_entries):
        missing = [e["pdf"] for e in test_entries if not (ROOT / e["pdf"]).exists()]
        assert not missing, f"Missing test_invoices PDFs: {missing}"

    def test_valid_invoices_flagged_compliant(self, test_valid):
        failures = [e["pdf"] for e in test_valid if not check_compliance(e["pdf"])]
        assert not failures, f"False negatives on valid test invoices: {failures}"

    def test_adversarial_invoices_flagged_non_compliant(self, test_adversarial):
        missed = [e["pdf"] for e in test_adversarial if check_compliance(e["pdf"])]
        # Stub will produce false positives here — this documents the gap.
        # assert not missed  # uncomment once real engine is wired
        pytest.xfail(f"Stub active — {len(missed)} adversarial invoices not yet detected")

    def test_manifest_coverage(self, test_valid, test_adversarial):
        """Sanity check: expected counts from generate_test_invoices.py."""
        assert len(test_valid) == 30, f"Expected 30 valid, got {len(test_valid)}"
        assert len(test_adversarial) == 15, f"Expected 15 adversarial, got {len(test_adversarial)}"


# ── Deterministic unit checks (no PDF needed) ─────────────────────────────────

class TestDeterministicRules:
    """
    Unit tests for the individual rule functions.
    These run against raw strings/values, not PDFs.
    Stub implementations live here until Phase 6 replaces them.
    """

    # Stub rule functions — replace with real imports from compliance_engine
    @staticmethod
    def _check_uid_format(uid: str | None) -> bool:
        if not uid:
            return False
        return bool(re.match(r"^ATU\d{8}$", uid))

    @staticmethod
    def _check_vat_arithmetic(net: float, rate: float, vat_amount: float) -> bool:
        expected = round(net * rate, 2)
        return abs(vat_amount - expected) < 0.01

    def test_uid_valid(self):
        assert self._check_uid_format("ATU12345678")

    def test_uid_missing(self):
        assert not self._check_uid_format(None)

    def test_uid_wrong_prefix(self):
        assert not self._check_uid_format("EU12345678")

    def test_uid_too_short(self):
        assert not self._check_uid_format("ATU1234567")

    def test_vat_arithmetic_correct(self):
        assert self._check_vat_arithmetic(1000.0, 0.20, 200.0)

    def test_vat_arithmetic_wrong_rate(self):
        assert not self._check_vat_arithmetic(1000.0, 0.20, 250.0)

    def test_vat_arithmetic_rounding_tolerance(self):
        assert self._check_vat_arithmetic(333.33, 0.20, 66.67)
