"""Reports: data collection + ReportLab PDF rendering.

Public entry points:
  collect_eod(shift_id, conn=None) -> dict       — full EOD aggregate
  render_eod_pdf(data, store, out_path)          — pretty PDF, returns path
  collect_tax_summary(start_date, end_date)      — by-period tax totals
  collect_lottery(start_date, end_date)          — sales/payouts/net
  collect_best_sellers(start_date, end_date, limit=20)
  collect_void_log(start_date, end_date)
  collect_cashier_performance(start_date, end_date)
  render_generic_pdf(title, sections, store, out_path)
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from core import db
from core.logger import get_logger

log = get_logger("reports")

EXPORTS_DIR = Path("exports")


# ─── Data collection ─────────────────────────────────────────────────────────

def collect_eod(shift_id: int) -> dict:
    """Aggregate everything an EOD report needs for one shift.

    Returns a flat dict: keys = section name, value = list[tuple] of (label, value)
    plus raw `meta` (cashier, opened_at, etc).
    """
    conn = db.conn()
    shift = db.get_shift(shift_id) or {}

    txns = db.list_transactions_for_shift(shift_id)
    completed = [t for t in txns if t["status"] == "completed"]
    voided = [t for t in txns if t["status"] == "voided"]

    # Sums on completed only
    gross_cents = sum(t["total_cents"] for t in completed)
    voids_cents = sum(t["total_cents"] for t in voided)
    net_cents = gross_cents

    gst = sum(t["gst_cents"] for t in completed)
    pst = sum(t["pst_cents"] for t in completed)
    deposits = sum(t["deposit_cents"] for t in completed)
    bags = sum(t["bag_charge_cents"] for t in completed)
    discounts = sum(t["discount_cents"] for t in completed)

    cash_total = sum(t["total_cents"] for t in completed if t["payment_method"] == "cash")
    card_total = sum(t["card_amount_cents"] for t in completed)
    split_total = sum(t["total_cents"] for t in completed if t["payment_method"] == "split")

    # Cash actually-received (use rounded for cash payments)
    cash_received = sum(
        (t["rounded_total_cents"] if t["payment_method"] == "cash" else 0)
        for t in completed
    )

    # Lottery from ledger
    lot = db.lottery_totals_for_shift(shift_id)

    # Department breakdown (line-item level)
    dept_totals: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_cents": 0})
    for t in completed:
        full = db.get_transaction(t["id"])
        for it in full["items"]:
            dept = it.get("name", "?")
            # transaction_items doesn't store dept; group by item_id → look up dept from items table
            if it.get("item_id"):
                row = db.get_item(it["item_id"])
                dept = row.get("department", "?") if row else "?"
            else:
                dept = "_manual"
            dept_totals[dept]["count"] += it["quantity"]
            dept_totals[dept]["total_cents"] += it["line_total_cents"]

    # Cash events (drops, petty cash, no_sale)
    cash_events = db.list_cash_events(shift_id)
    drops = sum(e["amount_cents"] for e in cash_events if e["event_type"] == "drop")
    petty = sum(e["amount_cents"] for e in cash_events if e["event_type"] == "petty_cash")
    no_sales = sum(1 for e in cash_events if e["event_type"] == "no_sale")

    # Cash reconciliation
    opening = shift.get("opening_float_cents", 0) or 0
    expected_cash = opening + cash_received - lot["payouts"] - drops - petty
    closing = shift.get("closing_cash_cents")   # None until shift closed
    variance = (closing - expected_cash) if closing is not None else None

    return {
        "shift": shift,
        "txn_count": len(completed),
        "void_count": len(voided),
        "summary": {
            "gross_cents":   gross_cents,
            "voids_cents":   voids_cents,
            "net_cents":     net_cents,
            "discount_cents": discounts,
        },
        "tax": {
            "gst_cents":     gst,
            "pst_cents":     pst,
            "deposit_cents": deposits,
            "bag_cents":     bags,
        },
        "payment": {
            "cash_total_cents": cash_total,
            "cash_received_cents": cash_received,    # rounded
            "card_total_cents": card_total,
            "split_total_cents": split_total,
        },
        "lottery": lot,
        "departments": dict(dept_totals),
        "cash_events": {
            "drops_cents":  drops,
            "petty_cents":  petty,
            "no_sale_count": no_sales,
        },
        "reconciliation": {
            "opening_float_cents":   opening,
            "cash_received_cents":   cash_received,
            "lottery_payouts_cents": lot["payouts"],
            "drops_cents":           drops,
            "petty_cents":           petty,
            "expected_cash_cents":   expected_cash,
            "closing_cash_cents":    closing,
            "variance_cents":        variance,
        },
    }


def collect_tax_summary(start_date: str, end_date: str) -> dict:
    """All completed txns between dates (YYYY-MM-DD inclusive)."""
    conn = db.conn()
    rows = conn.execute(
        """SELECT * FROM transactions
           WHERE status = 'completed'
             AND date(created_at, 'localtime') BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()
    return {
        "period": (start_date, end_date),
        "txn_count": len(rows),
        "gross_cents":   sum(r["total_cents"] for r in rows),
        "gst_cents":     sum(r["gst_cents"] for r in rows),
        "pst_cents":     sum(r["pst_cents"] for r in rows),
        "deposit_cents": sum(r["deposit_cents"] for r in rows),
        "bag_cents":     sum(r["bag_charge_cents"] for r in rows),
    }


