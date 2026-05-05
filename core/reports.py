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


def collect_period_report(start_date: str, end_date: str) -> dict:
    """Aggregate everything a period report needs (any date range).

    Returns a dict with daily breakdown + dept totals + payment split + tax.
    """
    conn = db.conn()

    # All completed transactions in range
    txns = [dict(r) for r in conn.execute(
        """SELECT * FROM transactions
           WHERE status = 'completed'
             AND date(created_at, 'localtime') BETWEEN ? AND ?
           ORDER BY created_at""",
        (start_date, end_date),
    ).fetchall()]

    voided = [dict(r) for r in conn.execute(
        """SELECT * FROM transactions
           WHERE status = 'voided'
             AND date(created_at, 'localtime') BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()]

    # Dept totals — group line items by item.department
    dept_totals: dict[str, int] = defaultdict(int)
    dept_qtys: dict[str, int] = defaultdict(int)

    # Daily breakdown structure
    daily: dict[str, dict] = defaultdict(lambda: {
        "date": "",
        "dept_totals": defaultdict(int),
        "gross": 0,
        "discount": 0,
        "gst": 0,
        "pst": 0,
        "deposit": 0,
        "bag": 0,
        "net_sales": 0,
        "taxes": 0,
        "total": 0,
    })

    for t in txns:
        # Local date of the txn
        day = conn.execute(
            "SELECT date(?, 'localtime') AS d", (t["created_at"],)
        ).fetchone()["d"]

        d = daily[day]
        d["date"] = day
        d["gross"] += t["total_cents"]
        d["discount"] += t["discount_cents"]
        d["gst"] += t["gst_cents"]
        d["pst"] += t["pst_cents"]
        d["deposit"] += t["deposit_cents"]
        d["bag"] += t["bag_charge_cents"]
        d["net_sales"] += t["total_cents"]
        d["taxes"] += t["gst_cents"] + t["pst_cents"]
        d["total"] += t["total_cents"]

        # Per-line dept attribution
        full = db.get_transaction(t["id"])
        for it in full["items"]:
            dept = "_manual"
            if it.get("item_id"):
                row = db.get_item(it["item_id"])
                if row:
                    dept = row["department"]
            dept_totals[dept] += it["line_total_cents"]
            dept_qtys[dept] += it["quantity"]
            d["dept_totals"][dept] += it["line_total_cents"]

    # Lottery from ledger
    lot = collect_lottery(start_date, end_date)

    # Aggregates
    gross_sales = sum(t["subtotal_cents"] for t in txns)
    discounts = sum(t["discount_cents"] for t in txns)
    gst_total = sum(t["gst_cents"] for t in txns)
    pst_total = sum(t["pst_cents"] for t in txns)
    deposits = sum(t["deposit_cents"] for t in txns)
    bags = sum(t["bag_charge_cents"] for t in txns)

    cash_received = sum(
        (t["rounded_total_cents"] if t["payment_method"] == "cash" else 0)
        for t in txns
    )
    card_total = sum(t["card_amount_cents"] for t in txns)
    split_total = sum(
        (t["total_cents"] if t["payment_method"] == "split" else 0)
        for t in txns
    )

    # Net sales = subtotal - discount + deposit + bag (the customer-facing net)
    # Total collectable = net + GST + PST  (mirrors what the cashier should bring in)
    net_sales = gross_sales - discounts + deposits + bags
    total_taxes = gst_total + pst_total
    total_collectable = net_sales + total_taxes
    total_collected = cash_received + card_total + split_total

    # Convert daily sub-defaultdicts to plain dicts (sorted)
    daily_list = []
    for day in sorted(daily.keys()):
        d = daily[day]
        daily_list.append({
            "date": d["date"],
            "dept_totals": dict(d["dept_totals"]),
            "gross": d["gross"],
            "discount": d["discount"],
            "gst": d["gst"],
            "pst": d["pst"],
            "deposit": d["deposit"],
            "bag": d["bag"],
            "net_sales": d["net_sales"],
            "taxes": d["taxes"],
            "total": d["total"],
        })

    return {
        "period": (start_date, end_date),
        "txn_count": len(txns),
        "void_count": len(voided),
        "daily": daily_list,
        "departments": {k: {"total": v, "qty": dept_qtys[k]} for k, v in dept_totals.items()},
        "lottery": {
            "sales_cents": lot["sales_cents"],
            "payouts_cents": lot["payouts_cents"],
            "net_cents": lot["net_cents"],
        },
        "deposits_cents": deposits,
        "bag_charges_cents": bags,
        "summary": {
            "gross_sales_cents": gross_sales,
            "discounts_cents": discounts,
            "net_sales_cents": net_sales,
            "gst_cents": gst_total,
            "pst_cents": pst_total,
            "total_taxes_cents": total_taxes,
            "total_collectable_cents": total_collectable,
        },
        "payment": {
            "cash_received_cents": cash_received,
            "card_cents": card_total,
            "split_cents": split_total,
            "total_collected_cents": total_collected,
        },
        "verification": {
            "collectable_cents": total_collectable,
            "collected_cents": total_collected,
            "variance_cents": total_collected - total_collectable,
        },
    }


def collect_comparison_report(
    a_start: str, a_end: str, b_start: str, b_end: str,
) -> dict:
    """Two-period comparison. Returns A, B, plus per-metric deltas."""
    a = collect_period_report(a_start, a_end)
    b = collect_period_report(b_start, b_end)

    def diff(a_cents: int, b_cents: int) -> tuple[int, float]:
        delta = b_cents - a_cents
        pct = (delta / a_cents * 100) if a_cents else (100.0 if b_cents else 0.0)
        return delta, pct

    metrics = {
        "gross_sales":  diff(a["summary"]["gross_sales_cents"], b["summary"]["gross_sales_cents"]),
        "net_sales":    diff(a["summary"]["net_sales_cents"], b["summary"]["net_sales_cents"]),
        "gst":          diff(a["summary"]["gst_cents"], b["summary"]["gst_cents"]),
        "pst":          diff(a["summary"]["pst_cents"], b["summary"]["pst_cents"]),
        "discounts":    diff(a["summary"]["discounts_cents"], b["summary"]["discounts_cents"]),
        "lottery_net":  diff(a["lottery"]["net_cents"], b["lottery"]["net_cents"]),
        "txn_count":    (b["txn_count"] - a["txn_count"],
                         (b["txn_count"] - a["txn_count"]) / a["txn_count"] * 100 if a["txn_count"] else 0),
        "cash":         diff(a["payment"]["cash_received_cents"], b["payment"]["cash_received_cents"]),
        "card":         diff(a["payment"]["card_cents"], b["payment"]["card_cents"]),
    }

    # Per-dept comparison
    all_depts = set(a["departments"].keys()) | set(b["departments"].keys())
    dept_deltas = {}
    for d in all_depts:
        a_v = a["departments"].get(d, {"total": 0})["total"]
        b_v = b["departments"].get(d, {"total": 0})["total"]
        dept_deltas[d] = diff(a_v, b_v)

    return {"a": a, "b": b, "metrics": metrics, "dept_deltas": dept_deltas}


# ─── Chart helpers (matplotlib → PNG bytes) ──────────────────────────────────

NAVY = "#1B3A6B"
BLUE_MID = "#2E5BA8"
BLUE_LIGHT = "#5B9BD5"
SUCCESS = "#27AE60"
DANGER = "#E74C3C"


def _chart_setup():
    """Lazy matplotlib import + Agg backend (no display needed)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def chart_daily_sales_bar(daily: list[dict]) -> bytes:
    """Bar chart of daily total sales. Returns PNG bytes."""
    plt = _chart_setup()
    from io import BytesIO

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)
    if daily:
        labels = [d["date"][-5:] for d in daily]   # "MM-DD"
        values = [d["total"] / 100 for d in daily]
        bars = ax.bar(labels, values, color=NAVY, edgecolor=BLUE_MID, linewidth=0.6)
        ax.set_ylabel("Sales ($)", color=NAVY)
        ax.set_title("Daily Sales", color=NAVY, fontsize=12, fontweight="bold", pad=10)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#999")
        ax.spines["bottom"].set_color("#999")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No sales", ha="center", va="center",
                color=NAVY, fontsize=12, transform=ax.transAxes)
        ax.set_axis_off()

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def chart_dept_pie(dept_totals: dict[str, dict]) -> bytes:
    """Pie chart of department share (% of total)."""
    plt = _chart_setup()
    from io import BytesIO

    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    items = [(k, v["total"]) for k, v in dept_totals.items() if v["total"] > 0]
    items.sort(key=lambda x: -x[1])
    if items:
        labels = [k for k, _ in items]
        values = [v / 100 for _, v in items]
        # CityLink blue palette
        palette = [NAVY, BLUE_MID, BLUE_LIGHT, "#7DA8D9", "#A0BBE0",
                   "#27AE60", "#F39C12", "#E74C3C", "#9B27B0", "#16A085",
                   "#FF8C42", "#00BCD4", "#6C3483", "#546E7A", "#1B6B3A"]
        ax.pie(values, labels=labels,
               colors=palette[:len(items)],
               autopct="%1.1f%%",
               textprops={"fontsize": 9, "color": NAVY},
               wedgeprops={"edgecolor": "white", "linewidth": 1.5})
        ax.set_title("Department Share", color=NAVY, fontsize=12, fontweight="bold", pad=10)
    else:
        ax.text(0.5, 0.5, "No dept data", ha="center", va="center",
                color=NAVY, fontsize=12, transform=ax.transAxes)
        ax.set_axis_off()

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def chart_payment_split_bar(payment: dict) -> bytes:
    """Bar chart of payment method split."""
    plt = _chart_setup()
    from io import BytesIO

    fig, ax = plt.subplots(figsize=(6, 3.2), dpi=100)
    labels = ["Cash", "Card", "Split"]
    values = [
        payment["cash_received_cents"] / 100,
        payment["card_cents"] / 100,
        payment["split_cents"] / 100,
    ]
    colors = [SUCCESS, BLUE_MID, "#9B27B0"]
    ax.bar(labels, values, color=colors, edgecolor="white", linewidth=1)
    ax.set_ylabel("$", color=NAVY)
    ax.set_title("Payment Split", color=NAVY, fontsize=12, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    for i, v in enumerate(values):
        if v > 0:
            ax.text(i, v, f"${v:.2f}", ha="center", va="bottom",
                    color=NAVY, fontsize=9, fontweight="bold")

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def chart_comparison_bar(a_data: dict, b_data: dict, *, labels: tuple[str, str]) -> bytes:
    """Side-by-side bar comparing key metrics for periods A vs B."""
    plt = _chart_setup()
    from io import BytesIO
    import numpy as np

    metrics = ["Gross", "Net", "GST", "PST", "Cash", "Card"]
    a_vals = [
        a_data["summary"]["gross_sales_cents"] / 100,
        a_data["summary"]["net_sales_cents"] / 100,
        a_data["summary"]["gst_cents"] / 100,
        a_data["summary"]["pst_cents"] / 100,
        a_data["payment"]["cash_received_cents"] / 100,
        a_data["payment"]["card_cents"] / 100,
    ]
    b_vals = [
        b_data["summary"]["gross_sales_cents"] / 100,
        b_data["summary"]["net_sales_cents"] / 100,
        b_data["summary"]["gst_cents"] / 100,
        b_data["summary"]["pst_cents"] / 100,
        b_data["payment"]["cash_received_cents"] / 100,
        b_data["payment"]["card_cents"] / 100,
    ]

    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    x = np.arange(len(metrics))
    w = 0.35
    ax.bar(x - w/2, a_vals, w, label=labels[0], color=NAVY, edgecolor="white")
    ax.bar(x + w/2, b_vals, w, label=labels[1], color=BLUE_LIGHT, edgecolor="white")
    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.set_ylabel("$", color=NAVY)
    ax.set_title("Period Comparison", color=NAVY, fontsize=12, fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ─── Detailed period report PDF (multi-page Visual-Touch style) ──────────────

def render_period_report_pdf(
    data: dict,
    *,
    store: dict,
    report_type: str = "Period",
    out_dir: Path = EXPORTS_DIR,
) -> Path:
    """4-page period report PDF with daily table + totals analysis + charts + tax summary."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from io import BytesIO

    out_dir.mkdir(parents=True, exist_ok=True)
    period = data["period"]
    safe_period = f"{period[0]}_to_{period[1]}"
    out = out_dir / f"{report_type.lower()}_report_{safe_period}.pdf"

    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.4 * inch,
        title=f"{report_type} Report  {period[0]} to {period[1]}",
    )

    s = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=s["Heading1"],
                        textColor=colors.HexColor(NAVY),
                        fontSize=18, alignment=TA_CENTER, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=s["Heading2"],
                        textColor=colors.HexColor(NAVY),
                        fontSize=13, spaceBefore=10, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=s["Normal"], alignment=TA_CENTER,
                         fontSize=10, textColor=colors.HexColor("#7F8C8D"))
    body = s["Normal"]
    body_r = ParagraphStyle("body_r", parent=s["Normal"], alignment=TA_RIGHT)

    story = []

    # ─── PAGE 1 — Header + daily breakdown table ──────────────────────────
    logo_path = Path("assets/logo.png")
    if logo_path.exists():
        try:
            story.append(Image(str(logo_path), width=1.6 * inch, height=0.62 * inch,
                               hAlign="CENTER"))
        except Exception:
            pass
    story.append(Paragraph(store.get("name", "CityLink Convenience"), h1))
    if store.get("address"):
        story.append(Paragraph(store["address"], sub))
    story.append(Paragraph(f"{report_type.upper()} SALES REPORT", h2))
    story.append(Paragraph(
        f"{period[0]}  to  {period[1]}  ·  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        sub,
    ))
    story.append(Spacer(1, 8))

    # Daily breakdown table — narrow columns, key depts only for fit
    KEY_DEPTS = ["candy", "drinks", "carbonated", "medicine"]
    daily = data["daily"]
    if daily:
        header_row = ["Date"] + [d.title() for d in KEY_DEPTS] + [
            "Lott Sales", "Lott Pay", "GST", "Disc", "Net", "Tax", "Total"
        ]
        # Aggregate lottery per day from the ledger
        lottery_per_day: dict[str, dict] = {}
        for r in db.conn().execute(
            """SELECT date(created_at, 'localtime') AS d,
                      SUM(CASE WHEN entry_type='sale'   THEN amount_cents END) AS s,
                      SUM(CASE WHEN entry_type='payout' THEN amount_cents END) AS p
               FROM lottery_ledger
               WHERE date(created_at, 'localtime') BETWEEN ? AND ?
               GROUP BY d""",
            (period[0], period[1]),
        ).fetchall():
            lottery_per_day[r["d"]] = {"s": r["s"] or 0, "p": r["p"] or 0}

        rows = [header_row]
        for d in daily:
            lot = lottery_per_day.get(d["date"], {"s": 0, "p": 0})
            rows.append([
                d["date"],
                *[_money(d["dept_totals"].get(dept, 0)) for dept in KEY_DEPTS],
                _money(lot["s"]),
                _money(lot["p"]),
                _money(d["gst"]),
                _money(d["discount"]),
                _money(d["net_sales"]),
                _money(d["taxes"]),
                _money(d["total"]),
            ])
        # Grand total row
        rows.append([
            "TOTAL",
            *[_money(sum(d["dept_totals"].get(dept, 0) for d in daily)) for dept in KEY_DEPTS],
            _money(data["lottery"]["sales_cents"]),
            _money(data["lottery"]["payouts_cents"]),
            _money(data["summary"]["gst_cents"]),
            _money(data["summary"]["discounts_cents"]),
            _money(data["summary"]["net_sales_cents"]),
            _money(data["summary"]["total_taxes_cents"]),
            _money(sum(d["total"] for d in daily)),
        ])

        tbl = Table(rows, colWidths=[0.7 * inch] + [0.55 * inch] * (len(header_row) - 1),
                    repeatRows=1, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#C8D0E0")),
            ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.HexColor(NAVY)),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EEF2FF")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("No sales in this period.", body))

    # ─── PAGE 2 — Overall Totals Analysis (two columns) ──────────────────
    story.append(PageBreak())
    story.append(Paragraph("OVERALL TOTALS ANALYSIS", h2))

    # Build COLLECTABLE rows
    summ = data["summary"]
    collectable_rows = []
    for d_id in [
        "candy", "drinks", "carbonated", "non_carbonated", "snacks",
        "confectionery", "medicine", "stationary", "gift_items", "gift_cards",
        "ice_cream", "slush", "lottery", "accessories", "retail",
    ]:
        from core.departments import DEPT_BY_ID
        cents = data["departments"].get(d_id, {"total": 0})["total"]
        collectable_rows.append((DEPT_BY_ID[d_id]["label"] + " (Gross)", _money(cents), False))

    collectable_rows.append(("LOTTERY SALES",        _money(data["lottery"]["sales_cents"]),   False))
    collectable_rows.append(("LOTTERY PAYOUT",       f"-{_money(data['lottery']['payouts_cents'])}",  True))
    collectable_rows.append(("Bottle Deposits",      _money(data["deposits_cents"]),   False))
    collectable_rows.append(("Bag Charges",          _money(data["bag_charges_cents"]), False))
    collectable_rows.append(("──────────────",        "",                                False))
    collectable_rows.append(("Gross Sales",          _money(summ["gross_sales_cents"]), False))
    collectable_rows.append(("Less Discounts",       f"-{_money(summ['discounts_cents'])}",   True))
    collectable_rows.append(("Net Sales",            _money(summ["net_sales_cents"]), True))
    collectable_rows.append(("──────────────",        "",                                False))
    collectable_rows.append(("PST 7% collected",     _money(summ["pst_cents"]),         False))
    collectable_rows.append(("GST 5% collected",     _money(summ["gst_cents"]),         False))
    collectable_rows.append(("Total Taxes",          _money(summ["total_taxes_cents"]), True))
    collectable_rows.append(("══════════════",        "",                                False))
    collectable_rows.append(("TOTAL COLLECTABLE",    _money(summ["total_collectable_cents"]), True))

    # COLLECTED rows
    pay = data["payment"]
    collected_rows = [
        ("Cash",                      _money(pay["cash_received_cents"]), False),
        ("Card (Debit / Credit)",     _money(pay["card_cents"]),          False),
        ("Split Transactions",        _money(pay["split_cents"]),         False),
        ("──────────────",            "",                                  False),
        ("TOTAL COLLECTED",           _money(pay["total_collected_cents"]), True),
    ]

    # Build two parallel tables side-by-side
    def _build_two_col(left_rows, right_rows, *, header_l, header_r):
        max_len = max(len(left_rows), len(right_rows))
        # Pad shorter
        left_rows  = left_rows + [("", "", False)] * (max_len - len(left_rows))
        right_rows = right_rows + [("", "", False)] * (max_len - len(right_rows))

        # Assemble paragraphs with optional bold/red styling
        def cell(text: str, *, bold: bool, danger: bool = False, align_right: bool = False):
            sty = body_r if align_right else body
            color_str = f' color="{DANGER}"' if danger else ""
            weight = "<b>" if bold else ""
            weight_close = "</b>" if bold else ""
            return Paragraph(f'<font{color_str}>{weight}{text}{weight_close}</font>', sty)

        rows = [[
            Paragraph(f"<b>{header_l}</b>", body),
            Paragraph(f"<b>{header_r}</b>", body),
        ]]
        for i in range(max_len):
            l_lbl, l_val, l_bold = left_rows[i]
            r_lbl, r_val, r_bold = right_rows[i]
            l_danger = (l_lbl in ("LOTTERY PAYOUT", "Less Discounts"))
            row = [
                Table([[cell(l_lbl, bold=l_bold, danger=l_danger),
                        cell(l_val, bold=l_bold, danger=l_danger, align_right=True)]],
                       colWidths=[2.4 * inch, 1.0 * inch]),
                Table([[cell(r_lbl, bold=r_bold),
                        cell(r_val, bold=r_bold, align_right=True)]],
                       colWidths=[2.0 * inch, 1.0 * inch]),
            ]
            rows.append(row)
        return rows

    two_col_data = _build_two_col(
        collectable_rows, collected_rows,
        header_l="COLLECTABLE", header_r="COLLECTED",
    )
    two_col = Table(two_col_data, colWidths=[3.6 * inch, 3.2 * inch])
    two_col.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D0E0")),
        ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.HexColor("#C8D0E0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(two_col)

    # Verification line
    story.append(Spacer(1, 8))
    var = data["verification"]["variance_cents"]
    if var == 0:
        verify_text = f'<font color="{SUCCESS}"><b>✓ VERIFIED</b>  Collectable matches Collected ({_money(data["verification"]["collectable_cents"])})</font>'
    else:
        verify_text = f'<font color="{DANGER}"><b>⚠ VARIANCE</b>  ${var/100:+.2f}  (Collected − Collectable)</font>'
    story.append(Paragraph(verify_text, ParagraphStyle("verify", parent=body, alignment=TA_CENTER, fontSize=11)))

    # ─── PAGE 3 — Charts ──────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("CHARTS", h2))

    # Daily sales bar
    bar_png = chart_daily_sales_bar(data["daily"])
    story.append(Image(BytesIO(bar_png), width=7 * inch, height=3 * inch))
    story.append(Spacer(1, 8))

    # Department pie
    pie_png = chart_dept_pie(data["departments"])
    story.append(Image(BytesIO(pie_png), width=5.5 * inch, height=3.5 * inch, hAlign="CENTER"))
    story.append(Spacer(1, 8))

    # Payment split bar
    pay_png = chart_payment_split_bar(data["payment"])
    story.append(Image(BytesIO(pay_png), width=5.5 * inch, height=2.8 * inch, hAlign="CENTER"))

    # ─── PAGE 4 — BC Tax Summary (for accountant) ─────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("BC CANADA TAX SUMMARY (For Accountant)", h2))
    story.append(Paragraph(
        f"Period: {period[0]} to {period[1]}  ·  {data['txn_count']} transactions",
        sub,
    ))
    story.append(Spacer(1, 6))

    # Compute taxable / non-taxable subtotals from items where possible
    taxable_subtotal = 0
    non_taxable_subtotal = 0
    for t_row in db.conn().execute(
        """SELECT t.id FROM transactions t
           WHERE t.status = 'completed'
             AND date(t.created_at, 'localtime') BETWEEN ? AND ?""",
        (period[0], period[1]),
    ).fetchall():
        full = db.get_transaction(t_row["id"])
        for it in full["items"]:
            if it.get("gst_cents", 0) > 0 or it.get("pst_cents", 0) > 0:
                taxable_subtotal += it["unit_price_cents"] * it["quantity"] - it.get("deal_discount_cents", 0)
            else:
                non_taxable_subtotal += it["unit_price_cents"] * it["quantity"] - it.get("deal_discount_cents", 0)

    tax_sections = [
        ("For GST/HST Return", [
            ("Taxable Sales (subject to GST)", _money(taxable_subtotal)),
            ("GST Collected (5%)",             _money(summ["gst_cents"])),
            ("",                                ""),
            ("Net Remittable GST",             _money(summ["gst_cents"])),
        ]),
        ("For PST Return (BC)", [
            ("Sales subject to PST (7%)",      _money(summ["pst_cents"] * 100 // 7 if summ["pst_cents"] else 0)),
            ("PST Collected (7%)",             _money(summ["pst_cents"])),
            ("",                                ""),
            ("Net Remittable PST",             _money(summ["pst_cents"])),
        ]),
        ("Bottle Deposits (separate remittance)", [
            ("Total bottle deposits collected", _money(data["deposits_cents"])),
        ]),
        ("Sales Composition", [
            ("Total taxable sales",            _money(taxable_subtotal)),
            ("Total non-taxable sales",        _money(non_taxable_subtotal)),
            ("Total bag charges (GST+PST)",    _money(data["bag_charges_cents"])),
        ]),
    ]

    for title, rows in tax_sections:
        story.append(Paragraph(title, h2))
        body_rows = [[Paragraph(left, body), Paragraph(right, body_r)] for left, right in rows]
        tbl = Table(body_rows, colWidths=[4.5 * inch, 1.5 * inch], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D0E0")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        sub,
    ))

    doc.build(story)
    log.info("period report PDF: %s", out)
    return out


def render_comparison_pdf(
    cmp_data: dict,
    *,
    store: dict,
    out_dir: Path = EXPORTS_DIR,
) -> Path:
    """Side-by-side comparison PDF: Period A vs Period B."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from io import BytesIO

    a, b = cmp_data["a"], cmp_data["b"]
    metrics = cmp_data["metrics"]
    dept_deltas = cmp_data["dept_deltas"]

    out_dir.mkdir(parents=True, exist_ok=True)
    a_label = f"{a['period'][0]}_to_{a['period'][1]}"
    b_label = f"{b['period'][0]}_to_{b['period'][1]}"
    out = out_dir / f"comparison_{a_label}_vs_{b_label}.pdf"

    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.4 * inch,
        title=f"Comparison Report",
    )

    s = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=s["Heading1"],
                        textColor=colors.HexColor(NAVY),
                        fontSize=18, alignment=TA_CENTER)
    h2 = ParagraphStyle("h2", parent=s["Heading2"],
                        textColor=colors.HexColor(NAVY),
                        fontSize=13, spaceBefore=10, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=s["Normal"], alignment=TA_CENTER,
                         fontSize=10, textColor=colors.HexColor("#7F8C8D"))
    body = s["Normal"]
    body_r = ParagraphStyle("body_r", parent=s["Normal"], alignment=TA_RIGHT)

    story = [
        Paragraph(store.get("name", "CityLink Convenience"), h1),
        Paragraph("PERIOD COMPARISON REPORT", h2),
        Paragraph(
            f"<b>Period A:</b> {a['period'][0]} to {a['period'][1]}<br/>"
            f"<b>Period B:</b> {b['period'][0]} to {b['period'][1]}",
            ParagraphStyle("p", parent=body, alignment=TA_CENTER),
        ),
        Spacer(1, 8),
    ]

    # Metric comparison table
    def diff_cell(delta_cents: int, pct: float) -> str:
        if delta_cents > 0:
            return f'<font color="{SUCCESS}">+{_money(delta_cents)} ({pct:+.1f}%)</font>'
        if delta_cents < 0:
            return f'<font color="{DANGER}">{_money(delta_cents)} ({pct:+.1f}%)</font>'
        return "—"

    def metric_row(label: str, a_val: int, b_val: int, delta_pair: tuple[int, float]):
        return [
            Paragraph(label, body),
            Paragraph(_money(a_val), body_r),
            Paragraph(_money(b_val), body_r),
            Paragraph(diff_cell(*delta_pair), body_r),
        ]

    rows = [[
        Paragraph("<b>Metric</b>", body),
        Paragraph("<b>Period A</b>", body_r),
        Paragraph("<b>Period B</b>", body_r),
        Paragraph("<b>Δ ($, %)</b>", body_r),
    ]]
    rows.append(metric_row("Gross Sales", a["summary"]["gross_sales_cents"],
                           b["summary"]["gross_sales_cents"], metrics["gross_sales"]))
    rows.append(metric_row("Net Sales", a["summary"]["net_sales_cents"],
                           b["summary"]["net_sales_cents"], metrics["net_sales"]))
    rows.append(metric_row("GST", a["summary"]["gst_cents"],
                           b["summary"]["gst_cents"], metrics["gst"]))
    rows.append(metric_row("PST", a["summary"]["pst_cents"],
                           b["summary"]["pst_cents"], metrics["pst"]))
    rows.append(metric_row("Discounts", a["summary"]["discounts_cents"],
                           b["summary"]["discounts_cents"], metrics["discounts"]))
    rows.append(metric_row("Lottery Net", a["lottery"]["net_cents"],
                           b["lottery"]["net_cents"], metrics["lottery_net"]))
    rows.append(metric_row("Cash", a["payment"]["cash_received_cents"],
                           b["payment"]["cash_received_cents"], metrics["cash"]))
    rows.append(metric_row("Card", a["payment"]["card_cents"],
                           b["payment"]["card_cents"], metrics["card"]))
    # Txn count (as integer not money)
    delta_n, pct_n = metrics["txn_count"]
    rows.append([
        Paragraph("Transaction Count", body),
        Paragraph(str(a["txn_count"]), body_r),
        Paragraph(str(b["txn_count"]), body_r),
        Paragraph(
            f'<font color="{SUCCESS if delta_n>0 else DANGER if delta_n<0 else "#000"}">{delta_n:+d} ({pct_n:+.1f}%)</font>',
            body_r,
        ),
    ])

    tbl = Table(rows, colWidths=[2.0*inch, 1.2*inch, 1.2*inch, 2.2*inch], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D0E0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)

    # Per-dept comparison
    story.append(Spacer(1, 12))
    story.append(Paragraph("Department Breakdown (A vs B)", h2))
    drows = [[
        Paragraph("<b>Department</b>", body),
        Paragraph("<b>A</b>", body_r),
        Paragraph("<b>B</b>", body_r),
        Paragraph("<b>Δ</b>", body_r),
    ]]
    for dept, (delta_c, pct_c) in sorted(
        dept_deltas.items(), key=lambda kv: -abs(kv[1][0])
    ):
        a_v = a["departments"].get(dept, {"total": 0})["total"]
        b_v = b["departments"].get(dept, {"total": 0})["total"]
        drows.append([
            Paragraph(dept, body),
            Paragraph(_money(a_v), body_r),
            Paragraph(_money(b_v), body_r),
            Paragraph(diff_cell(delta_c, pct_c), body_r),
        ])
    dtbl = Table(drows, colWidths=[2.0*inch, 1.2*inch, 1.2*inch, 2.2*inch], hAlign="LEFT")
    dtbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D0E0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(dtbl)

    # Comparison chart
    story.append(PageBreak())
    story.append(Paragraph("Comparison Chart", h2))
    cmp_png = chart_comparison_bar(a, b, labels=("A", "B"))
    story.append(Image(BytesIO(cmp_png), width=7 * inch, height=3.5 * inch))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        sub,
    ))

    doc.build(story)
    log.info("comparison PDF: %s", out)
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
