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
        root.setSpacing(12)

        title = QLabel("REPORTS")
        title.setObjectName("admin_reports_title")
        f = QFont(styles.FONT_FAMILY, 22); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        root.addWidget(title)

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

        # Quick range presets
        for label, days in [("Today", 0), ("7d", 7), ("30d", 30)]:
            b = QPushButton(label)
            b.setObjectName(f"rep_preset_{label.lower()}")
            b.clicked.connect(lambda _ck=False, d=days: self._set_preset(d))
            date_row.addWidget(b)

        date_row.addStretch(1)
        root.addLayout(date_row)

        # Report buttons grid
        grid = QGridLayout()
        grid.setSpacing(10)
        for c in range(3):
            grid.setColumnStretch(c, 1)

        report_buttons = [
            ("Tax Summary",         "rep_btn_tax",      "btn_card",    self._run_tax_summary),
            ("Lottery",             "rep_btn_lottery",  "btn_lottery_s", self._run_lottery),
            ("Best Sellers (Top 20)", "rep_btn_best",   "btn_cash",    self._run_best_sellers),
            ("Void Log",            "rep_btn_void",     "btn_void",    self._run_void_log),
            ("Cashier Performance", "rep_btn_cashier",  "btn_hold",    self._run_cashier_perf),
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

    def _set_preset(self, days: int) -> None:
        end = QDate.currentDate()
        start = end if days == 0 else end.addDays(-days)
        self._start.setDate(start)
        self._end.setDate(end)

    def _period(self) -> tuple[str, str]:
        return (self._start.date().toString("yyyy-MM-dd"),
                self._end.date().toString("yyyy-MM-dd"))

    # ─── Report runners ──────────────────────────────────────────────────────

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