def collect_lottery(start_date: str, end_date: str) -> dict:
    conn = db.conn()
    rows = conn.execute(
        """SELECT entry_type, COALESCE(SUM(amount_cents),0) AS total
           FROM lottery_ledger
           WHERE date(created_at, 'localtime') BETWEEN ? AND ?
           GROUP BY entry_type""",
        (start_date, end_date),
    ).fetchall()
    sales = next((r["total"] for r in rows if r["entry_type"] == "sale"), 0)
    payouts = next((r["total"] for r in rows if r["entry_type"] == "payout"), 0)
    return {
        "period": (start_date, end_date),
        "sales_cents": sales,
        "payouts_cents": payouts,
        "net_cents": sales - payouts,
    }


def collect_best_sellers(start_date: str, end_date: str, *, limit: int = 20) -> list[dict]:
    """Top items by quantity sold."""
    conn = db.conn()
    rows = conn.execute(
        """SELECT ti.name,
                  SUM(ti.quantity) AS qty,
                  SUM(ti.line_total_cents) AS total_cents
           FROM transaction_items ti
           JOIN transactions t ON t.id = ti.transaction_id
           WHERE t.status = 'completed'
             AND date(t.created_at, 'localtime') BETWEEN ? AND ?
           GROUP BY ti.name
           ORDER BY qty DESC
           LIMIT ?""",
        (start_date, end_date, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def collect_void_log(start_date: str, end_date: str) -> list[dict]:
    conn = db.conn()
    rows = conn.execute(
        """SELECT v.*, t.transaction_ref
           FROM void_log v
           LEFT JOIN transactions t ON t.id = v.original_transaction_id
           WHERE date(v.created_at, 'localtime') BETWEEN ? AND ?
           ORDER BY v.id DESC""",
        (start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]


def collect_cashier_performance(start_date: str, end_date: str) -> list[dict]:
    conn = db.conn()
    rows = conn.execute(
        """SELECT cashier_name,
                  COUNT(*) AS txn_count,
                  SUM(total_cents) AS gross_cents
           FROM transactions
           WHERE status = 'completed'
             AND date(created_at, 'localtime') BETWEEN ? AND ?
             AND cashier_name IS NOT NULL
           GROUP BY cashier_name
           ORDER BY gross_cents DESC""",
        (start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── PDF rendering (ReportLab) ──────────────────────────────────────────────

def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def render_eod_pdf(data: dict, *, store: dict, out_dir: Path = EXPORTS_DIR) -> Path:
    """Render End-of-Day report. Professional layout, 8.5×11 portrait."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
    )
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

    out_dir.mkdir(parents=True, exist_ok=True)
    shift = data["shift"]
    shift_id = shift.get("id", "?")
    out = out_dir / f"eod_shift_{shift_id}.pdf"

    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=f"EOD Shift {shift_id}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                        textColor=colors.HexColor("#1B3A6B"),
                        fontSize=20, alignment=TA_CENTER, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        textColor=colors.HexColor("#1B3A6B"),
                        fontSize=13, spaceBefore=12, spaceAfter=6)
    sub = ParagraphStyle("sub", parent=styles["Normal"],
                         alignment=TA_CENTER, fontSize=10,
                         textColor=colors.HexColor("#7F8C8D"))
    body = styles["Normal"]

    story = []

    # Header
    logo_path = Path("assets/logo.png")
    if logo_path.exists():
        try:
            story.append(Image(str(logo_path), width=2.0 * inch, height=0.77 * inch,
                               hAlign="CENTER"))
        except Exception:
            pass
    story.append(Paragraph(store.get("name", "CityLink Convenience"), h1))
    if store.get("address"):
        story.append(Paragraph(store["address"], sub))
    story.append(Paragraph(
        f"END OF DAY REPORT — Shift #{shift_id}", h2,
    ))
    meta = (
        f"Cashier: <b>{shift.get('cashier_name', '?')}</b>"
        f"  ·  Opened: {shift.get('opened_at', '?')}"
        f"  ·  Closed: {shift.get('closed_at') or '(open)'}"
    )
    story.append(Paragraph(meta, body))
    story.append(Spacer(1, 8))

    # Helper for section tables
    def section(title: str, rows: list[tuple], totals_row: Optional[tuple] = None):
        story.append(Paragraph(title, h2))
        body_rows = [[r[0], r[1]] for r in rows]
        if totals_row:
            body_rows.append([f"<b>{totals_row[0]}</b>", f"<b>{totals_row[1]}</b>"])
        # Wrap in Paragraph for HTML support in totals row
        wrapped = []
        for left, right in body_rows:
            wrapped.append([Paragraph(str(left), body), Paragraph(str(right), body)])
        tbl = Table(wrapped, colWidths=[3.8 * inch, 1.7 * inch], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#C8D0E0")),
            ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.HexColor("#1B3A6B")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF2FF")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)

    # Transaction Summary
    s = data["summary"]
    section("Transaction Summary", [
        ("Completed transactions",         str(data["txn_count"])),
        ("Voided transactions",            str(data["void_count"])),
        ("Gross sales",                    _money(s["gross_cents"])),
        ("Discounts (deals)",              _money(s["discount_cents"])),
        ("Voids (excluded from gross)",    _money(s["voids_cents"])),
    ], totals_row=("Net sales", _money(s["net_cents"])))

    # Tax Collected
    t = data["tax"]
    tax_total = t["gst_cents"] + t["pst_cents"] + t["deposit_cents"] + t["bag_cents"]
    section("Tax & Fees Collected", [
        ("GST (5%)",       _money(t["gst_cents"])),
        ("PST (7%)",       _money(t["pst_cents"])),
        ("Bottle deposit", _money(t["deposit_cents"])),
        ("Bag charge",     _money(t["bag_cents"])),
    ], totals_row=("Total tax/fees", _money(tax_total)))

    # Payment Split
    p = data["payment"]
    section("Payment Split", [
        ("Cash (gross)",        _money(p["cash_total_cents"])),
        ("Cash received (rounded)", _money(p["cash_received_cents"])),
        ("Card",                _money(p["card_total_cents"])),
        ("Split",               _money(p["split_total_cents"])),
    ], totals_row=("Total received",
                   _money(p["cash_received_cents"] + p["card_total_cents"] + p["split_total_cents"])))

    # Lottery
    lot = data["lottery"]
    section("Lottery", [
        ("Sales",   _money(lot["sales"])),
        ("Payouts", _money(lot["payouts"])),
    ], totals_row=("Net lottery", _money(lot["net"])))

    # Department Breakdown
    if data["departments"]:
        story.append(Paragraph("Department Breakdown", h2))
        rows = [[Paragraph("<b>Department</b>", body),
                 Paragraph("<b>Items</b>", body),
                 Paragraph("<b>Total</b>", body)]]
        sorted_depts = sorted(
            data["departments"].items(), key=lambda kv: -kv[1]["total_cents"]
        )
        total_lines = 0
        total_value = 0
        for dept, agg in sorted_depts:
            rows.append([Paragraph(dept, body),
                         Paragraph(str(agg["count"]), body),
                         Paragraph(_money(agg["total_cents"]), body)])
            total_lines += agg["count"]
            total_value += agg["total_cents"]
        rows.append([Paragraph("<b>Total</b>", body),
                     Paragraph(f"<b>{total_lines}</b>", body),
                     Paragraph(f"<b>{_money(total_value)}</b>", body)])
        tbl = Table(rows, colWidths=[3.0 * inch, 1.0 * inch, 1.5 * inch], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#1B3A6B")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#C8D0E0")),
            ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.HexColor("#1B3A6B")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF2FF")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)

    # Cash Reconciliation
    r = data["reconciliation"]
    rec_rows = [
        ("Opening float",            _money(r["opening_float_cents"])),
        ("+ Cash received (rounded)", _money(r["cash_received_cents"])),
        ("− Lottery payouts",        _money(r["lottery_payouts_cents"])),
        ("− Cash drops",             _money(r["drops_cents"])),
        ("− Petty cash out",         _money(r["petty_cents"])),
    ]
    section("Cash Reconciliation", rec_rows,
            totals_row=("Expected in drawer", _money(r["expected_cash_cents"])))
    if r["closing_cash_cents"] is not None:
        story.append(Spacer(1, 4))
        v = r["variance_cents"]
        v_str = _money(v) if v is not None else "—"
        if v is not None and v < 0:
            v_str = f"-{_money(-v)}"
        section("Closing Count", [
            ("Counted closing cash", _money(r["closing_cash_cents"])),
        ], totals_row=("Variance (closing − expected)", v_str))

    story.append(Spacer(1, 24))
    foot = ParagraphStyle("foot", parent=styles["Normal"], alignment=TA_CENTER,
                          textColor=colors.HexColor("#7F8C8D"), fontSize=9)
    story.append(Paragraph(
        f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        foot,
    ))

    doc.build(story)
    log.info("EOD PDF saved: %s", out)
    return out


def render_generic_pdf(
    title: str,
    sections: list[tuple[str, list[tuple]]],
    *,
    store: dict,
    out_path: Path,
    period: Optional[tuple[str, str]] = None,
) -> Path:
    """Render an arbitrary report. `sections` = [(section_title, [(label, value), ...]), ...]"""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from reportlab.lib.enums import TA_CENTER

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=title,
    )
    s = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=s["Heading1"],
                        textColor=colors.HexColor("#1B3A6B"),
                        fontSize=18, alignment=TA_CENTER, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=s["Heading2"],
                        textColor=colors.HexColor("#1B3A6B"),
                        fontSize=13, spaceBefore=12, spaceAfter=6)
    sub = ParagraphStyle("sub", parent=s["Normal"],
                         alignment=TA_CENTER, fontSize=10,
                         textColor=colors.HexColor("#7F8C8D"))
    body = s["Normal"]

    story = [Paragraph(store.get("name", "CityLink"), h1)]
    if period:
        story.append(Paragraph(f"{period[0]} to {period[1]}", sub))
    story.append(Paragraph(title, h2))

    for sec_title, rows in sections:
        story.append(Paragraph(sec_title, h2))
        wrapped = [[Paragraph(str(left), body), Paragraph(str(right), body)] for left, right in rows]
        tbl = Table(wrapped, colWidths=[3.8 * inch, 1.7 * inch], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D0E0")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)

    doc.build(story)
    return out_path
