"""Admin cash management screen.

Operations:
  - View current shift float / opened-at
  - Cash Drop (mid-day cash → safe), amount + note
  - Petty Cash Out, amount + reason
  - Till Count (compare expected vs actual), shows variance
  - No-Sale Log table

All events persist via `db.log_cash_event(shift_id, type, ...)`.
Events table at bottom shows recent activity for the active shift.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import db
from core.logger import get_logger
from ui import styles
from ui.cashier.numpad import MODE_PRICE, Numpad

log = get_logger("ui.admin.cash_mgmt")


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


# ─── Cash management screen ──────────────────────────────────────────────────

class CashManagementScreen(QWidget):
    """Admin view: shift status + cash event actions + event log."""

    back_requested = pyqtSignal()

    def __init__(self, *, admin_name: str = "admin", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cash_mgmt_screen")
        self._admin_name = admin_name
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("CASH MANAGEMENT")
        title.setObjectName("cash_mgmt_title")
        f = QFont(styles.FONT_FAMILY, 22); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back")
        back.setObjectName("cash_mgmt_back")
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Shift info card
        self._shift_card = QFrame()
        self._shift_card.setObjectName("shift_info_card")
        self._shift_card.setStyleSheet(
            f"QFrame#shift_info_card {{ background-color: white;"
            f" border: 1px solid {styles.COLORS['blue_mid']};"
            f" border-radius: 6px; padding: 4px; }}"
        )
        self._shift_card.setFixedHeight(96)
        sl = QGridLayout(self._shift_card)
        sl.setContentsMargins(16, 8, 16, 8)
        sl.setHorizontalSpacing(20)

        def _lab(text: str, name: str, *, big: bool = False) -> QLabel:
            l = QLabel(text); l.setObjectName(name)
            f = QFont(styles.FONT_FAMILY, 14 if big else 10)
            if big:
                f.setBold(True)
            l.setFont(f)
            return l

        sl.addWidget(_lab("Active Shift", "shift_lbl_a"), 0, 0)
        self._shift_id_val = _lab("—", "shift_val_id", big=True)
        self._shift_id_val.setStyleSheet(f"color: {styles.COLORS['navy']};")
        sl.addWidget(self._shift_id_val, 1, 0)

        sl.addWidget(_lab("Cashier", "shift_lbl_b"), 0, 1)
        self._shift_cashier_val = _lab("—", "shift_val_cashier", big=True)
        sl.addWidget(self._shift_cashier_val, 1, 1)

        sl.addWidget(_lab("Opened", "shift_lbl_c"), 0, 2)
        self._shift_opened_val = _lab("—", "shift_val_opened", big=False)
        sl.addWidget(self._shift_opened_val, 1, 2)

        sl.addWidget(_lab("Opening Float", "shift_lbl_d"), 0, 3)
        self._shift_float_val = _lab("$0.00", "shift_val_float", big=True)
        self._shift_float_val.setStyleSheet(f"color: {styles.COLORS['btn_cash']};")
        sl.addWidget(self._shift_float_val, 1, 3)

        root.addWidget(self._shift_card)

        # Action buttons grid
        grid = QGridLayout()
        grid.setSpacing(10)
        for c in range(4):
            grid.setColumnStretch(c, 1)

        actions = [
            ("Cash Drop",      "cm_btn_drop",     "btn_hold",    self._on_cash_drop),
            ("Petty Cash Out", "cm_btn_petty",    "btn_lottery_p", self._on_petty),
            ("Till Count",     "cm_btn_till",     "btn_cash",    self._on_till_count),
            ("No-Sale Log",    "cm_btn_nosale",   "btn_no_sale", self._on_no_sale_log),
        ]
        for i, (label, name, color_key, slot) in enumerate(actions):
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(72)
            bf = QFont(styles.FONT_FAMILY, 13); bf.setBold(True)
            b.setFont(bf)
            color = styles.COLORS[color_key]
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: none; border-radius: 8px; padding: 12px; }}"
                f"QPushButton:disabled {{ background-color: #BDBDBD; color: #757575; }}"
            )
            b.clicked.connect(slot)
            grid.addWidget(b, 0, i)
        root.addLayout(grid)

        # Recent events table
        evt_label = QLabel("Recent Cash Events (current shift)")
        elf = QFont(styles.FONT_FAMILY, 12); elf.setBold(True)
        evt_label.setFont(elf)
        evt_label.setStyleSheet(f"color: {styles.COLORS['navy']}; padding-top: 6px;")
        root.addWidget(evt_label)

        self._events_table = QTableWidget()
        self._events_table.setObjectName("cm_events_table")
        self._events_table.setColumnCount(5)
        self._events_table.setHorizontalHeaderLabels(
            ["Time", "Type", "Amount", "Cashier", "Note"]
        )
        self._events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._events_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._events_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self._events_table, stretch=1)

    # ─── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        shift = self._active_shift()
        any_open = shift is not None
        if shift is not None:
            self._shift_id_val.setText(f"#{shift['id']}")
            self._shift_cashier_val.setText(shift["cashier_name"])
            self._shift_opened_val.setText(shift.get("opened_at") or "—")
            self._shift_float_val.setText(_money(shift.get("opening_float_cents", 0) or 0))
        else:
            self._shift_id_val.setText("(none)")
            self._shift_cashier_val.setText("—")
            self._shift_opened_val.setText("No active shift")
            self._shift_float_val.setText("$0.00")

        # Disable action buttons if no shift
        for n in ("cm_btn_drop", "cm_btn_petty", "cm_btn_till"):
            b = self.findChild(QPushButton, n)
            if b is not None:
                b.setEnabled(any_open)
        # No-sale log can show data even after shift closed → leave enabled

        # Events table
        self._events_table.setRowCount(0)
        if shift is not None:
            events = db.list_cash_events(shift["id"])
            self._events_table.setRowCount(len(events))
            for ri, e in enumerate(events):
                self._events_table.setItem(ri, 0, QTableWidgetItem(e.get("created_at", "")))
                self._events_table.setItem(ri, 1, QTableWidgetItem(e["event_type"]))
                amt = QTableWidgetItem(_money(e["amount_cents"]))
                amt.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._events_table.setItem(ri, 2, amt)
                self._events_table.setItem(ri, 3, QTableWidgetItem(e.get("cashier_name", "")))
                self._events_table.setItem(ri, 4, QTableWidgetItem(e.get("note") or ""))

    def _active_shift(self) -> Optional[dict]:
        """Return the most-recent OPEN shift, or None."""
        row = db.conn().execute(
            """SELECT * FROM shifts WHERE status = 'open'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None

    # ─── Action handlers ─────────────────────────────────────────────────────

    def _on_cash_drop(self) -> None:
        shift = self._active_shift()
        if shift is None:
            self._error("No active shift.")
            return
        dlg = CashEventDialog(
            title="Cash Drop",
            subtitle="Amount removed from drawer to safe",
            note_label="Note (optional):",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            db.log_cash_event(
                shift["id"], "drop", dlg.amount_cents,
                self._admin_name, note=dlg.note,
            )
            self.refresh()
            self._info(f"Drop logged: {_money(dlg.amount_cents)}")
        except Exception:
            log.exception("cash drop log failed")
            self._error("Failed to log drop.")

    def _on_petty(self) -> None:
        shift = self._active_shift()
        if shift is None:
            self._error("No active shift.")
            return
        dlg = CashEventDialog(
            title="Petty Cash Out",
            subtitle="Small expense paid from drawer",
            note_label="Reason (required):",
            note_required=True,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            db.log_cash_event(
                shift["id"], "petty_cash", dlg.amount_cents,
                self._admin_name, note=dlg.note,
            )
            self.refresh()
            self._info(f"Petty cash logged: {_money(dlg.amount_cents)}")
        except Exception:
            log.exception("petty cash log failed")
            self._error("Failed to log petty cash.")

    def _on_till_count(self) -> None:
        shift = self._active_shift()
        if shift is None:
            self._error("No active shift.")
            return
        # Compute expected via reports.collect_eod (reuse logic)
        try:
            from core import reports as _r
            data = _r.collect_eod(shift["id"])
            expected = data["reconciliation"]["expected_cash_cents"]
        except Exception:
            log.exception("till count: collect_eod failed")
            self._error("Could not compute expected cash.")
            return

        dlg = TillCountDialog(expected_cents=expected, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Logged as its own event_type='till_count' with the counted amount.
        try:
            db.log_cash_event(
                shift["id"], "till_count", dlg.counted_cents,
                self._admin_name,
                note=(
                    f"expected={_money(expected)}  "
                    f"variance={_money(dlg.counted_cents - expected)}"
                ),
            )
            self.refresh()
        except Exception:
            log.exception("till count log failed")

    def _on_no_sale_log(self) -> None:
        shift = self._active_shift()
        if shift is None:
            self._error("No active shift to display log for.")
            return
        events = [
            e for e in db.list_cash_events(shift["id"])
            if e["event_type"] == "no_sale"
        ]
        dlg = NoSaleLogDialog(events, parent=self)
        dlg.exec()

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "Cash Mgmt", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Cash Mgmt", msg)


# ─── Cash event dialog (numpad + note) ───────────────────────────────────────

class CashEventDialog(QDialog):
    """Generic amount + note dialog used for Cash Drop and Petty Cash."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        note_label: str = "Note:",
        note_required: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("cash_event_dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(420, 540)
        self._note_required = note_required
        self.amount_cents: int = 0
        self.note: str = ""
        self._build(title, subtitle, note_label)

    def _build(self, title: str, subtitle: str, note_label: str) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        title_lbl = QLabel(title)
        f = QFont(styles.FONT_FAMILY, 16); f.setBold(True)
        title_lbl.setFont(f)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title_lbl)

        sub = QLabel(subtitle)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        v.addWidget(sub)

        # Numpad (price mode)
        self._numpad = Numpad(mode=MODE_PRICE, with_ok=False)
        self._numpad.setMinimumHeight(280)
        v.addWidget(self._numpad, stretch=1)

        # Note field
        v.addWidget(QLabel(note_label))
        self._note = QTextEdit()
        self._note.setObjectName("cash_event_note")
        self._note.setMaximumHeight(70)
        v.addWidget(self._note)

        # Buttons
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setObjectName("cm_evt_save")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("cm_evt_cancel")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _save(self) -> None:
        self.amount_cents = self._numpad.current_cents()
        self.note = self._note.toPlainText().strip()
        if self.amount_cents <= 0:
            QMessageBox.warning(self, "Cash Mgmt", "Amount must be > 0.")
            return
        if self._note_required and not self.note:
            QMessageBox.warning(self, "Cash Mgmt", "Reason is required.")
            return
        self.accept()


# ─── Till count dialog (live variance) ───────────────────────────────────────

class TillCountDialog(QDialog):
    """Anytime till count: enter cash in drawer, see variance vs expected."""

    def __init__(self, *, expected_cents: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("till_count_dialog")
        self.setWindowTitle("Till Count")
        self.setModal(True)
        self.setMinimumSize(420, 580)
        self.expected_cents = expected_cents
        self.counted_cents: int = 0
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        title = QLabel("Till Count")
        f = QFont(styles.FONT_FAMILY, 16); f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        # Expected display
        exp_row = QHBoxLayout()
        exp_lbl = QLabel("Expected in drawer:")
        exp_val = QLabel(_money(self.expected_cents))
        ef = QFont(styles.FONT_FAMILY, 14); ef.setBold(True)
        exp_val.setFont(ef)
        exp_val.setStyleSheet(f"color: {styles.COLORS['navy']};")
        exp_row.addWidget(exp_lbl); exp_row.addStretch(1); exp_row.addWidget(exp_val)
        v.addLayout(exp_row)

        # Numpad
        self._numpad = Numpad(mode=MODE_PRICE, with_ok=False)
        self._numpad.setMinimumHeight(280)
        self._numpad.value_changed.connect(self._update_variance)
        v.addWidget(self._numpad, stretch=1)

        # Variance label
        self._var = QLabel("Variance: $0.00")
        vf = QFont(styles.FONT_FAMILY, 13); vf.setBold(True)
        self._var.setFont(vf)
        self._var.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._var)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setObjectName("till_save")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._update_variance("")

    def _update_variance(self, _text: str) -> None:
        counted = self._numpad.current_cents()
        var = counted - self.expected_cents
        sign = "+" if var > 0 else "-" if var < 0 else ""
        self._var.setText(f"Counted: {_money(counted)}   ·   Variance: {sign}{_money(abs(var))}")
        if abs(var) <= 500:
            color = styles.COLORS["btn_cash"]
        elif abs(var) <= 2000:
            color = styles.COLORS["warning"]
        else:
            color = styles.COLORS["danger"]
        self._var.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _save(self) -> None:
        self.counted_cents = self._numpad.current_cents()
        if self.counted_cents <= 0:
            QMessageBox.warning(self, "Till Count", "Enter the counted cash amount.")
            return
        self.accept()


# ─── No-sale log dialog ──────────────────────────────────────────────────────

class NoSaleLogDialog(QDialog):
    def __init__(self, events: list[dict], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("no_sale_log_dialog")
        self.setWindowTitle("No-Sale Log")
        self.setMinimumSize(520, 400)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        title = QLabel(f"No-Sale Events ({len(events)})")
        tf = QFont(styles.FONT_FAMILY, 14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        if not events:
            empty = QLabel("No no-sale events for this shift.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {styles.COLORS['text_muted']}; padding: 40px;")
            v.addWidget(empty, stretch=1)
        else:
            t = QTableWidget()
            t.setObjectName("no_sale_table")
            t.setColumnCount(3)
            t.setHorizontalHeaderLabels(["Time", "Cashier", "Note"])
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            t.setRowCount(len(events))
            for ri, e in enumerate(events):
                t.setItem(ri, 0, QTableWidgetItem(e.get("created_at", "")))
                t.setItem(ri, 1, QTableWidgetItem(e.get("cashier_name", "")))
                t.setItem(ri, 2, QTableWidgetItem(e.get("note") or ""))
            v.addWidget(t, stretch=1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        v.addWidget(bb)
