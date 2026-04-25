"""
Generate PDFs from the gold eval set and write ground_truth.json.

Outputs
-------
evals/gold/pdfs/<industry>/<id>.pdf        — one per non-null expected_quote entry
evals/gold/pdfs/adversarial/<id>_<defect>.pdf — 3 defect types × 3 industries
evals/gold/ground_truth.json               — manifest with compliance expectations
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pdf_utils import fmt_eur, fmt_qty, german_date, render_pdf

ROOT = Path(__file__).parent.parent
GOLD_DIR = ROOT / "evals" / "gold"
PDF_BASE = GOLD_DIR / "pdfs"
DATA_DIR = ROOT / "data"

# Business profile used for all gold PDFs (kept separate from real config)
GOLD_PROFILE = {
    "name": "DocAssist Musterfirma GmbH",
    "address_line1": "Teststraße 1",
    "address_line2": "1010 Wien",
    "uid": "ATU99999999",
    "iban": "AT61 1904 3002 3457 3201",
    "bic": "OPSKATWW",
    "brand_color": "#2C6E49",
}

INDUSTRY_DIRS = {"Reinigung": "reinigung", "Handwerk": "handwerk", "Beratung": "beratung"}


def load_clients() -> dict[str, dict]:
    data = json.loads((DATA_DIR / "clients.json").read_text(encoding="utf-8"))
    index = {}
    for c in data:
        index[c["name"]] = c
        for short in c.get("short_names", []):
            index[short] = c
    return index


def build_context(entry: dict, clients: dict, today: date) -> dict:
    eq = entry["expected_quote"]
    client_ref = entry["expected_scope"]["client_ref"]
    client = clients.get(client_ref, {
        "name": client_ref,
        "address_line1": "Adresse nicht verfügbar",
        "address_line2": "0000 Ort",
        "uid": None,
    })
    line_items = [
        {
            "description": li["description"],
            "qty": fmt_qty(li["qty"]),
            "unit": li["unit"],
            "rate": fmt_eur(li["rate"]),
            "amount": fmt_eur(li["amount"]),
        }
        for li in eq["line_items"]
    ]
    invoice_num = f"GOLD-{entry['id'].upper()}-{today.year}"
    return {
        "doc_type": "Rechnung",
        "invoice_number": invoice_num,
        "invoice_date": german_date(today),
        "service_period": f"{today.strftime('%B')} {today.year}",
        "supplier": GOLD_PROFILE,
        "recipient": {
            "name": client.get("name", client_ref),
            "address_line1": client.get("address_line1", ""),
            "address_line2": client.get("address_line2", ""),
            "uid": client.get("uid"),
        },
        "line_items": line_items,
        "net_total": fmt_eur(eq["net_total"]),
        "vat_rate": str(int(round(entry["expected_scope"]["vat_rate"] * 100))),
        "vat_amount": fmt_eur(eq["vat_amount"]),
        "gross_total": fmt_eur(eq["gross_total"]),
        "payment_terms": eq["payment_terms"],
        "brand_color": GOLD_PROFILE["brand_color"],
    }


DEFECT_VIOLATED_FIELDS = {
    "missing_uid": "§11/1 Z3 — Supplier UID missing",
    "malformed_vat": "§11/1 Z11 — VAT amount does not match net × rate",
    "long_description": None,  # compliant — long descriptions are not a §11 violation
}


def apply_defect(ctx: dict, defect: str) -> dict:
    """Return a copy of ctx with the named defect applied."""
    import copy
    c = copy.deepcopy(ctx)
    if defect == "missing_uid":
        c["supplier"]["uid"] = None
    elif defect == "malformed_vat":
        # Arithmetic error: VAT amount does not match net × rate
        c["vat_amount"] = fmt_eur(float(c["net_total"].replace(".", "").replace(",", ".")) * 0.25)
    elif defect == "long_description":
        if c["line_items"]:
            c["line_items"][0]["description"] = (
                c["line_items"][0]["description"]
                + " — " + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 4).strip()
            )
    return c


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    clients = load_clients()
    today = date.today()
    ground_truth = []

    for jsonl_path in sorted(GOLD_DIR.glob("*.jsonl")):
        entries = load_jsonl(jsonl_path)
        industry_slug = INDUSTRY_DIRS.get(entries[0]["industry"], jsonl_path.stem)
        out_dir = PDF_BASE / industry_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        adv_dir = PDF_BASE / "adversarial"
        adv_dir.mkdir(parents=True, exist_ok=True)

        first_valid_ctx = None  # used for adversarial variants

        for entry in entries:
            if entry["expected_quote"] is None:
                continue

            ctx = build_context(entry, clients, today)
            pdf_path = out_dir / f"{entry['id']}.pdf"
            print(f"  rendering {pdf_path.relative_to(ROOT)} ...", end=" ", flush=True)
            pdf_path.write_bytes(render_pdf(ctx))
            print("ok")

            ground_truth.append({
                "pdf": str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
                "id": entry["id"],
                "industry": entry["industry"],
                "scenario": entry["scenario"],
                "compliance_expected": True,
                "defects": [],
            })

            if first_valid_ctx is None:
                first_valid_ctx = ctx

        # One adversarial variant per defect type per industry
        if first_valid_ctx:
            for defect in ("missing_uid", "malformed_vat", "long_description"):
                bad_ctx = apply_defect(first_valid_ctx, defect)
                pdf_path = adv_dir / f"{industry_slug}_{defect}.pdf"
                print(f"  rendering {pdf_path.relative_to(ROOT)} [{defect}] ...", end=" ", flush=True)
                pdf_path.write_bytes(render_pdf(bad_ctx))
                print("ok")
                entry = {
                    "pdf": str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
                    "id": f"{industry_slug}_{defect}",
                    "industry": entries[0]["industry"],
                    "scenario": "adversarial",
                    "compliance_expected": defect == "long_description",  # only long_description remains compliant
                    "defects": [defect],
                }
                if DEFECT_VIOLATED_FIELDS[defect]:
                    entry["violated_field"] = DEFECT_VIOLATED_FIELDS[defect]
                ground_truth.append(entry)

    out_path = GOLD_DIR / "ground_truth.json"
    out_path.write_text(json.dumps(ground_truth, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(ground_truth)} entries to {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
