"""Admin reports screen — preview-first workflow.

Flow:
  1. Pick period (quick range buttons or custom dates).
  2. Click a report type — preview renders inline.
  3. Choose action: Print Receipt / Export PDF / Save Report / Close Preview.

Reports are built from `core/reports.py` collectors. Three rendering paths
keep concerns separate:
  - `_build_sections(...)`     → structured (label, value) data
  - `_format_preview(...)`     → on-screen monospace text
  - `_format_receipt_text(...)` → thermal printer text (58mm/80mm widths)
  - `reports.render_generic_pdf` → branded PDF
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import db, reports
from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.reports")

EXPORTS_DIR = Path("exports")

# Receipt printer widths in monospace columns (chars).
RECEIPT_WIDTH_58MM = 32
RECEIPT_WIDTH_80MM = 48


class AdminReportsScreen(QWidget):
    """Admin reports — preview-first, no auto-print."""

    back_requested = pyqtSignal()

    def __init__(self, store: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("admin_inventory")    # reuse premium light bg
        self.store = store
        # Last rendered report — drives Print/Export/Save actions.
        self._last_title: Optional[str] = None
        self._last_sections: Optional[list] = None
        self._last_period: Optional[tuple] = None
        self._last_slug: Optional[str] = None
        self._build_ui()

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(styles.admin_screen_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Reports")
        title.setObjectName("screen_title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back to Home")
        back.setStyleSheet(styles.pill_button_qss("ghost"))
        back.setMinimumHeight(40)
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Quick range pills
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        for label, name in [
            ("Today",       "rep_q_today"),
            ("Yesterday",   "rep_q_yesterday"),
            ("This Week",   "rep_q_this_week"),
            ("This Month",  "rep_q_this_month"),
            ("Last Month",  "rep_q_last_month"),
            ("This Year",   "rep_q_this_year"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(36)
            b.setStyleSheet(styles.pill_button_qss("ghost"))
            b.clicked.connect(lambda _ck=False, n=name: self._set_quick(n))
            quick_row.addWidget(b)
        quick_row.addStretch(1)
        root.addLayout(quick_row)

        # Custom date row
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(QLabel("From:"))
        self._start = QDateEdit(QDate.currentDate().addDays(-7))
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        date_row.addWidget(self._start)
        date_row.addWidget(QLabel("To:"))
        self._end = QDateEdit(QDate.currentDate())
        self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd")
        date_row.addWidget(self._end)
        date_row.addStretch(1)
        root.addLayout(date_row)

        # Body splitter: report buttons (left) + preview (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #E1E4EA; }")

        # Left: report buttons
        left = QFrame()
        left.setObjectName("card")
        left.setStyleSheet(
            "QFrame#card { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 10px; }"
        )
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 14, 16, 14)
        lv.setSpacing(8)

        sales_lbl = QLabel("Sales Reports")
        sales_lbl.setStyleSheet(
            f"color: {styles.COLORS['navy']}; font-weight: bold; font-size: 11pt;"
        )
        lv.addWidget(sales_lbl)
        sales_grid = QGridLayout()
        sales_grid.setSpacing(6)
        for c in range(2):
            sales_grid.setColumnStretch(c, 1)
        for i, (label, slot) in enumerate([
            ("Daily",        lambda: self._run_period("Daily")),
            ("Period",       lambda: self._run_period("Period")),
            ("Monthly",      lambda: self._run_period("Monthly")),
            ("Yearly",       lambda: self._run_period("Yearly")),
        ]):
            b = self._mk_btn(label, "primary"); b.clicked.connect(slot)
            sales_grid.addWidget(b, i // 2, i % 2)
        lv.addLayout(sales_grid)

        quick_lbl = QLabel("Quick Reports")
        quick_lbl.setStyleSheet(
            f"color: {styles.COLORS['navy']}; font-weight: bold;"
            f" font-size: 11pt; padding-top: 6px;"
        )
        lv.addWidget(quick_lbl)
        quick_grid = QGridLayout()
        quick_grid.setSpacing(6)
        for c in range(2):
            quick_grid.setColumnStretch(c, 1)
        for i, (label, slot) in enumerate([
            ("Retail Sales",     self._run_retail_sales),
            ("Lottery",          self._run_lottery),
            ("Tax Summary",      self._run_tax_summary),
            ("Best Sellers",     self._run_best_sellers),
            ("Cashier Perf.",    self._run_cashier_perf),
            ("Department Sales", self._run_dept_sales),
            ("Refund/Void Log",  self._run_void_log),
            ("Hourly Sales",     self._run_hourly_sales),
        ]):
            b = self._mk_btn(label, "ghost"); b.clicked.connect(slot)
            quick_grid.addWidget(b, i // 2, i % 2)
        lv.addLayout(quick_grid)

        complete = self._mk_btn("Complete Store Report", "success")
        complete.setMinimumHeight(50)
        complete.clicked.connect(self._run_complete_store)
        lv.addWidget(complete)
        lv.addStretch(1)

        splitter.addWidget(left)

        # Right: preview pane
        right = QFrame()
        right.setObjectName("card")
        right.setStyleSheet(left.styleSheet())
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 14, 16, 14)
        rv.setSpacing(8)

        # Action bar (Print Receipt / Export PDF / Save / Close)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._title_lbl = QLabel("Live Preview")
        tlf = QFont(styles.FONT_FAMILY, 13); tlf.setBold(True)
        self._title_lbl.setFont(tlf)
        self._title_lbl.setStyleSheet(f"color: {styles.COLORS['navy']};")
        action_row.addWidget(self._title_lbl)
        action_row.addStretch(1)

        self._receipt_width = QComboBox()
        self._receipt_width.addItem("58mm (32 col)", RECEIPT_WIDTH_58MM)
        self._receipt_width.addItem("80mm (48 col)", RECEIPT_WIDTH_80MM)
        self._receipt_width.setCurrentIndex(1)
        self._receipt_width.setMinimumHeight(34)
        self._receipt_width.setStyleSheet(styles.premium_combo_qss())
        action_row.addWidget(self._receipt_width)

        for label, variant, slot in [
            ("Print Receipt", "primary", self._on_print_receipt),
            ("Export PDF",    "primary", self._on_export_pdf),
            ("Save Report",   "ghost",   self._on_save_report),
            ("Close",         "ghost",   self._on_close_preview),
        ]:
            b = QPushButton(label)
            b.setMinimumHeight(36)
            b.setStyleSheet(styles.pill_button_qss(variant))
            b.clicked.connect(slot)
            action_row.addWidget(b)
        rv.addLayout(action_row)

        # Preview text area (monospace, read-only)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setObjectName("report_preview")
        mono = QFont("Menlo", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._preview.setFont(mono)
        self._preview.setStyleSheet(
            "QPlainTextEdit { background: #FAFBFC; color: #1A1A1A;"
            " border: 1px solid #E1E4EA; border-radius: 8px; padding: 12px; }"
        )
        self._preview.setPlainText(
            "Select a date range and a report type to load a live preview here.\n\n"
            "Choose Print Receipt, Export PDF, or Save Report after preview."
        )
        rv.addWidget(self._preview, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([320, 880])
        root.addWidget(splitter, stretch=1)

    @staticmethod
    def _mk_btn(text: str, variant: str = "primary") -> QPushButton:
        b = QPushButton(text)
        b.setMinimumHeight(40)
        b.setStyleSheet(styles.pill_button_qss(variant))
        return b

    # ─── Date helpers ──────────────────────────────────────────────────────

    def _set_quick(self, name: str) -> None:
        today = date.today()
        if name == "rep_q_today":
            s = e = today
        elif name == "rep_q_yesterday":
            s = e = today - timedelta(days=1)
        elif name == "rep_q_this_week":
            s = today - timedelta(days=today.weekday()); e = today
        elif name == "rep_q_this_month":
            s = today.replace(day=1); e = today
        elif name == "rep_q_last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            s = last_prev.replace(day=1); e = last_prev
        elif name == "rep_q_this_year":
            s = today.replace(month=1, day=1); e = today
        else:
            return
        self._start.setDate(QDate(s.year, s.month, s.day))
        self._end.setDate(QDate(e.year, e.month, e.day))

    def _period(self) -> tuple[str, str]:
        return (self._start.date().toString("yyyy-MM-dd"),
                self._end.date().toString("yyyy-MM-dd"))

    # ─── Report runners ────────────────────────────────────────────────────

    def _run_period(self, report_type: str) -> None:
        s, e = self._period()
        try:
            data = reports.collect_period_report(s, e)
        except Exception:
            log.exception("period collect failed")
            self._error("Failed to compute period report."); return
        sections = self._build_period_sections(data, report_type)
        self._show_preview(f"{report_type} Report", sections, s, e,
                           slug=f"{report_type.lower()}_period",
                           pdf_renderer=lambda store, out: reports.render_period_report_pdf(
                               data, store=store, report_type=report_type,
                           ))

    def _run_retail_sales(self) -> None:
        s, e = self._period()
        try:
            data = reports.collect_period_report(s, e)
        except Exception:
            log.exception("retail collect failed")
            self._error("Failed to compute retail sales."); return
        sections = [
            ("Retail Totals", [
                ("Transactions",    str(data.get("txn_count", 0))),
                ("Items sold",      str(data.get("item_count", 0))),
                ("Gross sales",     reports._money(data.get("gross_cents", 0))),
                ("Discounts",       reports._money(data.get("discount_cents", 0))),
                ("Net sales",       reports._money(data.get("net_cents", 0))),
            ]),
        ]
        self._show_preview("Retail Sales", sections, s, e, slug="retail_sales")

    def _run_lottery(self) -> None:
        s, e = self._period()
        data = reports.collect_lottery(s, e)
        sections = [
            ("Lottery Totals", [
                ("Sales",    reports._money(data["sales_cents"])),
                ("Payouts",  reports._money(data["payouts_cents"])),
                ("Net",      reports._money(data["net_cents"])),
            ]),
        ]
        self._show_preview("Lottery Report", sections, s, e, slug="lottery")

    def _run_tax_summary(self) -> None:
        s, e = self._period()
        data = reports.collect_tax_summary(s, e)
        sections = [
            ("Period Totals", [
                ("Transactions",   str(data["txn_count"])),
                ("Gross sales",    reports._money(data["gross_cents"])),
                ("GST (5%)",       reports._money(data["gst_cents"])),
                ("PST (7%)",       reports._money(data["pst_cents"])),
                ("Bottle deposit", reports._money(data["deposit_cents"])),
                ("Bag charges",    reports._money(data["bag_cents"])),
            ]),
        ]
        self._show_preview("Tax Summary", sections, s, e, slug="tax_summary")

    def _run_best_sellers(self) -> None:
        s, e = self._period()
        rows = reports.collect_best_sellers(s, e, limit=20)
        if not rows:
            self._info("No sales in selected period."); return
        sections = [
            ("Top 20 Items", [
                (f"{r['name']}  ({r['qty']}x)", reports._money(r["total_cents"]))
                for r in rows
            ]),
        ]
        self._show_preview("Best Sellers", sections, s, e, slug="best_sellers")

    def _run_cashier_perf(self) -> None:
        s, e = self._period()
        rows = reports.collect_cashier_performance(s, e)
        if not rows:
            self._info("No cashier activity in selected period."); return
        sections = [
            ("Cashier Performance", [
                (f"{r['cashier_name']}  ·  {r['txn_count']} txns",
                 reports._money(r["gross_cents"]))
                for r in rows
            ]),
        ]
        self._show_preview("Cashier Performance", sections, s, e, slug="cashier_perf")

    def _run_dept_sales(self) -> None:
        s, e = self._period()
        try:
            data = reports.collect_period_report(s, e)
        except Exception:
            log.exception("dept collect failed")
            self._error("Failed to compute department sales."); return
        depts = data.get("dept_totals") or {}
        if not depts:
            self._info("No department activity in selected period."); return
        rows = sorted(depts.items(),
                      key=lambda kv: -kv[1].get("total_cents", 0))
        sections = [
            ("Department Sales", [
                (k.title(), reports._money(v.get("total_cents", 0)))
                for k, v in rows
            ]),
        ]
        self._show_preview("Department Sales", sections, s, e, slug="dept_sales")

    def _run_void_log(self) -> None:
        s, e = self._period()
        rows = reports.collect_void_log(s, e)
        if not rows:
            self._info("No voids in selected period."); return
        sections = [
            ("Voided Transactions", [
                (f"#{r['id']}  ·  {r.get('transaction_ref') or '?'}  ·"
                 f"  by {r['authorized_by'] or '?'}  ·  {r['created_at']}",
                 reports._money(r["amount_cents"]))
                for r in rows
            ]),
        ]
        self._show_preview("Refund / Void Log", sections, s, e, slug="void_log")

    def _run_hourly_sales(self) -> None:
        s, e = self._period()
        try:
            txns = db.list_transactions_in_range(
                f"{s} 00:00:00", f"{e} 23:59:59",
            ) if hasattr(db, "list_transactions_in_range") else []
        except Exception:
            log.exception("hourly fetch failed")
            txns = []
        if not txns:
            self._info("No transactions in selected period."); return
        bucket: dict[int, dict] = {}
        for t in txns:
            try:
                hr = int((t.get("created_at") or "00:00")[11:13])
            except Exception:
                continue
            b = bucket.setdefault(hr, {"n": 0, "total": 0})
            b["n"] += 1
            b["total"] += int(t.get("rounded_total_cents", 0) or 0)
        sections = [
            ("Hourly Sales", [
                (f"{hr:02d}:00 — {hr:02d}:59  ·  {b['n']} txns",
                 reports._money(b["total"]))
                for hr, b in sorted(bucket.items())
            ]),
        ]
        self._show_preview("Hourly Sales", sections, s, e, slug="hourly_sales")

    def _run_complete_store(self) -> None:
        """Manager master report — aggregates every metric for the period."""
        s, e = self._period()
        sections: list = []
        try:
            tax = reports.collect_tax_summary(s, e)
            sections.append(("Tax & Sales", [
                ("Transactions",   str(tax["txn_count"])),
                ("Gross sales",    reports._money(tax["gross_cents"])),
                ("GST (5%)",       reports._money(tax["gst_cents"])),
                ("PST (7%)",       reports._money(tax["pst_cents"])),
                ("Deposit",        reports._money(tax["deposit_cents"])),
                ("Bag charges",    reports._money(tax["bag_cents"])),
            ]))
        except Exception:
            log.exception("complete: tax failed")
        try:
            lot = reports.collect_lottery(s, e)
            sections.append(("Lottery", [
                ("Sales",   reports._money(lot["sales_cents"])),
                ("Payouts", reports._money(lot["payouts_cents"])),
                ("Net",     reports._money(lot["net_cents"])),
            ]))
        except Exception:
            log.exception("complete: lottery failed")
        try:
            best = reports.collect_best_sellers(s, e, limit=10)
            if best:
                sections.append(("Top 10 Items", [
                    (f"{r['name']}  ({r['qty']}x)",
                     reports._money(r["total_cents"]))
                    for r in best
                ]))
        except Exception:
            log.exception("complete: best failed")
        try:
            cash = reports.collect_cashier_performance(s, e)
            if cash:
                sections.append(("Cashier Performance", [
                    (f"{r['cashier_name']}  ·  {r['txn_count']} txns",
                     reports._money(r["gross_cents"]))
                    for r in cash
                ]))
        except Exception:
            log.exception("complete: cashier failed")
        try:
            voids = reports.collect_void_log(s, e)
            if voids:
                sections.append(("Voids / Refunds", [
                    (f"#{r['id']}  ·  by {r['authorized_by'] or '?'}",
                     reports._money(r["amount_cents"]))
                    for r in voids
                ]))
        except Exception:
            log.exception("complete: voids failed")
        if not sections:
            self._info("No data in selected period."); return
        self._show_preview("Complete Store Report", sections, s, e,
                           slug="complete_store")

    # ─── Period section builder ────────────────────────────────────────────

    @staticmethod
    def _build_period_sections(data: dict, report_type: str) -> list:
        secs = [
            ("Sales Summary", [
                ("Type",            report_type),
                ("Transactions",    str(data.get("txn_count", 0))),
                ("Items sold",      str(data.get("item_count", 0))),
                ("Gross sales",     reports._money(data.get("gross_cents", 0))),
                ("Discounts",       reports._money(data.get("discount_cents", 0))),
                ("Net sales",       reports._money(data.get("net_cents", 0))),
                ("GST",             reports._money(data.get("gst_cents", 0))),
                ("PST",             reports._money(data.get("pst_cents", 0))),
                ("Deposit",         reports._money(data.get("deposit_cents", 0))),
            ]),
        ]
        depts = data.get("dept_totals") or {}
        if depts:
            secs.append(("Department Totals", [
                (k.title(), reports._money(v.get("total_cents", 0)))
                for k, v in sorted(depts.items(),
                                   key=lambda kv: -kv[1].get("total_cents", 0))
            ]))
        pay = data.get("payment_totals") or {}
        if pay:
            secs.append(("Payments", [
                (k.title(), reports._money(v))
                for k, v in pay.items()
            ]))
        return secs

    # ─── Preview / output ──────────────────────────────────────────────────

    def _show_preview(self, title: str, sections: list,
                      s: str, e: str, *,
                      slug: str = "report",
                      pdf_renderer=None) -> None:
        self._last_title = title
        self._last_sections = sections
        self._last_period = (s, e)
        self._last_slug = slug
        self._last_pdf_renderer = pdf_renderer
        self._title_lbl.setText(title)
        self._preview.setPlainText(self._format_preview(title, sections, s, e))

    @staticmethod
    def _format_preview(title: str, sections: list, s: str, e: str) -> str:
        WIDTH = 64
        lines = []
        lines.append("CITYLINK CONVENIENCE")
        lines.append(title.upper())
        lines.append(f"Period: {s}  →  {e}")
        lines.append("=" * WIDTH)
        for sec_title, rows in sections:
            lines.append("")
            lines.append(sec_title)
            lines.append("-" * WIDTH)
            for label, value in rows:
                pad = WIDTH - len(label) - len(value) - 2
                if pad < 1:
                    lines.append(label)
                    lines.append(value.rjust(WIDTH))
                else:
                    lines.append(f"{label}  {' ' * pad}{value}")
        lines.append("")
        lines.append("=" * WIDTH)
        return "\n".join(lines)

    @staticmethod
    def _format_receipt_text(title: str, sections: list,
                             s: str, e: str, width: int) -> str:
        """Receipt printer view — aligned to thermal column width."""
        lines = []
        lines.append(title.upper().center(width))
        lines.append(("CityLink Convenience").center(width))
        lines.append(f"{s} -> {e}".center(width))
        lines.append("=" * width)
        for sec_title, rows in sections:
            lines.append("")
            lines.append(sec_title)
            lines.append("-" * width)
            for label, value in rows:
                # Wrap long labels; right-align value on last wrapped line.
                wrapped = textwrap.wrap(label, width=width - len(value) - 1) \
                          or [label[:width - len(value) - 1]]
                for ln in wrapped[:-1]:
                    lines.append(ln)
                last = wrapped[-1]
                pad = width - len(last) - len(value)
                if pad < 1:
                    lines.append(last)
                    lines.append(value.rjust(width))
                else:
                    lines.append(f"{last}{' ' * pad}{value}")
        lines.append("=" * width)
        lines.append(("Thank you").center(width))
        return "\n".join(lines)

    # ─── Action handlers ───────────────────────────────────────────────────

    def _on_print_receipt(self) -> None:
        if not self._has_preview():
            return
        width = int(self._receipt_width.currentData())
        text = self._format_receipt_text(
            self._last_title, self._last_sections,
            self._last_period[0], self._last_period[1], width,
        )
        # For now: write to an exports text file + open. Real ESC-POS
        # dispatch wires into core.receipt printer driver later.
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            out = EXPORTS_DIR / f"{self._last_slug}_receipt.txt"
            out.write_text(text, encoding="utf-8")
            self._info(f"Receipt-format text saved.\n{out.name}")
            self._open_file(out)
        except Exception:
            log.exception("receipt print failed")
            self._error("Failed to render receipt.")

    def _on_export_pdf(self) -> None:
        if not self._has_preview():
            return
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            out = (EXPORTS_DIR /
                   f"{self._last_slug}_{self._last_period[0]}_to_{self._last_period[1]}.pdf")
            if self._last_pdf_renderer is not None:
                pdf = self._last_pdf_renderer(self.store, out)
                if isinstance(pdf, Path):
                    out = pdf
            else:
                reports.render_generic_pdf(
                    self._last_title, self._last_sections,
                    store=self.store, out_path=out,
                    period=self._last_period,
                )
            self._info(f"{self._last_title} PDF saved.\n{out.name}")
            self._open_file(out)
        except Exception:
            log.exception("export pdf failed")
            self._error("Failed to export PDF.")

    def _on_save_report(self) -> None:
        if not self._has_preview():
            return
        default = (
            EXPORTS_DIR / f"{self._last_slug}_{self._last_period[0]}_to_{self._last_period[1]}.txt"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", str(default), "Text (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self._preview.toPlainText(), encoding="utf-8")
            self._info(f"Report saved.\n{path}")
        except Exception:
            log.exception("save text failed")
            self._error("Failed to save report.")

    def _on_close_preview(self) -> None:
        self._last_title = self._last_sections = self._last_period = None
        self._last_slug = None
        self._last_pdf_renderer = None
        self._title_lbl.setText("Live Preview")
        self._preview.setPlainText(
            "Select a date range and a report type to load a live preview here."
        )

    def _has_preview(self) -> bool:
        if self._last_sections is None:
            self._info("Run a report first to load a preview.")
            return False
        return True

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _open_file(self, path: Path) -> None:
        try:
            p = str(path)
            if sys.platform == "darwin":
                subprocess.Popen(["open", p])
            elif sys.platform == "win32":
                import os as _os
                _os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:
            log.exception("could not open %s", path)

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "Reports", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Reports", msg)
