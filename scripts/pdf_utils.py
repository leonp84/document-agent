"""Shared PDF rendering utilities for DocAssist data generation scripts."""
import logging
from pathlib import Path

import jinja2

logging.getLogger("weasyprint").setLevel(logging.ERROR)
logging.getLogger("fonttools").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.ERROR)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

MONATE = {
    1: "Jänner", 2: "Februar", 3: "März", 4: "April",
    5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
    9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


def fmt_eur(value: float) -> str:
    """Format a float as a German decimal string: 1234.5 → '1.234,50'."""
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_qty(value) -> str:
    """Format a quantity: integer-valued floats drop the decimal."""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).replace(".", ",")


def german_date(d) -> str:
    """Format a date object as '15. Jänner 2026'."""
    return f"{d.day}. {MONATE[d.month]} {d.year}"


def render_pdf(context: dict, lang: str = "de") -> bytes:
    """Render an invoice context dict to a PDF byte string via Jinja2 + WeasyPrint."""
    from weasyprint import HTML  # imported here so the module loads without GTK present

    template_dir = TEMPLATES_DIR / lang
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    html_str = env.get_template("invoice.html").render(**context)
    return HTML(string=html_str, base_url=str(template_dir)).write_pdf()
