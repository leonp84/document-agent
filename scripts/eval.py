"""
DocAssist eval scorecard runner.

Runs the pytest eval suite and writes a JSON scorecard to evals/scorecard.json.

Usage
-----
    python scripts/eval.py                  # run all evals
    python scripts/eval.py --pretty         # pretty-print scorecard to stdout
    python scripts/eval.py --suite compliance  # run only compliance tests
    python scripts/eval.py --suite extraction  # run only extraction tests
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVALS_DIR = ROOT / "evals"
SCORECARD_PATH = EVALS_DIR / "scorecard.json"

SUITES = {
    "compliance": EVALS_DIR / "test_compliance.py",
    "extraction": EVALS_DIR / "test_extraction.py",
    "all": EVALS_DIR,
}


def run_pytest(target: Path) -> dict:
    """Run pytest in quiet mode and parse the summary line."""
    cmd = [sys.executable, "-m", "pytest", str(target), "--tb=no", "-q"]

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    elapsed = round(time.monotonic() - start, 2)

    report = _parse_stdout(result.stdout)
    report["wall_seconds"] = elapsed
    report["returncode"] = result.returncode
    return report


def _parse_stdout(stdout: str) -> dict:
    """
    Parse pytest -q summary line, e.g.:
        '20 passed, 8 xfailed in 0.18s'
        '1 failed, 19 passed, 8 xfailed in 0.20s'
    """
    passed = failed = xfailed = errors = 0
    for line in stdout.splitlines():
        line = line.strip()
        # Summary line always contains "in X.XXs"
        if "in " not in line:
            continue
        for segment in line.split(","):
            segment = segment.strip().split(" in ")[0].strip()
            if not segment:
                continue
            parts = segment.split()
            if len(parts) < 2:
                continue
            try:
                count = int(parts[0])
            except ValueError:
                continue
            label = parts[1]
            if label == "passed":
                passed = count
            elif label == "failed":
                failed = count
            elif label == "xfailed":
                xfailed = count
            elif label in ("error", "errors"):
                errors = count
    return {
        "summary": {
            "passed": passed,
            "failed": failed,
            "xfailed": xfailed,
            "errors": errors,
            "total": passed + failed + xfailed + errors,
        }
    }


def build_scorecard(suite: str, report: dict) -> dict:
    summary = report.get("summary", {})
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    xfailed = summary.get("xfailed", 0)
    errors = summary.get("errors", 0)
    total = summary.get("total", passed + failed + xfailed + errors)

    # Active tests are those not xfailed (stubs don't count toward pass rate)
    active = passed + failed + errors
    pass_rate = round(passed / active, 4) if active else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "wall_seconds": report.get("wall_seconds", 0),
        "counts": {
            "passed": passed,
            "failed": failed,
            "xfailed": xfailed,
            "errors": errors,
            "total": total,
            "active": active,
        },
        "pass_rate": pass_rate,
        "status": "green" if failed == 0 and errors == 0 else "red",
        "note": (
            f"{xfailed} test(s) xfailed - stub placeholders awaiting real implementation."
            if xfailed else ""
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="DocAssist eval scorecard runner")
    parser.add_argument(
        "--suite",
        choices=list(SUITES.keys()),
        default="all",
        help="Which test suite to run (default: all)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the scorecard to stdout after writing",
    )
    args = parser.parse_args()

    target = SUITES[args.suite]
    print(f"Running suite: {args.suite} ({target.relative_to(ROOT)})")

    report = run_pytest(target)
    scorecard = build_scorecard(args.suite, report)

    SCORECARD_PATH.write_text(
        json.dumps(scorecard, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if args.pretty or True:  # always print summary
        counts = scorecard["counts"]
        status_icon = "PASS" if scorecard["status"] == "green" else "FAIL"
        print(
            f"\n{status_icon} {counts['passed']} passed  "
            f"{counts['failed']} failed  "
            f"{counts['xfailed']} xfailed  "
            f"({scorecard['wall_seconds']}s)"
        )
        print(f"  pass rate (active tests): {scorecard['pass_rate']:.0%}")
        if scorecard["note"]:
            print(f"  {scorecard['note']}")
        print(f"\nScorecard written to {SCORECARD_PATH.relative_to(ROOT)}")

    sys.exit(0 if scorecard["status"] == "green" else 1)


if __name__ == "__main__":
    main()
