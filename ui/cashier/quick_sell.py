"""Quick-Sell button grid: persistent up to 12 user-defined hotbuttons.

Click → emit `add_requested(name, price_cents, taxable)` so the register can
add a manual cart line without typing on the numpad.

Long-press (≥600ms) → emit `edit_requested(qid)` so caller can open the
edit/delete dialog.

The grid also exposes a trailing "+" tile when slot count < 12; clicking it
emits `add_button_requested`.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import db
from core.logger import get_logger
from ui import styles

log = get_logger("ui.quick_sell")

MAX_QUICK_BUTTONS = 12


# ─── Edit/create dialog ──────────────────────────────────────────────────────

class QuickButtonDialog(QDialog):
    """Add or edit a quick-sale button."""

    PRESET_COLORS = [
        "#27AE60", "#2196F3", "#F39C12", "#9B59B6",
        "#E74C3C", "#1ABC9C", "#34495E", "#E67E22",
    ]

    def __init__(self, *, qid: Optional[int], existing: Optional[dict] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.qid = qid
        self.setObjectName("quick_btn_dialog")
        self.setWindowTitle("Quick Button" if qid is None else "Edit Quick Button")
        self.setModal(True)
        self.setMinimumSize(420, 320)
        self._color = (existing or {}).get("color") or self.PRESET_COLORS[0]

        self.setStyleSheet(
            "QLineEdit { padding: 8px 12px; border: 1px solid #B0BEC5;"
            " border-radius: 6px; font-size: 12pt; min-width: 220px; }"
            f"QLineEdit:focus {{ border: 2px solid {styles.COLORS['blue_mid']}; }}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16); v.setSpacing(12)

        title = QLabel("Quick Button")
        tf = QFont(styles.FONT_FAMILY, 17); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        f = QFormLayout(); f.setHorizontalSpacing(14); f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._name = QLineEdit((existing or {}).get("name", ""))
        f.addRow("Name:", self._name)
        self._price = QLineEdit("0.00" if not existing else f"{existing['price_cents']/100:.2f}")
        self._price.setPlaceholderText("Dollars (e.g. 1.99)")
        f.addRow("Price:", self._price)
        self._taxable = QCheckBox("Taxable (GST + PST)")
        self._taxable.setChecked(bool((existing or {}).get("taxable", 1)))
        f.addRow("Tax:", self._taxable)

        # Color row: preset chips + custom button.
        cwrap = QHBoxLayout(); cwrap.setSpacing(6)
        self._color_btns: list[QPushButton] = []
        for c in self.PRESET_COLORS:
            cb = QPushButton()
            cb.setFixedSize(34, 34)
            cb.setProperty("color_value", c)
            cb.setStyleSheet(self._chip_qss(c, c == self._color))
            cb.clicked.connect(lambda _ck=False, x=c, w=cb: self._pick_color(x))
            cwrap.addWidget(cb); self._color_btns.append(cb)
        custom = QPushButton("Custom…")
        custom.clicked.connect(self._pick_custom_color)
        cwrap.addWidget(custom)
        f.addRow("Color:", self._wrap(cwrap))
        v.addLayout(f)

        # Buttons
        btns = QHBoxLayout(); btns.setSpacing(10); btns.addStretch(1)
        if qid is not None:
            delete = QPushButton("Delete")
            delete.setMinimumSize(110, 42)
            delete.setStyleSheet(
                f"QPushButton {{ background-color: {styles.COLORS['btn_cancel']};"
                f" color: white; border: none; border-radius: 6px; font-weight: bold; }}"
            )
            delete.clicked.connect(self._on_delete)
            btns.addWidget(delete)
        cancel = QPushButton("Cancel"); cancel.setMinimumSize(110, 42)
        cancel.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_void']};"
            f" color: white; border: none; border-radius: 6px; font-weight: bold; }}"
        )
        cancel.clicked.connect(self.reject); btns.addWidget(cancel)
        save = QPushButton("Save"); save.setMinimumSize(140, 42); save.setDefault(True)
        save.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']};"
            f" color: white; border: none; border-radius: 6px;"
            f" font-weight: bold; font-size: 13pt; }}"
        )
        save.clicked.connect(self._on_save); btns.addWidget(save)
        v.addLayout(btns)

        # Result holders
        self.deleted = False
        self.result_data: Optional[dict] = None

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget(); w.setLayout(layout); return w

    @staticmethod
    def _chip_qss(color: str, selected: bool) -> str:
        border = "3px solid #1B3A6B" if selected else "1px solid #B0BEC5"
        return (
            f"QPushButton {{ background-color: {color}; border: {border};"
            f" border-radius: 4px; }}"
        )

    def _pick_color(self, color: str) -> None:
        self._color = color
        for b in self._color_btns:
            b.setStyleSheet(self._chip_qss(b.property("color_value"),
                                           b.property("color_value") == color))

    def _pick_custom_color(self) -> None:
        from PyQt6.QtGui import QColor
        c = QColorDialog.getColor(QColor(self._color), self, "Pick color")
        if c.isValid():
            self._color = c.name()
            for b in self._color_btns:
                b.setStyleSheet(self._chip_qss(b.property("color_value"), False))

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Quick Button", "Name required.")
            return
        try:
            price = int(round(float(self._price.text().strip() or "0") * 100))
            if price < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Quick Button", "Price must be a non-negative number.")
            return
        self.result_data = {
            "name": name,
            "price_cents": price,
            "taxable": self._taxable.isChecked(),
            "color": self._color,
        }
        self.accept()

    def _on_delete(self) -> None:
        reply = QMessageBox.question(
            self, "Delete Quick Button",
            "Delete this quick button? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.deleted = True
        self.accept()


# ─── Quick-sell grid ─────────────────────────────────────────────────────────

class QuickSellGrid(QWidget):
    """Up to 12 persisted quick-sale buttons + trailing '+' to add new."""

    add_requested = pyqtSignal(str, int, bool)   # name, price_cents, taxable
    admin_unlock_requested = pyqtSignal(object)  # callback(success_bool)

    def __init__(self, dept_id: str = "", admin_mode: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("quick_sell_grid")
        self._dept_id = dept_id
        self._admin_mode = bool(admin_mode)
        self._press_timers: dict[int, QTimer] = {}
        self._build()
        self.refresh()

    def set_dept(self, dept_id: str) -> None:
        self._dept_id = dept_id
        self.refresh()

    def set_admin_mode(self, on: bool) -> None:
        """Toggle admin tools (+ tile + long-press edit). Employees see read-only."""
        self._admin_mode = bool(on)
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(
            "QWidget#quick_sell_grid { background: white; }"
        )
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(6, 4, 6, 4); self._outer.setSpacing(2)

        self._grid_host = QFrame()
        self._grid_host.setStyleSheet("background: white;")
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setSpacing(4)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._outer.addWidget(self._grid_host)

    def refresh(self) -> None:
        # Clear grid
        while self._grid_layout.count():
            it = self._grid_layout.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.setParent(None); w.deleteLater()

        try:
            buttons = db.list_quick_buttons(self._dept_id)
        except Exception:
            log.exception("list_quick_buttons failed")
            buttons = []

        cols = 6
        for i, b in enumerate(buttons[:MAX_QUICK_BUTTONS]):
            r, c = divmod(i, cols)
            tile = self._make_tile(b)
            self._grid_layout.addWidget(tile, r, c)

        # Trailing "+" tile — admin-only. Employees see read-only grid.
        if self._admin_mode and len(buttons) < MAX_QUICK_BUTTONS:
            i = len(buttons)
            r, c = divmod(i, cols)
            plus = QPushButton("+")
            plus.setObjectName("quick_btn_plus")
            plus.setMinimumHeight(46)
            pf = QFont(styles.FONT_FAMILY, 18); pf.setBold(True)
            plus.setFont(pf)
            plus.setStyleSheet(
                "QPushButton { background-color: white; color: #1B3A6B;"
                " border: 2px dashed #B0BEC5; border-radius: 8px; }"
                "QPushButton:hover { border-color: #1B3A6B; }"
            )
            plus.clicked.connect(self._on_add_button)
            self._grid_layout.addWidget(plus, r, c)

    def _make_tile(self, row: dict) -> QPushButton:
        b = QPushButton(row["name"])
        b.setObjectName(f"quick_btn_{row['id']}")
        b.setMinimumHeight(46)
        bf = QFont(styles.FONT_FAMILY, 12); bf.setBold(True)
        b.setFont(bf)
        color = row.get("color") or "#27AE60"
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: #1B1B1B;"
            f" border: none; border-radius: 8px;"
            f" padding: 4px 6px; font-weight: bold; }}"
            f"QPushButton:pressed {{ padding: 6px 6px 2px 8px; }}"
        )
        b.clicked.connect(lambda _ck=False, rr=row: self._on_tile_click(rr))
        # Long-press detection — start timer on press, cancel on release/click.
        b.pressed.connect(lambda rr=row, btn=b: self._start_long_press(rr, btn))
        b.released.connect(lambda rr=row: self._cancel_long_press(rr["id"]))
        return b

    def _start_long_press(self, row: dict, btn: QPushButton) -> None:
        if not self._admin_mode:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(600)
        timer.timeout.connect(lambda rr=row, b=btn: self._on_long_press(rr, b))
        self._press_timers[row["id"]] = timer
        timer.start()

    def _cancel_long_press(self, qid: int) -> None:
        t = self._press_timers.pop(qid, None)
        if t is not None:
            t.stop()

    def _on_long_press(self, row: dict, btn: QPushButton) -> None:
        # Block the upcoming click from firing add.
        self._press_timers.pop(row["id"], None)
        btn.setDown(False)
        self._open_edit(row)

    def _on_tile_click(self, row: dict) -> None:
        # Suppress click if long-press already opened edit.
        if not self._press_timers.pop(row["id"], None) and not row.get("_just_edited"):
            self.add_requested.emit(
                row["name"], int(row["price_cents"]), bool(row.get("taxable", 1)),
            )

    def _on_add_button(self) -> None:
        dlg = QuickButtonDialog(qid=None, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_data is None:
            return
        try:
            db.create_quick_button(dept_id=self._dept_id, **dlg.result_data)
        except Exception:
            log.exception("create_quick_button failed")
            QMessageBox.warning(self, "Quick Button", "Could not save.")
            return
        self.refresh()

    def _open_edit(self, row: dict) -> None:
        dlg = QuickButtonDialog(qid=int(row["id"]), existing=row, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dlg.deleted:
                db.delete_quick_button(int(row["id"]))
            elif dlg.result_data is not None:
                db.update_quick_button(int(row["id"]), **dlg.result_data)
        except Exception:
            log.exception("quick button save failed")
            QMessageBox.warning(self, "Quick Button", "Save failed.")
            return
        self.refresh()
