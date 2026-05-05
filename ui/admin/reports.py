"""Admin reports screen.

Lists available reports + date-range picker. Each report → core/reports.py
collector + render_generic_pdf or render_eod_pdf, then opens in OS viewer.

Reports supported:
  - Tax Summary (period)
  - Lottery (period)
  - Best Sellers (period, top 20)
  - Void Log (period)
  - Cashier Performance (period)
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import db, reports
from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.reports")

EXPORTS_DIR = Path("exports")


class AdminReportsScreen(QWidget):
    """Self-contained reports panel. Mountable inside the admin dashboard."""

    back_requested = pyqtSignal()

    def __init__(self, store: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("admin_reports")
        self.store = store
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(10)

        title = QLabel("REPORTS")
        title.setObjectName("admin_reports_title")
        f = QFont(styles.FONT_FAMILY, 22); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        root.addWidget(title)

        # Quick range buttons
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_row.addWidget(QLabel("Quick range:"))
        for label, name in [
            ("Today",       "rep_q_today"),
            ("Yesterday",   "rep_q_yesterday"),
            ("This Week",   "rep_q_this_week"),
            ("This Month",  "rep_q_this_month"),
            ("Last Month",  "rep_q_last_month"),
            ("This Year",   "rep_q_this_year"),
            ("Last Year",   "rep_q_last_year"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(34)
            b.clicked.connect(lambda _ck=False, n=name: self._set_quick(n))
            quick_row.addWidget(b)
        quick_row.addStretch(1)
        root.addLayout(quick_row)

        # Date range row
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(QLabel("From:"))
        self._start = QDateEdit(QDate.currentDate().addDays(-7))
        self._start.setObjectName("rep_start_date")
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        date_row.addWidget(self._start)

        date_row.addWidget(QLabel("To:"))
        self._end = QDateEdit(QDate.currentDate())
        self._end.setObjectName("rep_end_date")
        self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd")
        date_row.addWidget(self._end)

        date_row.addStretch(1)
        root.addLayout(date_row)

        # Comparison-mode toggle
        comp_row = QHBoxLayout()
        comp_row.setSpacing(8)
        self._comp_check = QPushButton("Comparison mode: OFF")
        self._comp_check.setObjectName("rep_comp_toggle")
        self._comp_check.setCheckable(True)
        self._comp_check.toggled.connect(self._on_comp_toggle)
        comp_row.addWidget(self._comp_check)

        comp_row.addWidget(QLabel("  Period B: "))
        self._b_start = QDateEdit(QDate.currentDate().addDays(-30))
        self._b_start.setObjectName("rep_b_start_date")
        self._b_start.setCalendarPopup(True)
        self._b_start.setDisplayFormat("yyyy-MM-dd")
        self._b_start.setEnabled(False)
        comp_row.addWidget(self._b_start)
        comp_row.addWidget(QLabel("→"))
        self._b_end = QDateEdit(QDate.currentDate().addDays(-23))
        self._b_end.setObjectName("rep_b_end_date")
        self._b_end.setCalendarPopup(True)
        self._b_end.setDisplayFormat("yyyy-MM-dd")
        self._b_end.setEnabled(False)
        comp_row.addWidget(self._b_end)
        comp_row.addStretch(1)
        root.addLayout(comp_row)

        # Sales-report buttons (Visual-Touch style detailed reports)
        sales_label = QLabel("Detailed Sales Reports")
        slf = QFont(styles.FONT_FAMILY, 12); slf.setBold(True)
        sales_label.setFont(slf)
        sales_label.setStyleSheet(f"color: {styles.COLORS['navy']}; padding-top: 6px;")
        root.addWidget(sales_label)

        sales_grid = QGridLayout()
        sales_grid.setSpacing(8)
        for c in range(5):
            sales_grid.setColumnStretch(c, 1)
        for i, (label, name, color_key, slot) in enumerate([
            ("Daily",      "rep_btn_daily",      "btn_cash",      lambda: self._run_period("Daily")),
            ("Period",     "rep_btn_period",     "btn_hold",      lambda: self._run_period("Period")),
            ("Monthly",    "rep_btn_monthly",    "btn_hold",      lambda: self._run_period("Monthly")),
            ("Yearly",     "rep_btn_yearly",     "btn_hold",      lambda: self._run_period("Yearly")),
            ("Comparison", "rep_btn_comparison", "btn_lottery_s", self._run_comparison),
        ]):
            b = self._mk_btn(label, name, color_key)
            b.clicked.connect(slot)
            sales_grid.addWidget(b, 0, i)
        root.addLayout(sales_grid)

        # Quick reports (existing 5)
        quick_label = QLabel("Quick Reports")
        quick_label.setFont(slf)
        quick_label.setStyleSheet(f"color: {styles.COLORS['navy']}; padding-top: 6px;")
        root.addWidget(quick_label)

        grid = QGridLayout()
        grid.setSpacing(8)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        report_buttons = [
            ("Tax Summary",         "rep_btn_tax",      "btn_card",       self._run_tax_summary),
            ("Lottery",             "rep_btn_lottery",  "btn_lottery_s",  self._run_lottery),
            ("Best Sellers (Top 20)", "rep_btn_best",   "btn_cash",       self._run_best_sellers),
            ("Void Log",            "rep_btn_void",     "btn_void",       self._run_void_log),
            ("Cashier Performance", "rep_btn_cashier",  "btn_hold",       self._run_cashier_perf),
        ]
        for i, (label, name, color_key, slot) in enumerate(report_buttons):
            r, c = divmod(i, 3)
            b = self._mk_btn(label, name, color_key)
            b.clicked.connect(slot)
            grid.addWidget(b, r, c)
        root.addLayout(grid)
        root.addStretch(1)

        # Back button
        back = QPushButton("Back")
        back.setObjectName("admin_reports_back")
        back.setMinimumHeight(48)
        bf = QFont(styles.FONT_FAMILY, 12); bf.setBold(True)
        back.setFont(bf)
        back.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_void']}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 24px; }}"
        )
        back.clicked.connect(self.back_requested.emit)
        root.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _mk_btn(text: str, name: str, color_key: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumHeight(72)
        f = QFont(styles.FONT_FAMILY, 13); f.setBold(True)
        b.setFont(f)
        color = styles.COLORS[color_key]
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: none; border-radius: 8px; padding: 12px; }}"
        )
        return b

    # ─── Date helpers ────────────────────────────────────────────────────────

    def _set_quick(self, name: str) -> None:
        today = QDate.currentDate()
        if name == "rep_q_today":
            s = e = today
        elif name == "rep_q_yesterday":
            y = today.addDays(-1); s = e = y
        elif name == "rep_q_this_week":
            # Monday of this week (ISO)
            s = today.addDays(-(today.dayOfWeek() - 1))
            e = today
        elif name == "rep_q_this_month":
            s = QDate(today.year(), today.month(), 1)
            e = today
        elif name == "rep_q_last_month":
            first_this = QDate(today.year(), today.month(), 1)
            last_prev = first_this.addDays(-1)
            s = QDate(last_prev.year(), last_prev.month(), 1)
            e = last_prev
        elif name == "rep_q_this_year":
            s = QDate(today.year(), 1, 1)
            e = today
        elif name == "rep_q_last_year":
            s = QDate(today.year() - 1, 1, 1)
            e = QDate(today.year() - 1, 12, 31)
        else:
            return
        self._start.setDate(s); self._end.setDate(e)

    def _on_comp_toggle(self, checked: bool) -> None:
        self._comp_check.setText(
            "Comparison mode: ON" if checked else "Comparison mode: OFF"
        )
        self._b_start.setEnabled(checked)
        self._b_end.setEnabled(checked)

    def _period(self) -> tuple[str, str]:
        return (self._start.date().toString("yyyy-MM-dd"),
                self._end.date().toString("yyyy-MM-dd"))

    def _period_b(self) -> tuple[str, str]:
        return (self._b_start.date().toString("yyyy-MM-dd"),
                self._b_end.date().toString("yyyy-MM-dd"))

    # ─── Report runners ──────────────────────────────────────────────────────

    def _run_period(self, report_type: str) -> None:
        s, e = self._period()
        try:
            data = reports.collect_period_report(s, e)
            pdf = reports.render_period_report_pdf(
                data, store=self.store, report_type=report_type,
            )
            self._info(f"{report_type} report saved.\n{pdf.name}")
            self._open_file(pdf)
        except Exception:
            log.exception("period report '%s' failed", report_type)
            self._error(f"Failed to render {report_type} report.")

    def _run_comparison(self) -> None:
        if not self._comp_check.isChecked():
            self._info("Enable Comparison mode and pick Period B first.")
            return
        a_s, a_e = self._period()
        b_s, b_e = self._period_b()
        try:
            cmp_data = reports.collect_comparison_report(a_s, a_e, b_s, b_e)
            pdf = reports.render_comparison_pdf(cmp_data, store=self.store)
            self._info(f"Comparison report saved.\n{pdf.name}")
            self._open_file(pdf)
        except Exception:
            log.exception("comparison report failed")
            self._error("Failed to render comparison report.")

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
        self._render_and_open("tax_summary", "Tax Summary", sections, period=(s, e))

    def _run_lottery(self) -> None:
        s, e = self._period()
        data = reports.collect_lottery(s, e)
        sections = [
            ("Lottery Totals", [
                ("Sales",   reports._money(data["sales_cents"])),
                ("Payouts", reports._money(data["payouts_cents"])),
                ("Net",     reports._money(data["net_cents"])),
            ]),
        ]
        self._render_and_open("lottery", "Lottery Report", sections, period=(s, e))

    def _run_best_sellers(self) -> None:
        s, e = self._period()
        rows = reports.collect_best_sellers(s, e, limit=20)
        if not rows:
            self._info("No sales in selected period.")
            return
        sections = [
            ("Top 20 Items", [
                (f"{r['name']}  ({r['qty']}x)",
                 reports._money(r["total_cents"]))
                for r in rows
            ]),
        ]
        self._render_and_open("best_sellers", "Best Sellers", sections, period=(s, e))

    def _run_void_log(self) -> None:
        s, e = self._period()
        rows = reports.collect_void_log(s, e)
        if not rows:
            self._info("No voids in selected period.")
            return
        sections = [
            ("Voided Transactions", [
                (f"#{r['id']}  ·  {r.get('transaction_ref') or '?'}  ·  by {r['authorized_by'] or '?'}  ·  {r['created_at']}",
                 reports._money(r["amount_cents"]))
                for r in rows
            ]),
        ]
        self._render_and_open("void_log", "Void Log", sections, period=(s, e))

    def _run_cashier_perf(self) -> None:
        s, e = self._period()
        rows = reports.collect_cashier_performance(s, e)
        if not rows:
            self._info("No cashier activity in selected period.")
            return
        sections = [
            ("Cashier Performance", [
                (f"{r['cashier_name']}  ·  {r['txn_count']} txns",
                 reports._money(r["gross_cents"]))
                for r in rows
            ]),
        ]
        self._render_and_open("cashier_perf", "Cashier Performance",
                              sections, period=(s, e))

    # ─── Render + open ───────────────────────────────────────────────────────

    def _render_and_open(self, slug: str, title: str,
                        sections: list[tuple[str, list[tuple]]],
                        *, period: tuple[str, str]) -> None:
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            out = EXPORTS_DIR / f"{slug}_{period[0]}_to_{period[1]}.pdf"
            reports.render_generic_pdf(
                title, sections, store=self.store, out_path=out, period=period,
            )
            self._info(f"{title} saved.\n{out.name}")
            self._open_file(out)
        except Exception:
            log.exception("report '%s' failed", title)
            self._error(f"Failed to render {title}.")

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

    # ─── Dialogs ─────────────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "Reports", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Reports", msg)
