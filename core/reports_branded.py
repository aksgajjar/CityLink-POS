"""Premium branded PDF reports for CityLink POS.

Single helper `render_branded_pdf` produces a polished, enterprise-style
report layout: navy banner, logo, neat tables, page footer.

Eight ready-to-use report builders (each returns a Path):
  - daily_sales_pdf          (daily roll-up)
  - department_sales_pdf     (per-dept sales / qty / tax / share)
  - payment_summary_pdf      (cash vs card vs split)
  - refund_summary_pdf       (refunds in range, with refs)
  - lottery_summary_pdf      (sales − payouts → net)
  - cashier_summary_pdf      (per-cashier performance)
  - gst_pst_summary_pdf      (BC tax breakdown)
  - hourly_sales_pdf         (hour-of-day distribution)

All collectors are tolerant: missing tables / zero rows render an empty
section cleanly, never raise.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from core import db
from core.logger import get_logger

log = get_logger("reports.branded")

EXPORTS_DIR = Path("exports")
ASSETS_DIR = Path("assets")
LOGO_PATH = ASSETS_DIR / "logo.png"

NAVY = "#1B3A6B"
GREY = "#7F8C8D"
LIGHT_GREY = "#E1E4EA"
ACCENT = "#27AE60"
DANGER = "#E74C3C"


def _money(c: int) -> str:
    sign = "-" if c < 0 else ""
    return f"{sign}${abs(c) / 100:.2f}"


def _pct(num: int, denom: int) -> str:
    if denom <= 0:
        return "—"
    return f"{(num / denom) * 100:.1f}%"


# ─── Premium PDF template ────────────────────────────────────────────────────

def render_branded_pdf(
    *,
    title: str,
    subtitle: str,
    out_path: Path,
    sections: list[dict],
    store: Optional[dict] = None,
) -> Path:
    """Build a premium branded PDF.

    sections — list of dicts. Each dict supports:
      {"heading": str, "table": [[...rows...]], "headers": [str, ...]}
    OR
      {"heading": str, "kv": [(label, value), ...]}
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.pdfgen import canvas as _canvas

    out_path.parent.mkdir(parents=True, exist_ok=True)
    store = store or {}
    store_name = store.get("name", "CityLink Convenience")

    # Page geometry — letter, generous side margins for clean enterprise feel.
    PAGE_W, PAGE_H = letter
    margin = 0.55 * inch
    banner_h = 0.85 * inch

    def _page_decoration(c, _doc):
        # Navy banner across the top.
        c.setFillColor(colors.HexColor(NAVY))
        c.rect(0, PAGE_H - banner_h, PAGE_W, banner_h, fill=1, stroke=0)
        # Logo (left of banner) if available.
        try:
            if LOGO_PATH.exists():
                c.drawImage(
                    str(LOGO_PATH), margin,
                    PAGE_H - banner_h + 0.12 * inch,
                    width=0.6 * inch, height=0.6 * inch,
                    preserveAspectRatio=True, mask="auto",
                )
        except Exception:
            log.exception("logo render failed")
        # Title text right of logo.
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin + 0.85 * inch, PAGE_H - 0.45 * inch, title)
        c.setFont("Helvetica", 10)
        c.drawString(margin + 0.85 * inch, PAGE_H - 0.65 * inch, subtitle)
        # Right-side store + generation timestamp.
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(PAGE_W - margin, PAGE_H - 0.45 * inch, store_name)
        c.setFont("Helvetica", 9)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        c.drawRightString(PAGE_W - margin, PAGE_H - 0.65 * inch,
                          f"Generated {ts}")

        # Footer rule + page number.
        c.setStrokeColor(colors.HexColor(LIGHT_GREY))
        c.setLineWidth(0.5)
        c.line(margin, 0.55 * inch, PAGE_W - margin, 0.55 * inch)
        c.setFillColor(colors.HexColor(GREY))
        c.setFont("Helvetica", 8)
        c.drawString(margin, 0.4 * inch, "CityLink POS — Confidential")
        c.drawRightString(PAGE_W - margin, 0.4 * inch,
                          f"Page {c.getPageNumber()}")

    frame = Frame(
        margin, 0.7 * inch,
        PAGE_W - 2 * margin,
        PAGE_H - banner_h - 1.0 * inch,
        leftPadding=0, rightPadding=0,
        topPadding=10, bottomPadding=0,
        showBoundary=0,
    )

    doc = BaseDocTemplate(str(out_path), pagesize=letter, title=title)
    doc.addPageTemplates([PageTemplate(id="branded", frames=[frame],
                                       onPage=_page_decoration)])

    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Heading2"],
                       textColor=colors.HexColor(NAVY),
                       fontSize=13, spaceBefore=10, spaceAfter=4,
                       fontName="Helvetica-Bold")
    body = ParagraphStyle("body", parent=styles["Normal"],
                          fontName="Helvetica", fontSize=10, leading=12)
    body_b = ParagraphStyle("body_b", parent=body, fontName="Helvetica-Bold")
    body_r = ParagraphStyle("body_r", parent=body, alignment=TA_RIGHT)

    story: list = [Spacer(1, 4)]

    def _add_kv_table(rows):
        if not rows:
            story.append(Paragraph("No data.", body)); return
        wrapped = [[Paragraph(str(k), body), Paragraph(str(v), body_r)]
                   for k, v in rows]
        t = Table(wrapped, colWidths=[3.8 * inch, 2.2 * inch], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor(LIGHT_GREY)),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)

    def _add_grid(headers, rows):
        if not rows:
            story.append(Paragraph("No data.", body)); return
        head_para = [Paragraph(f"<b>{h}</b>", body) for h in headers]
        body_rows = [[Paragraph(str(c), body) for c in r] for r in rows]
        t = Table([head_para] + body_rows, hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F4F6F8")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor(LIGHT_GREY)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)

    for sec in sections:
        story.append(Paragraph(sec.get("heading", ""), h))
        if "kv" in sec:
            _add_kv_table(sec["kv"])
        elif "table" in sec:
            _add_grid(sec.get("headers", []), sec["table"])
        story.append(Spacer(1, 6))

    doc.build(story)
    log.info("branded report built: %s", out_path)
    return out_path


# ─── Collectors (DB-tolerant) ────────────────────────────────────────────────

def _txns_in_range(start: str, end: str, status: str = "completed") -> list[dict]:
    sql = (
        "SELECT * FROM transactions "
        "WHERE date(created_at, 'localtime') BETWEEN ? AND ? "
        "  AND status = ? ORDER BY id"
    )
    try:
        return [dict(r) for r in db.conn().execute(sql, (start, end, status)).fetchall()]
    except Exception:
        log.exception("_txns_in_range failed")
        return []


def _items_in_range(start: str, end: str) -> list[dict]:
    sql = (
        "SELECT ti.*, t.created_at, t.payment_method "
        "FROM transaction_items ti "
        "JOIN transactions t ON t.id = ti.transaction_id "
        "WHERE date(t.created_at, 'localtime') BETWEEN ? AND ? "
        "  AND t.status = 'completed'"
    )
    try:
        return [dict(r) for r in db.conn().execute(sql, (start, end)).fetchall()]
    except Exception:
        log.exception("_items_in_range failed")
        return []


# ─── Eight branded report builders ───────────────────────────────────────────

def _filename(prefix: str, suffix: str = "") -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    s = datetime.now().strftime("%Y-%m-%d")
    name = f"CityLink_{prefix}_{s}{suffix}.pdf"
    return EXPORTS_DIR / name


def daily_sales_pdf(date: str, *, store: Optional[dict] = None) -> Path:
    """Daily sales report — single date (YYYY-MM-DD)."""
    txns = _txns_in_range(date, date)
    n = len(txns)
    gross = sum(t["total_cents"] for t in txns)
    gst = sum(t.get("gst_cents", 0) for t in txns)
    pst = sum(t.get("pst_cents", 0) for t in txns)
    cash_t = [t for t in txns if t["payment_method"] == "cash"]
    card_t = [t for t in txns if t["payment_method"] == "card"]
    split_t = [t for t in txns if t["payment_method"] == "split"]
    refund_t = _txns_in_range(date, date, status="refunded")
    refund_total = sum(t["total_cents"] for t in refund_t)

    sections = [
        {"heading": "Sales Summary",
         "kv": [
            ("Transactions", str(n)),
            ("Gross Sales", _money(gross)),
            ("GST (5%)", _money(gst)),
            ("PST (7%)", _money(pst)),
            ("Refunds", _money(refund_total)),
            ("Net Sales", _money(gross + refund_total)),
         ]},
        {"heading": "Payment Mix",
         "headers": ["Method", "Count", "Total", "Share"],
         "table": [
             ["Cash",  str(len(cash_t)), _money(sum(t["total_cents"] for t in cash_t)),
              _pct(sum(t["total_cents"] for t in cash_t), gross)],
             ["Card",  str(len(card_t)), _money(sum(t["total_cents"] for t in card_t)),
              _pct(sum(t["total_cents"] for t in card_t), gross)],
             ["Split", str(len(split_t)), _money(sum(t["total_cents"] for t in split_t)),
              _pct(sum(t["total_cents"] for t in split_t), gross)],
         ]},
    ]
    out = _filename("Daily_Report")
    return render_branded_pdf(
        title="Daily Sales Report",
        subtitle=f"Date: {date}",
        out_path=out, sections=sections, store=store,
    )


def department_sales_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    items = _items_in_range(start, end)
    by_dept: dict[str, dict] = {}
    grand_total = 0
    for it in items:
        # Look up dept via items table.
        row = db.conn().execute(
            "SELECT department FROM items WHERE id = ?", (it.get("item_id"),)
        ).fetchone() if it.get("item_id") else None
        dept = (row["department"] if row else "manual") or "manual"
        d = by_dept.setdefault(dept, {"qty": 0, "subtotal": 0, "tax": 0, "total": 0})
        d["qty"] += int(it.get("quantity", 1))
        d["subtotal"] += int(it.get("unit_price_cents", 0)) * int(it.get("quantity", 1))
        d["tax"] += int(it.get("gst_cents", 0)) + int(it.get("pst_cents", 0))
        d["total"] += int(it.get("line_total_cents", 0))
        grand_total += int(it.get("line_total_cents", 0))

    rows = []
    for dept, d in sorted(by_dept.items(), key=lambda x: -x[1]["total"]):
        rows.append([dept, str(d["qty"]), _money(d["subtotal"]),
                     _money(d["tax"]), _money(d["total"]),
                     _pct(d["total"], grand_total)])
    sections = [
        {"heading": "Department Sales",
         "headers": ["Department", "Qty", "Subtotal", "Tax", "Total", "Share"],
         "table": rows},
    ]
    out = _filename("Department_Report", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="Department Sales Report",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def payment_summary_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    txns = _txns_in_range(start, end)
    cash = sum(t["cash_tendered_cents"] - t.get("change_cents", 0)
               for t in txns if t["payment_method"] in ("cash", "split"))
    card = sum(t.get("card_amount_cents", 0)
               for t in txns if t["payment_method"] in ("card", "split"))
    refunds = sum(t["total_cents"]
                  for t in _txns_in_range(start, end, status="refunded"))
    sections = [{
        "heading": "Payment Summary",
        "kv": [
            ("Cash Tendered (Net of Change)", _money(cash)),
            ("Card Charged", _money(card)),
            ("Refunds", _money(refunds)),
            ("Net Receipts", _money(cash + card + refunds)),
        ],
    }]
    out = _filename("Payment_Summary", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="Payment Summary",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def refund_summary_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    rows = _txns_in_range(start, end, status="refunded")
    table = []
    for r in rows:
        table.append([
            r["transaction_ref"], r.get("created_at", ""),
            r.get("cashier_name", "—"),
            _money(r["total_cents"]),
        ])
    sections = [{
        "heading": f"Refunds — {len(rows)} record(s)",
        "headers": ["Ref", "Date/Time", "Cashier", "Amount"],
        "table": table,
    }]
    out = _filename("Refund_Summary", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="Refund Summary",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def lottery_summary_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    try:
        rows = db.conn().execute(
            "SELECT entry_type, SUM(amount_cents) AS amt, COUNT(*) AS n "
            "FROM lottery_ledger "
            "WHERE date(created_at, 'localtime') BETWEEN ? AND ? "
            "GROUP BY entry_type",
            (start, end),
        ).fetchall()
    except Exception:
        log.exception("lottery query failed"); rows = []
    sales = next((r["amt"] for r in rows if r["entry_type"] == "sale"), 0) or 0
    payouts = next((r["amt"] for r in rows if r["entry_type"] == "payout"), 0) or 0
    sections = [{
        "heading": "Lottery Activity",
        "kv": [
            ("Sales", _money(sales)),
            ("Payouts", _money(payouts)),
            ("Net", _money(sales - payouts)),
        ],
    }]
    out = _filename("Lottery_Summary", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="Lottery Payout Summary",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def cashier_summary_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    txns = _txns_in_range(start, end)
    by_c: dict[str, dict] = {}
    for t in txns:
        c = t.get("cashier_name") or "—"
        d = by_c.setdefault(c, {"n": 0, "total": 0})
        d["n"] += 1
        d["total"] += int(t["total_cents"])
    rows = [[c, str(d["n"]), _money(d["total"])]
            for c, d in sorted(by_c.items(), key=lambda x: -x[1]["total"])]
    sections = [{
        "heading": "Cashier Performance",
        "headers": ["Cashier", "Transactions", "Total"],
        "table": rows,
    }]
    out = _filename("Cashier_Summary", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="Cashier Summary",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def gst_pst_summary_pdf(start: str, end: str, *, store: Optional[dict] = None) -> Path:
    txns = _txns_in_range(start, end)
    gst = sum(t.get("gst_cents", 0) for t in txns)
    pst = sum(t.get("pst_cents", 0) for t in txns)
    gross = sum(t["total_cents"] for t in txns)
    sections = [{
        "heading": "BC Tax Summary",
        "kv": [
            ("GST collected (5%)", _money(gst)),
            ("PST collected (7%)", _money(pst)),
            ("Total Tax", _money(gst + pst)),
            ("Gross Sales (incl. tax)", _money(gross)),
        ],
    }]
    out = _filename("GST_PST_Summary", f"_{start}_to_{end}")
    return render_branded_pdf(
        title="GST / PST Summary",
        subtitle=f"{start} → {end}",
        out_path=out, sections=sections, store=store,
    )


def hourly_sales_pdf(date: str, *, store: Optional[dict] = None) -> Path:
    try:
        rows = db.conn().execute(
            "SELECT strftime('%H', created_at, 'localtime') AS hour, "
            "       COUNT(*) AS n, SUM(total_cents) AS total "
            "FROM transactions "
            "WHERE date(created_at, 'localtime') = ? AND status = 'completed' "
            "GROUP BY hour ORDER BY hour",
            (date,),
        ).fetchall()
    except Exception:
        log.exception("hourly query failed"); rows = []
    table = []
    for r in rows:
        table.append([f"{r['hour']}:00", str(r["n"]), _money(r["total"] or 0)])
    sections = [{
        "heading": "Hourly Sales Distribution",
        "headers": ["Hour", "Transactions", "Total"],
        "table": table,
    }]
    out = _filename("Hourly_Sales", f"_{date}")
    return render_branded_pdf(
        title="Hourly Sales Report",
        subtitle=f"Date: {date}",
        out_path=out, sections=sections, store=store,
    )
