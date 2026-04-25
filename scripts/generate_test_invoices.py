"""
Generate a large synthetic invoice set for the eval harness.

Outputs
-------
evals/test_invoices/<industry>/valid_<n>.pdf   — 10 per industry (30 total)
evals/test_invoices/adversarial/<defect>_<industry>_<n>.pdf — 5 defect types × 3 industries (15)
evals/test_invoices/manifest.json              — expected compliance result per PDF
"""
import copy
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pdf_utils import fmt_eur, fmt_qty, german_date, render_pdf

random.seed(42)

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "evals" / "test_invoices"
DATA_DIR = ROOT / "data"

# ── Business profiles (one per industry) ────────────────────────────────────

PROFILES = {
    "Reinigung": {
        "name": "Glanz & Sauber Reinigung GmbH",
        "address_line1": "Hauptstraße 12",
        "address_line2": "4020 Linz",
        "uid": "ATU11111111",
        "iban": "AT61 1904 3002 3457 3201",
        "bic": "OPSKATWW",
        "brand_color": "#2C6E49",
    },
    "Handwerk": {
        "name": "Tischlerei Muster GmbH",
        "address_line1": "Werkstattgasse 8",
        "address_line2": "1140 Wien",
        "uid": "ATU22222222",
        "iban": "AT83 2011 1400 0123 4567",
        "bic": "GIBAATWWXXX",
        "brand_color": "#8B4513",
    },
    "Beratung": {
        "name": "Muster Consulting GmbH",
        "address_line1": "Am Opernring 3",
        "address_line2": "8010 Graz",
        "uid": "ATU33333333",
        "iban": "AT38 2011 1800 8001 2345",
        "bic": "STSPAT2GXXX",
        "brand_color": "#1A3A5C",
    },
}

# ── Service pools (description, unit, qty_choices, rate_range) ───────────────

SERVICE_POOLS = {
    "Reinigung": [
        ("Büroreinigung",       "Stunden", list(range(4, 21)),    (18, 28)),
        ("Fensterreinigung",    "Stunden", list(range(2, 9)),     (28, 42)),
        ("Sanitärreinigung",    "Stunden", list(range(2, 7)),     (22, 32)),
        ("Grundreinigung",      "Stunden", list(range(4, 13)),    (32, 45)),
        ("Treppenreinigung",    "Stunden", list(range(2, 9)),     (18, 25)),
        ("Teppichreinigung",    "Stunden", list(range(2, 7)),     (20, 30)),
        ("Reinigungsmittel",    "pauschal", [1],                  (20, 110)),
    ],
    "Handwerk": [
        ("Montagearbeit",       "Tage",    [1, 1.5, 2, 2.5, 3, 4, 5], (400, 520)),
        ("Tischlerarbeiten",    "Tage",    [1, 1.5, 2, 3, 4],     (420, 500)),
        ("Elektroinstallation", "Tage",    [0.5, 1, 1.5, 2],      (480, 560)),
        ("Material",            "pauschal", [1],                  (80, 2200)),
        ("Holz und Beschläge",  "pauschal", [1],                  (60, 800)),
        ("Transportpauschale",  "pauschal", [1],                  (50, 220)),
    ],
    "Beratung": [
        ("Strategieberatung",   "Tage",    [1, 1.5, 2, 3, 4, 5], (900, 1500)),
        ("Beratungsleistung",   "Tage",    [0.5, 1, 1.5, 2, 3],  (800, 1200)),
        ("Workshop",            "Tage",    [0.5, 1, 1.5, 2],     (1000, 1600)),
        ("Dokumentation",       "Tage",    [0.5, 1, 1.5],        (600, 1000)),
        ("Präsentation",        "Tage",    [0.5, 1],              (900, 1400)),
        ("Reisekosten",         "pauschal", [1],                  (50, 400)),
    ],
}

# ── Defect applicators ───────────────────────────────────────────────────────

