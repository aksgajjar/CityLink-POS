"""Admin Reports — premium visual dashboard.

Layout:
  ┌────────────────────────────────────────────────────────────────────┐
  │ Reports                          [Print Receipt][Export PDF][Save] │
  │ Period pills (Today / Yesterday / Week / Month / Custom)            │
  │ ┌─ KPI cards row (9 colored cards) ─────────────────────────────┐  │
  │ │ Baskets │ Items │ Net Sales │ Avg Basket │ Avg Items │ Scan…  │  │
  │ └────────────────────────────────────────────────────────────────┘  │
  │ ┌── Shift list (left) ──┐┌── Section cards (right, scrollable) ──┐│
  │ │ Search Shift          ││ Cash Summary                           ││
  │ │ ☑ Print All           ││ Payment Breakdown                      ││
  │ │ Started Closed Name   ││ Tax Summary                            ││
  │ │ ……                    ││ Lottery Summary                        ││
  │ │                       ││ Department Sales                       ││
  │ │                       ││ Top Items / Hourly / Cashier / Voids   ││
  │ │                       ││ Scan Ratio                             ││
  │ └───────────────────────┘└────────────────────────────────────────┘│
  └────────────────────────────────────────────────────────────────────┘

No auto-print. Every output action requires explicit user click.
Three rendering paths kept separate:
  - Visual dashboard (this file)
  - Receipt text (_format_receipt_text — 32/48 col)
  - PDF (reports.render_generic_pdf — branded full page)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import db, reports
from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.reports")

EXPORTS_DIR = Path("exports")
RECEIPT_WIDTH_58MM = 32
RECEIPT_WIDTH_80MM = 48

PERIODS = ["Today", "Yesterday", "Week", "Month", "Custom"]


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents)/100:.2f}"


def _period_range(period: str, custom_from: Optional[date] = None,
                  custom_to: Optional[date] = None) -> tuple[str, str, str]:
    today = date.today()
    if period == "Today":
        s = e = today
    elif period == "Yesterday":
        s = e = today - timedelta(days=1)
    elif period == "Week":
        s = today - timedelta(days=today.weekday())
        e = today
    elif period == "Month":
        s = today.replace(day=1)
        e = today
    elif period == "Custom" and custom_from and custom_to:
        s, e = custom_from, custom_to
    else:
        s = e = today
    label = f"{s.strftime('%m/%d/%Y')}  →  {e.strftime('%m/%d/%Y')}"
    return s.isoformat(), e.isoformat(), label


# ─── Reusable widgets ────────────────────────────────────────────────────────

class KPICard(QFrame):
    """Colored metric card. Top label, large value below. Configurable accent."""

    def __init__(self, label: str, accent: str = "#2E5BA8",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setStyleSheet(
            f"QFrame#kpiCard {{"
            f"  background: white;"
            f"  border: 1px solid #E1E4EA; border-left: 4px solid {accent};"
            f"  border-radius: 10px;"
            f"}}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel(label)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            f"color: {accent}; font-size: 10pt; font-weight: bold;"
            f" background: transparent;"
        )
        v.addWidget(self._label)
        self._value = QLabel("—")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vf = QFont(styles.FONT_FAMILY, 18); vf.setBold(True)
        self._value.setFont(vf)
        self._value.setStyleSheet(
            f"color: {styles.COLORS['navy']}; background: transparent;"
        )
        v.addWidget(self._value)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class SectionCard(QFrame):
    """White card with a navy header. Body is a QVBoxLayout the caller fills."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.setStyleSheet(
            "QFrame#sectionCard { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 10px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background: {styles.COLORS['navy']};"
            f" border-top-left-radius: 10px; border-top-right-radius: 10px; }}"
        )
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 8, 14, 8)
        title_lbl = QLabel(title)
        tf = QFont(styles.FONT_FAMILY, 11); tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setStyleSheet("color: white; background: transparent;")
        h.addWidget(title_lbl)
        h.addStretch(1)
        outer.addWidget(header)

        body = QFrame()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(6)
        outer.addWidget(body)


