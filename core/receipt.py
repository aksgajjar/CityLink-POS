"""Receipt rendering: ESC/POS thermal + PDF fallback.

Two backends:
  - print_to_thermal(txn, ...) → tries python-escpos USB printer, returns True/False
  - render_pdf(txn, ...)        → always works, writes to exports/receipt_<ref>.pdf

`print_receipt(txn, prefer_thermal=...)` picks the right one and returns the path
of the artifact (pdf path) or the ESC/POS device tag.

Layout: 32-char monospace column for thermal width (80mm @ 12cpi).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.models import Transaction

log = get_logger("receipt")

EXPORTS_DIR = Path("exports")
RECEIPT_WIDTH = 32   # chars per line on 80mm thermal


# ─── Plain-text rendering (used by both backends) ────────────────────────────

def render_text(
    txn: Transaction,
    *,
    store_name: str,
    store_address: str = "",
    cashier_name: str = "",
    location_id: str = "",
) -> str:
    """Build the receipt as a plain string (32-char width, line-broken)."""
    W = RECEIPT_WIDTH
    out: list[str] = []

    def center(s: str) -> str:
        s = s[:W]
        pad = (W - len(s)) // 2
        return " " * pad + s

    def two_col(left: str, right: str) -> str:
        right = right or ""
        avail = W - len(right)
        if avail < 1:
            return (left + right)[:W]
        if len(left) > avail - 1:
            left = left[: avail - 2] + "…"
        return left.ljust(avail) + right

    # ── Header ──
    out.append(center(store_name.upper()))
    if store_address:
        out.append(center(store_address))
    if location_id:
        out.append(center(f"Store: {location_id}"))
    out.append("")

    # ── Meta ──
    ts = txn.created_at or datetime.now().strftime("%Y-%m-%d %I:%M %p")
    out.append(f"Date: {ts}")
    if cashier_name:
        out.append(f"Cashier: {cashier_name}")
    out.append(f"Ref:  {txn.transaction_ref}")
    out.append("-" * W)

    # ── Items ──
    if not txn.items:
        out.append(center("(no items)"))
    for it in txn.items:
        line_total = f"${it.line_total_cents / 100:.2f}"
        if it.quantity > 1:
            qty_unit = f"  {it.quantity} x ${it.unit_price_cents / 100:.2f}"
            out.append(it.name[:W])
            out.append(two_col(qty_unit, line_total))
        else:
            out.append(two_col(it.name, line_total))
        if it.deal_discount_cents > 0:
            out.append(two_col("  Deal saved", f"-${it.deal_discount_cents / 100:.2f}"))

    out.append("-" * W)

    # ── Tax breakdown ──
    out.append(two_col("Subtotal", f"${txn.subtotal_cents / 100:.2f}"))
    if txn.discount_cents:
        out.append(two_col("Discount", f"-${txn.discount_cents / 100:.2f}"))
    if txn.gst_cents:
        out.append(two_col("GST (5%)", f"${txn.gst_cents / 100:.2f}"))
    if txn.pst_cents:
        out.append(two_col("PST (7%)", f"${txn.pst_cents / 100:.2f}"))
    if txn.deposit_cents:
        out.append(two_col("Deposit", f"${txn.deposit_cents / 100:.2f}"))
    if txn.bag_charge_cents:
        out.append(two_col("Bag charge", f"${txn.bag_charge_cents / 100:.2f}"))

    out.append("=" * W)
    out.append(two_col("TOTAL", f"${txn.total_cents / 100:.2f}"))
    if txn.rounded_total_cents != txn.total_cents:
        out.append(two_col("(Cash rounded)", f"${txn.rounded_total_cents / 100:.2f}"))
    out.append("")

    # ── Payment block ──
    if txn.payment_method == "cash":
        out.append(two_col("PAID BY", "CASH"))
        if txn.cash_tendered_cents:
            out.append(two_col("Tendered", f"${txn.cash_tendered_cents / 100:.2f}"))
        if txn.change_cents:
            out.append(two_col("Change", f"${txn.change_cents / 100:.2f}"))
    elif txn.payment_method == "card":
        out.append(two_col("PAID BY", "CARD"))
        if txn.card_auth_code:
            out.append(two_col("Auth", txn.card_auth_code))
        if txn.card_last4:
            out.append(two_col("Card", f"****{txn.card_last4}"))
    elif txn.payment_method == "split":
        out.append(two_col("PAID BY", "SPLIT"))
        if txn.cash_tendered_cents:
            out.append(two_col("  Cash", f"${txn.cash_tendered_cents / 100:.2f}"))
        if txn.card_amount_cents:
            out.append(two_col("  Card", f"${txn.card_amount_cents / 100:.2f}"))
            if txn.card_last4:
                out.append(two_col("  Card #", f"****{txn.card_last4}"))

    out.append("")

    # ── Lottery summary, if any kind=='lottery' lines present ──
    lottery_lines = [it for it in txn.items if (it.line_total_cents > 0 and it.name and "Lottery" in it.name)]
    if lottery_lines:
        out.append("-" * W)
        out.append(center("LOTTERY"))
        for it in lottery_lines:
            out.append(two_col(it.name, f"${it.line_total_cents / 100:.2f}"))

    out.append("")
    out.append(center("Thank you for shopping!"))
    out.append(center(store_name))
    out.append("")
    return "\n".join(out)


# ─── PDF backend (always-available fallback) ─────────────────────────────────

def render_pdf(
    txn: Transaction,
    *,
    store_name: str,
    store_address: str = "",
    cashier_name: str = "",
    location_id: str = "",
    out_dir: Path = EXPORTS_DIR,
) -> Path:
    """Render receipt as PDF in `out_dir`. Returns the file path."""
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.units import mm

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"receipt_{txn.transaction_ref}.pdf"

    text = render_text(
        txn,
        store_name=store_name,
        store_address=store_address,
        cashier_name=cashier_name,
        location_id=location_id,
    )

    page_w = 80 * mm
    line_h = 11
    margin = 6 * mm

    n_lines = text.count("\n") + 1
    page_h = max(120 * mm, margin * 2 + (n_lines + 4) * line_h)

    c = _canvas.Canvas(str(out), pagesize=(page_w, page_h))
    c.setFont("Courier", 9)

    y = page_h - margin
    for line in text.splitlines():
        c.drawString(margin, y, line)
        y -= line_h
        if y < margin:
            c.showPage()
            c.setFont("Courier", 9)
            y = page_h - margin

    c.save()
    log.info("PDF receipt saved: %s", out)
    return out


# ─── ESC/POS thermal backend ─────────────────────────────────────────────────

def print_to_thermal(
    txn: Transaction,
    *,
    store_name: str,
    store_address: str = "",
    cashier_name: str = "",
    location_id: str = "",
    vendor_id: int = 0x04b8,
    product_id: int = 0x0202,
) -> bool:
    """Try to print via python-escpos USB. Returns True on success.

    Caller should fall back to PDF on False. Raises nothing — all errors logged.
    """
    try:
        from escpos.printer import Usb
    except Exception:
        log.warning("escpos not importable")
        return False

    text = render_text(
        txn,
        store_name=store_name,
        store_address=store_address,
        cashier_name=cashier_name,
        location_id=location_id,
    )

    try:
        printer = Usb(vendor_id, product_id)
    except Exception:
        log.warning("escpos USB device not found (%04x:%04x)", vendor_id, product_id)
        return False

    try:
        printer.set(align="left", bold=False, double_height=False)
        for line in text.splitlines():
            printer.text(line + "\n")
        printer.cut()
        printer.cashdraw(2)   # kick drawer (cash sales) — harmless on others
        return True
    except Exception:
        log.exception("thermal print failed")
        return False
    finally:
        try:
            printer.close()
        except Exception:
            pass


# ─── Public entry point ──────────────────────────────────────────────────────

def print_receipt(
    txn: Transaction,
    *,
    store_name: str,
    store_address: str = "",
    cashier_name: str = "",
    location_id: str = "",
    prefer_thermal: bool = False,
    vendor_id: int = 0x04b8,
    product_id: int = 0x0202,
) -> Path:
    """Print the receipt. Tries thermal if requested, always falls back to PDF.

    Returns the PDF path written. (If thermal succeeds, a PDF copy is still
    written for archival — receipt history is one of our reports.)
    """
    if prefer_thermal:
        ok = print_to_thermal(
            txn,
            store_name=store_name,
            store_address=store_address,
            cashier_name=cashier_name,
            location_id=location_id,
            vendor_id=vendor_id,
            product_id=product_id,
        )
        if ok:
            log.info("thermal print OK ref=%s", txn.transaction_ref)
        else:
            log.info("thermal failed → PDF fallback ref=%s", txn.transaction_ref)
    return render_pdf(
        txn,
        store_name=store_name,
        store_address=store_address,
        cashier_name=cashier_name,
        location_id=location_id,
    )