DEFECTS = {
    "missing_supplier_uid": {
        "apply": lambda c: c["supplier"].update({"uid": None}) or c,
        "compliance_expected": False,
        "violated_field": "§11/1 Z3 — Supplier UID missing",
    },
    "malformed_supplier_uid": {
        "apply": lambda c: c["supplier"].update({"uid": "EU12345678"}) or c,  # wrong prefix
        "compliance_expected": False,
        "violated_field": "§11/1 Z3 — Supplier UID format invalid (not ATU…)",
    },
    "missing_invoice_number": {
        "apply": lambda c: c.update({"invoice_number": None}) or c,
        "compliance_expected": False,
        "violated_field": "§11/1 Z5 — Sequential invoice number missing",
    },
    "missing_service_period": {
        "apply": lambda c: c.update({"service_period": None}) or c,
        "compliance_expected": False,
        "violated_field": "§11/1 Z7 — Service period / Leistungsdatum missing",
    },
    "vat_arithmetic_error": {
        "apply": lambda c: c.update({"vat_amount": fmt_eur(
            float(c["net_total"].replace(".", "").replace(",", ".")) * 0.25
        )}) or c,
        "compliance_expected": False,
        "violated_field": "§11/1 Z11 — VAT amount does not match net × rate",
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_clients() -> list[dict]:
    return json.loads((DATA_DIR / "clients.json").read_text(encoding="utf-8"))


def random_service_date() -> date:
    """Return a date within the last 90 days."""
    return date.today() - timedelta(days=random.randint(0, 90))


def build_line_items(industry: str, n: int = None) -> list[dict]:
    pool = SERVICE_POOLS[industry]
    n = n or random.randint(1, 4)
    selected = random.sample(pool, min(n, len(pool)))
    items = []
    for desc, unit, qty_choices, (rate_min, rate_max) in selected:
        qty = random.choice(qty_choices)
        rate = round(random.uniform(rate_min, rate_max), -1)  # round to nearest 10
        amount = round(qty * rate, 2)
        items.append({
            "description": desc,
            "qty": fmt_qty(qty),
            "unit": unit,
            "rate": fmt_eur(rate),
            "amount": fmt_eur(amount),
            "_qty_f": qty,
            "_rate_f": rate,
            "_amount_f": amount,
        })
    return items


def build_context(industry: str, client: dict, invoice_num: str, svc_date: date) -> dict:
    profile = PROFILES[industry]
    items = build_line_items(industry)
    net = round(sum(i["_amount_f"] for i in items), 2)
    vat = round(net * 0.20, 2)
    gross = round(net + vat, 2)
    # strip internal float keys before passing to template
    line_items = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]
    return {
        "doc_type": "Rechnung",
        "invoice_number": invoice_num,
        "invoice_date": german_date(date.today()),
        "service_period": f"{MONATE[svc_date.month]} {svc_date.year}",
        "supplier": profile,
        "recipient": {
            "name": client["name"],
            "address_line1": client["address_line1"],
            "address_line2": client["address_line2"],
            "uid": client.get("uid"),
        },
        "line_items": line_items,
        "net_total": fmt_eur(net),
        "vat_rate": "20",
        "vat_amount": fmt_eur(vat),
        "gross_total": fmt_eur(gross),
        "payment_terms": "Zahlbar innerhalb von 14 Tagen",
        "brand_color": profile["brand_color"],
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    clients = load_clients()
    manifest = []
    total = 0

    for industry, slug in [("Reinigung", "reinigung"), ("Handwerk", "handwerk"), ("Beratung", "beratung")]:
        valid_dir = OUT_DIR / slug
        valid_dir.mkdir(parents=True, exist_ok=True)
        adv_dir = OUT_DIR / "adversarial"
        adv_dir.mkdir(parents=True, exist_ok=True)

        # 10 valid invoices per industry
        for n in range(1, 11):
            client = random.choice(clients)
            svc_date = random_service_date()
            invoice_num = f"{date.today().year}-{slug[:3].upper()}-{n:03d}"
            ctx = build_context(industry, client, invoice_num, svc_date)
            pdf_path = valid_dir / f"valid_{n:03d}.pdf"
            print(f"  {pdf_path.relative_to(ROOT)} ...", end=" ", flush=True)
            pdf_path.write_bytes(render_pdf(ctx))
            print("ok")
            manifest.append({
                "pdf": str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
                "industry": industry,
                "compliance_expected": True,
                "defects": [],
            })
            total += 1

        # One adversarial variant per defect type per industry
        base_client = clients[0]
        base_ctx = build_context(industry, base_client, f"{date.today().year}-ADV-001", date.today())
        for defect_name, defect in DEFECTS.items():
            ctx = defect["apply"](copy.deepcopy(base_ctx))
            pdf_path = adv_dir / f"{defect_name}_{slug}.pdf"
            print(f"  {pdf_path.relative_to(ROOT)} [{defect_name}] ...", end=" ", flush=True)
            pdf_path.write_bytes(render_pdf(ctx))
            print("ok")
            manifest.append({
                "pdf": str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
                "industry": industry,
                "compliance_expected": defect["compliance_expected"],
                "defects": [defect_name],
                "violated_field": defect.get("violated_field"),
            })
            total += 1

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGenerated {total} PDFs -> {manifest_path.relative_to(ROOT)}")

    # Round-trip sanity check (optional — requires pdfplumber)
    try:
        import pdfplumber
        sample = manifest[0]["pdf"]
        with pdfplumber.open(ROOT / sample) as pdf:
            text = pdf.pages[0].extract_text() or ""
        supplier_name = PROFILES["Reinigung"]["name"]
        assert supplier_name in text, f"Supplier name not found in extracted text"
        print(f"pdfplumber round-trip check: ok (supplier name found in {sample})")
    except ImportError:
        print("pdfplumber not installed — skipping round-trip check")
    except AssertionError as e:
        print(f"WARNING: round-trip check failed: {e}")


if __name__ == "__main__":
    main()