def _row_pair(label: str, value: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    l = QLabel(label)
    l.setStyleSheet("color: #5A6573; font-size: 11pt;")
    h.addWidget(l)
    h.addStretch(1)
    v = QLabel(value)
    v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    v.setStyleSheet(f"color: {styles.COLORS['text_dark']};"
                    f" font-size: 11pt; font-weight: bold;")
    h.addWidget(v)
    return w


def _build_table(headers: list[str], rows: list[list[str]]) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setShowGrid(False)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.setStyleSheet(styles.premium_table_qss())
    t.setAlternatingRowColors(True)
    t.setRowCount(len(rows))
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            it = QTableWidgetItem(cell)
            if ci > 0:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(ri, ci, it)
    # Constrain height to row count so cards don't take infinite space.
    h = 36 + len(rows) * 32
    t.setFixedHeight(min(max(h, 64), 360))
    return t


# ─── Main screen ─────────────────────────────────────────────────────────────

class AdminReportsScreen(QWidget):
    """Visual reports dashboard. KPI cards + structured section cards."""

    back_requested = pyqtSignal()

    def __init__(self, store: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("admin_inventory")
        self.setStyleSheet(styles.admin_screen_qss())
        self.store = store
        self._period = "Yesterday"
        self._custom_from: Optional[date] = None
        self._custom_to: Optional[date] = None
        self._period_range_iso: tuple[str, str] = ("", "")
        # Cached results for the currently rendered period.
        self._kpi_data: dict = {}
        self._sections_payload: list[tuple[str, list]] = []
        self._build()
        self.refresh()

    # ─── UI ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Reports")
        title.setObjectName("screen_title")
        title_row.addWidget(title)
        title_row.addStretch(1)

        # Receipt width selector
        from PyQt6.QtWidgets import QComboBox
        self._receipt_width = QComboBox()
        self._receipt_width.addItem("80mm receipt (48 col)", RECEIPT_WIDTH_80MM)
        self._receipt_width.addItem("58mm receipt (32 col)", RECEIPT_WIDTH_58MM)
        self._receipt_width.setMinimumHeight(36)
        self._receipt_width.setStyleSheet(styles.premium_combo_qss())
        title_row.addWidget(self._receipt_width)

        for label, variant, slot in [
            ("Print Receipt", "primary", self._on_print_receipt),
            ("Export PDF",    "primary", self._on_export_pdf),
            ("Save Report",   "ghost",   self._on_save_report),
        ]:
            b = QPushButton(label); b.setMinimumHeight(36)
            b.setStyleSheet(styles.pill_button_qss(variant))
            b.clicked.connect(slot)
            title_row.addWidget(b)
        back = QPushButton("Back to Home")
        back.setStyleSheet(styles.pill_button_qss("ghost"))
        back.setMinimumHeight(36)
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Period pills
        pills_row = QHBoxLayout()
        pills_row.setSpacing(8)
        self._pill_buttons: dict[str, QPushButton] = {}
        for p in PERIODS:
            b = QPushButton(p)
            b.setMinimumHeight(36)
            b.setMinimumWidth(100)
            b.setCheckable(True)
            b.clicked.connect(lambda _ck=False, x=p: self._on_period(x))
            self._pill_buttons[p] = b
            pills_row.addWidget(b)
        pills_row.addStretch(1)
        self._period_label = QLabel("")
        self._period_label.setStyleSheet("color: #5A6573; font-size: 10pt;")
        pills_row.addWidget(self._period_label)
        root.addLayout(pills_row)
        self._refresh_pills()

        # KPI cards
        self._kpis: dict[str, KPICard] = {}
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(10)
        kpi_layout = [
            ("Baskets",          "#2E5BA8"),
            ("Items Sold",       "#27AE60"),
            ("Net Sales",        "#16A085"),
            ("Avg Basket",       "#7D3C98"),
            ("Avg Items",        "#2980B9"),
            ("Scan Ratio",       "#E67E22"),
            ("Lottery Sales",    "#6C3483"),
            ("Retail Sales",     "#1B3A6B"),
            ("Refund/Voids",     "#E74C3C"),
        ]
        for i, (label, color) in enumerate(kpi_layout):
            card = KPICard(label, accent=color)
            self._kpis[label] = card
            kpi_grid.addWidget(card, i // 9, i)
        root.addLayout(kpi_grid)

        # Splitter — left shifts, right report sections
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #E1E4EA; }")

        # Left: shift list + search
        left = QFrame()
        left.setObjectName("card")
        left.setStyleSheet(
            "QFrame#card { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 10px; }"
        )
        lv = QVBoxLayout(left)
        lv.setContentsMargins(14, 12, 14, 12)
        lv.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search Shift")
        self._search.setProperty("touchKeyboard", "text")
        self._search.textChanged.connect(self._render_shift_table)
        lv.addWidget(self._search)
        self._print_all = QCheckBox("Print All")
        self._print_all.setChecked(True)
        lv.addWidget(self._print_all)
        self._shift_table = QTableWidget()
        self._shift_table.setColumnCount(3)
        self._shift_table.setHorizontalHeaderLabels(["Started", "Closed", "Name"])
        self._shift_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._shift_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._shift_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._shift_table.setShowGrid(False)
        self._shift_table.verticalHeader().setVisible(False)
        self._shift_table.setAlternatingRowColors(True)
        self._shift_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._shift_table.setStyleSheet(styles.premium_table_qss())
        lv.addWidget(self._shift_table, stretch=1)
        splitter.addWidget(left)

        # Right: scrollable section cards
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._sections_host = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_host)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(12)
        self._sections_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        right_scroll.setWidget(self._sections_host)
        splitter.addWidget(right_scroll)
        splitter.setSizes([320, 880])
        root.addWidget(splitter, stretch=1)

    # ─── Period handling ───────────────────────────────────────────────────

    def _refresh_pills(self) -> None:
        for p, btn in self._pill_buttons.items():
            active = p == self._period
            btn.setChecked(active)
            btn.setStyleSheet(self._pill_qss(active))

    @staticmethod
    def _pill_qss(active: bool) -> str:
        c = styles.COLORS
        if active:
            return (
                f"QPushButton {{ background: {c['blue_mid']}; color: white;"
                f" border: 1px solid {c['blue_mid']}; border-radius: 8px;"
                f" padding: 8px 14px; font-weight: bold; font-size: 11pt; }}"
            )
        return (
            f"QPushButton {{ background: white; color: {c['navy']};"
            f" border: 1px solid #E1E4EA; border-radius: 8px;"
            f" padding: 8px 14px; font-weight: bold; font-size: 11pt; }}"
            f"QPushButton:hover {{ background: #EEF3F9; }}"
        )

    def _on_period(self, period: str) -> None:
        if period == "Custom":
            dlg = _CustomRangeDialog(self._custom_from, self._custom_to, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self._refresh_pills()
                return
            self._custom_from, self._custom_to = dlg.from_date, dlg.to_date
        self._period = period
        self._refresh_pills()
        self.refresh()

    # ─── Refresh ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        s, e, label = _period_range(self._period, self._custom_from, self._custom_to)
        self._period_range_iso = (s, e)
        self._period_label.setText(label)
        try:
            self._collect_data(s, e)
        except Exception:
            log.exception("collect failed")
            self._kpi_data = {}
            self._sections_payload = []
        self._render_kpis()
        self._render_shift_table()
        self._render_sections()

    def _collect_data(self, s: str, e: str) -> None:
        try:
            tax = reports.collect_tax_summary(s, e)
        except Exception:
            tax = {"txn_count": 0, "gross_cents": 0, "gst_cents": 0,
                   "pst_cents": 0, "deposit_cents": 0, "bag_cents": 0}
        try:
            lot = reports.collect_lottery(s, e)
        except Exception:
            lot = {"sales_cents": 0, "payouts_cents": 0, "net_cents": 0}
        try:
            period = reports.collect_period_report(s, e)
        except Exception:
            period = {}
        try:
            best = reports.collect_best_sellers(s, e, limit=10)
        except Exception:
            best = []
        try:
            cashier = reports.collect_cashier_performance(s, e)
        except Exception:
            cashier = []
        try:
            voids = reports.collect_void_log(s, e)
        except Exception:
            voids = []
        # Hourly sales — derive from txn list
        try:
            txns = db.list_transactions_in_range(
                f"{s} 00:00:00", f"{e} 23:59:59",
            ) if hasattr(db, "list_transactions_in_range") else []
        except Exception:
            txns = []
        hourly: dict[int, dict] = {}
        for t in txns:
            try:
                hr = int((t.get("created_at") or "00:00")[11:13])
            except Exception:
                continue
            b = hourly.setdefault(hr, {"n": 0, "total": 0})
            b["n"] += 1
            b["total"] += int(t.get("rounded_total_cents", 0) or 0)
        # Shifts
        try:
            shifts = db.list_shifts_in_range(f"{s} 00:00:00", f"{e} 23:59:59") \
                if hasattr(db, "list_shifts_in_range") else []
        except Exception:
            shifts = []

        # KPIs
        baskets = period.get("txn_count", tax.get("txn_count", 0))
        items_sold = period.get("item_count", 0)
        net_sales = period.get("net_cents", tax.get("gross_cents", 0))
        avg_basket = (net_sales / baskets) if baskets else 0
        avg_items = (items_sold / baskets) if baskets else 0
        scan_ratio = 100.0 if items_sold else 0.0   # proxy until per-line scan flag wired
        retail_sales = net_sales - lot.get("sales_cents", 0)
        self._kpi_data = {
            "baskets": baskets,
            "items_sold": items_sold,
            "net_sales": net_sales,
            "avg_basket": int(avg_basket),
            "avg_items": avg_items,
            "scan_ratio": scan_ratio,
            "lottery_sales": lot.get("sales_cents", 0),
            "retail_sales": retail_sales,
            "voids": len(voids),
        }
        # Section payloads
        self._sections_payload = []
        # 1. Cash Summary
        cash_in = sum(int(sh.get("opening_cash_cents", 0) or 0) for sh in shifts)
        cash_gross = sum(int(t.get("rounded_total_cents", 0) or 0) for t in txns
                         if t.get("payment_method") == "cash"
                         and (t.get("rounded_total_cents") or 0) > 0)
        cash_back_lotto = sum(abs(int(t.get("change_cents", 0) or 0)) for t in txns
                              if (t.get("rounded_total_cents") or 0) < 0)
        self._sections_payload.append(("Cash Summary", [
            ("Cash In",              _money(cash_in)),
            ("Cash Gross Sales",     _money(cash_gross)),
            ("Vendor Payouts",       _money(0)),
            ("Cash Drops",           _money(0)),
            ("Cash Back from Lotto", _money(cash_back_lotto)),
            ("Cash on Hand",         _money(cash_in + cash_gross - cash_back_lotto)),
        ]))
        # 2. Payment Breakdown — table
        groups: dict[str, list] = {}
        for t in txns:
            m = (t.get("payment_method") or "other").title()
            groups.setdefault(m, []).append(t)
        total_baskets = sum(len(v) for v in groups.values()) or 1
        total_payments = sum(t["rounded_total_cents"] for t in txns) or 1
        pay_rows = []
        for method, ts in sorted(groups.items()):
            n = len(ts)
            paid = sum(t["rounded_total_cents"] for t in ts)
            pay_rows.append([
                method, str(n),
                f"{n / total_baskets * 100:.0f}%",
                _money(paid),
                f"{paid / total_payments * 100:.0f}%",
            ])
        self._sections_payload.append(
            ("Payment Breakdown",
             {"table": (["Method", "Baskets #", "Baskets %", "Payments", "Payments %"], pay_rows)})
        )
        # 3. Tax Summary
        self._sections_payload.append(("Tax Summary", [
            ("Transactions",   str(tax["txn_count"])),
            ("Gross Sales",    _money(tax["gross_cents"])),
            ("GST (5%)",       _money(tax["gst_cents"])),
            ("PST (7%)",       _money(tax["pst_cents"])),
            ("Bottle Deposit", _money(tax["deposit_cents"])),
            ("Bag Charges",    _money(tax["bag_cents"])),
        ]))
        # 4. Lottery Summary
        self._sections_payload.append(("Lottery Summary", [
            ("Sales",   _money(lot["sales_cents"])),
            ("Payouts", _money(lot["payouts_cents"])),
            ("Net",     _money(lot["net_cents"])),
        ]))
        # 5. Department Sales
        depts = period.get("dept_totals") or {}
        dept_rows = sorted(depts.items(),
                           key=lambda kv: -kv[1].get("total_cents", 0))
        self._sections_payload.append(
            ("Department Sales",
             {"table": (["Department", "Sales"],
                        [[k.title(), _money(v.get("total_cents", 0))]
                         for k, v in dept_rows])})
        )
        # 6. Top Items
        self._sections_payload.append(
            ("Top Items",
             {"table": (["Item", "Qty", "Sales"],
                        [[r["name"], str(r["qty"]), _money(r["total_cents"])]
                         for r in best])})
        )
        # 7. Hourly Sales
        self._sections_payload.append(
            ("Hourly Sales",
             {"table": (["Hour", "Baskets", "Sales"],
                        [[f"{hr:02d}:00–{hr:02d}:59", str(b["n"]),
                          _money(b["total"])]
                         for hr, b in sorted(hourly.items())])})
        )
        # 8. Cashier Performance
        self._sections_payload.append(
            ("Cashier Performance",
             {"table": (["Cashier", "Txns", "Gross"],
                        [[r["cashier_name"], str(r["txn_count"]),
                          _money(r["gross_cents"])]
                         for r in cashier])})
        )
        # 9. Refund / Void Log
        self._sections_payload.append(
            ("Refund / Void Log",
             {"table": (["When", "Ref", "By", "Amount"],
                        [[r.get("created_at", ""), r.get("transaction_ref") or "?",
                          r.get("authorized_by") or "?",
                          _money(r["amount_cents"])]
                         for r in voids])})
        )
        # 10. Scan Ratio breakdown — placeholder until per-line scan flag wired
        self._sections_payload.append(("Scan Ratio", [
            ("Scanned items",      str(items_sold)),
            ("Manual entries",     "0"),
            ("Scan ratio",         f"{scan_ratio:.1f}%"),
        ]))

        # Stash for shift list + receipt/PDF builders.
        self._shifts = shifts
        self._txns = txns

    def _render_kpis(self) -> None:
        d = self._kpi_data
        self._kpis["Baskets"].set_value(str(d.get("baskets", 0)))
        self._kpis["Items Sold"].set_value(str(d.get("items_sold", 0)))
        self._kpis["Net Sales"].set_value(_money(d.get("net_sales", 0)))
        self._kpis["Avg Basket"].set_value(_money(d.get("avg_basket", 0)))
        self._kpis["Avg Items"].set_value(f"{d.get('avg_items', 0):.1f}")
        self._kpis["Scan Ratio"].set_value(f"{d.get('scan_ratio', 0):.1f}%")
        self._kpis["Lottery Sales"].set_value(_money(d.get("lottery_sales", 0)))
        self._kpis["Retail Sales"].set_value(_money(d.get("retail_sales", 0)))
        self._kpis["Refund/Voids"].set_value(str(d.get("voids", 0)))

    def _render_shift_table(self) -> None:
        rows = list(self._shifts)
        q = (self._search.text() or "").strip().lower()
        if q:
            rows = [s for s in rows if q in (s.get("cashier_name") or "").lower()]
        self._shift_table.setRowCount(len(rows))
        for ri, s in enumerate(rows):
            self._shift_table.setItem(ri, 0, QTableWidgetItem(_short_dt(s.get("opened_at"))))
            self._shift_table.setItem(ri, 1, QTableWidgetItem(_short_dt(s.get("closed_at")) or "—"))
            self._shift_table.setItem(ri, 2, QTableWidgetItem(s.get("cashier_name") or "—"))

    def _render_sections(self) -> None:
        # Clear existing
        while self._sections_layout.count():
            it = self._sections_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for title, payload in self._sections_payload:
            card = SectionCard(title)
            if isinstance(payload, dict) and "table" in payload:
                headers, rows = payload["table"]
                if not rows:
                    empty = QLabel("— No data —")
                    empty.setStyleSheet("color: #8A8F95; font-size: 10pt;"
                                        " padding: 12px;")
                    empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    card.body.addWidget(empty)
                else:
                    card.body.addWidget(_build_table(headers, rows))
            else:
                # Plain key/value pairs
                for label, value in payload:
                    card.body.addWidget(_row_pair(label, value))
            self._sections_layout.addWidget(card)

    # ─── Output: receipt / pdf / save ───────────────────────────────────────

    def _flatten_for_text(self) -> list:
        """Sections in (title, [(label, value), ...]) form for receipt/PDF."""
        flat = []
        for title, payload in self._sections_payload:
            if isinstance(payload, dict) and "table" in payload:
                headers, rows = payload["table"]
                pairs = []
                for row in rows:
                    label = " · ".join(row[:-1])
                    pairs.append((label, row[-1]))
                flat.append((title, pairs))
            else:
                flat.append((title, list(payload)))
        return flat

    def _format_receipt_text(self, width: int) -> str:
        s, e = self._period_range_iso
        sections = self._flatten_for_text()
        lines = []
        lines.append("CITYLINK CONVENIENCE".center(width))
        lines.append(("Period: " + s + " - " + e).center(width))
        lines.append("=" * width)
        for sec_title, pairs in sections:
            lines.append("")
            lines.append(sec_title)
            lines.append("-" * width)
            if not pairs:
                lines.append("(no data)")
                continue
            for label, value in pairs:
                wrapped = textwrap.wrap(label, width=width - len(value) - 1) \
                          or [label[:width - len(value) - 1]]
                for ln in wrapped[:-1]:
                    lines.append(ln)
                last = wrapped[-1]
                pad = width - len(last) - len(value)
                lines.append(f"{last}{' ' * pad}{value}" if pad >= 1
                             else f"{last}\n{value.rjust(width)}")
        lines.append("=" * width)
        lines.append("Thank you".center(width))
        return "\n".join(lines)

    def _on_print_receipt(self) -> None:
        if not self._sections_payload:
            self._info("No data to print."); return
        width = int(self._receipt_width.currentData())
        text = self._format_receipt_text(width)
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            out = EXPORTS_DIR / f"reports_receipt_{stamp}.txt"
            out.write_text(text, encoding="utf-8")
            self._info(f"Receipt-format text saved.\n{out.name}")
            self._open_file(out)
        except Exception:
            log.exception("receipt print failed")
            self._error("Failed to render receipt.")

    def _on_export_pdf(self) -> None:
        if not self._sections_payload:
            self._info("No data to export."); return
        try:
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            s, e = self._period_range_iso
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            out = EXPORTS_DIR / f"reports_{s}_to_{e}_{stamp}.pdf"
            sections = self._flatten_for_text()
            reports.render_generic_pdf(
                "Reports", sections, store=self.store, out_path=out,
                period=(s, e),
            )
            self._info(f"PDF saved.\n{out.name}")
            self._open_file(out)
        except Exception:
            log.exception("export pdf failed")
            self._error("Failed to export PDF.")

    def _on_save_report(self) -> None:
        if not self._sections_payload:
            self._info("No data to save."); return
        s, e = self._period_range_iso
        default = EXPORTS_DIR / f"reports_{s}_to_{e}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", str(default), "Text (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            text = self._format_receipt_text(64)
            Path(path).write_text(text, encoding="utf-8")
            self._info(f"Report saved.\n{path}")
        except Exception:
            log.exception("save text failed")
            self._error("Failed to save report.")

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


# ─── Custom range dialog ─────────────────────────────────────────────────────

class _CustomRangeDialog(QDialog):
    def __init__(self, from_d: Optional[date], to_d: Optional[date],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Custom Date Range")
        self.setStyleSheet(styles.premium_dialog_qss() + styles.dialog_titlebar_qss())
        self.setMinimumWidth(420)
        self.from_date: Optional[date] = from_d
        self.to_date: Optional[date] = to_d

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar); tb.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Custom Date Range"); t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(t)
        v.addWidget(title_bar)

        body = QFrame(); body.setObjectName("card")
        bv = QVBoxLayout(body); bv.setContentsMargins(20, 16, 20, 16); bv.setSpacing(12)
        wrap = QVBoxLayout(); wrap.setContentsMargins(18, 14, 18, 14)
        wrap.addWidget(body)
        v.addLayout(wrap)

        from_lbl = QLabel("From:")
        self._from = QDateEdit(from_d or date.today() - timedelta(days=7))
        self._from.setCalendarPopup(True); self._from.setDisplayFormat("yyyy-MM-dd")
        rfr = QHBoxLayout(); rfr.addWidget(from_lbl); rfr.addWidget(self._from, stretch=1)
        bv.addLayout(rfr)

        to_lbl = QLabel("To:")
        self._to = QDateEdit(to_d or date.today())
        self._to.setCalendarPopup(True); self._to.setDisplayFormat("yyyy-MM-dd")
        rto = QHBoxLayout(); rto.addWidget(to_lbl); rto.addWidget(self._to, stretch=1)
        bv.addLayout(rto)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Apply).setText("Apply")
        btns.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        btns.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        bv.addWidget(btns)

    def _apply(self) -> None:
        self.from_date = self._from.date().toPyDate()
        self.to_date = self._to.date().toPyDate()
        self.accept()


def _short_dt(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("T", " "))
        return dt.strftime("%m/%d %I:%M%p").lower()
    except Exception:
        return iso[:16]
