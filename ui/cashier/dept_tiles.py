"""Editable department tile grid (3x3, max 9 slots, last slot = "+" for admin).

Click a tile → emits dept_clicked(dept_id_or_name).
Long-press (admin only) → opens edit/delete dialog.
Press "+" (admin only) → opens add dialog after PIN check (handled by caller).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
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

log = get_logger("ui.dept_tiles")

MAX_SLOTS = 12   # 2 rows × 6 cols


class DeptTileEditDialog(QDialog):
    """Add/edit a single dept tile. Caller decides admin vs employee."""

    PRESETS = [
        "#F4793D", "#F4C430", "#7FBA28", "#E03A3E",
        "#FF6B6B", "#3B2C7E", "#1F88E5", "#9B27B0",
        "#1ABC9C", "#34495E", "#E67E22", "#27AE60",
    ]

    def __init__(self, *, tile_id: Optional[int] = None,
                 existing: Optional[dict] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.tile_id = tile_id
        self._color = (existing or {}).get("color") or self.PRESETS[0]
        self.deleted = False
        self.result_data: Optional[dict] = None
        self.setObjectName("dept_tile_edit_dialog")
        self.setWindowTitle("Department Tile" if tile_id is None else "Edit Tile")
        self.setModal(True)
        self.setMinimumSize(420, 320)
        self.setStyleSheet(
            "QLineEdit { padding: 8px 12px; border: 1px solid #B0BEC5;"
            " border-radius: 6px; font-size: 12pt; min-width: 220px; }"
            f"QLineEdit:focus {{ border: 2px solid {styles.COLORS['blue_mid']}; }}"
        )

        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(12)
        title = QLabel("Department Tile")
        tf = QFont(styles.FONT_FAMILY, 17); tf.setBold(True)
        title.setFont(tf); title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        f = QFormLayout(); f.setHorizontalSpacing(14); f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._name = QLineEdit((existing or {}).get("name", ""))
        self._name.setPlaceholderText("e.g. Bag Fee")
        f.addRow("Name:", self._name)

        # Quick-add fields: price + taxable. Leaving Price blank/0 → tile acts
        # as a category selector (legacy behavior). Setting price >0 makes
        # the tile add an item to cart on tap with no extra prompts.
        self._price = QLineEdit("" if not (existing and existing.get("price_cents", 0)) else f"{existing['price_cents']/100:.2f}")
        self._price.setPlaceholderText("0.00 (blank = category only)")
        f.addRow("Price:", self._price)

        self._taxable = QCheckBox("Taxable (GST + PST)")
        self._taxable.setChecked(bool((existing or {}).get("taxable", 1)))
        f.addRow("Tax:", self._taxable)

        # Color preset chips
        cwrap = QHBoxLayout(); cwrap.setSpacing(6)
        self._color_btns: list[QPushButton] = []
        for c in self.PRESETS:
            cb = QPushButton(); cb.setFixedSize(34, 34)
            cb.setProperty("color_value", c)
            cb.setStyleSheet(self._chip_qss(c, c == self._color))
            cb.clicked.connect(lambda _ck=False, x=c: self._pick(x))
            cwrap.addWidget(cb); self._color_btns.append(cb)
        custom = QPushButton("Custom…")
        custom.clicked.connect(self._pick_custom); cwrap.addWidget(custom)
        f.addRow("Color:", self._wrap(cwrap))
        v.addLayout(f)

        btns = QHBoxLayout(); btns.setSpacing(10); btns.addStretch(1)
        if tile_id is not None:
            d = QPushButton("Delete"); d.setMinimumSize(110, 42)
            d.setStyleSheet(
                f"QPushButton {{ background-color: {styles.COLORS['btn_cancel']};"
                f" color: white; border: none; border-radius: 6px; font-weight: bold; }}"
            )
            d.clicked.connect(self._on_delete); btns.addWidget(d)
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

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget(); w.setLayout(layout); return w

    @staticmethod
    def _chip_qss(color: str, selected: bool) -> str:
        border = "3px solid #1B3A6B" if selected else "1px solid #B0BEC5"
        return f"QPushButton {{ background-color: {color}; border: {border}; border-radius: 4px; }}"

    def _pick(self, color: str) -> None:
        self._color = color
        for b in self._color_btns:
            b.setStyleSheet(self._chip_qss(b.property("color_value"),
                                           b.property("color_value") == color))

    def _pick_custom(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Pick color")
        if c.isValid():
            self._color = c.name()
            for b in self._color_btns:
                b.setStyleSheet(self._chip_qss(b.property("color_value"), False))

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Tile", "Name required.")
            return
        price_cents = 0
        raw_price = self._price.text().strip()
        if raw_price:
            try:
                price_cents = int(round(float(raw_price) * 100))
                if price_cents < 0: raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Tile", "Price must be a non-negative number.")
                return
        self.result_data = {
            "name": name, "color": self._color,
            "price_cents": price_cents,
            "taxable": self._taxable.isChecked(),
        }
        self.accept()

    def _on_delete(self) -> None:
        reply = QMessageBox.question(
            self, "Delete Tile",
            "Delete this department tile? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.deleted = True
        self.accept()


class DeptTileGrid(QWidget):
    """3×3 dept tile grid. Last empty slot becomes "+" for admins."""

    dept_clicked = pyqtSignal(str)        # dept_id (or empty)
    edit_requested = pyqtSignal(int)      # tile_id (admin long-press)
    add_requested = pyqtSignal()          # admin "+"
    quick_add_requested = pyqtSignal(str, int, bool)  # (name, cents, taxable)

    def __init__(self, admin_mode: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("dept_tile_grid")
        self._admin_mode = bool(admin_mode)
        self._press_timers: dict[int, QTimer] = {}
        self.setStyleSheet("QWidget#dept_tile_grid { background: #E8E8E8; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6); outer.setSpacing(6)
        self._grid = QGridLayout()
        self._grid.setSpacing(4); self._grid.setContentsMargins(0, 0, 0, 0)
        for c in range(6):
            self._grid.setColumnStretch(c, 1)
        outer.addLayout(self._grid)
        self.refresh()

    def set_admin_mode(self, on: bool) -> None:
        self._admin_mode = bool(on)
        self.refresh()

    def refresh(self) -> None:
        # Clear grid
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.setParent(None); w.deleteLater()
        try:
            tiles = db.list_dept_tiles()
        except Exception:
            log.exception("list_dept_tiles failed")
            tiles = []
        cols = 6
        # Dept tiles (cap at MAX_SLOTS - 1 to leave room for "+" if admin)
        admin_slot = self._admin_mode and len(tiles) < MAX_SLOTS
        max_dept = MAX_SLOTS - (1 if admin_slot else 0)
        for i, t in enumerate(tiles[:max_dept]):
            r, c = divmod(i, cols)
            self._grid.addWidget(self._make_tile(t), r, c)
        # "+" admin tile in next slot
        if admin_slot:
            i = min(len(tiles), max_dept)
            r, c = divmod(i, cols)
            self._grid.addWidget(self._make_plus(), r, c)
        # Fill remaining slots with empty placeholders so layout stays 3x3.
        used = (min(len(tiles), max_dept)) + (1 if admin_slot else 0)
        for i in range(used, MAX_SLOTS):
            r, c = divmod(i, cols)
            ph = QWidget()
            ph.setMinimumHeight(46); ph.setMaximumHeight(46)
            ph.setStyleSheet("background: transparent;")
            self._grid.addWidget(ph, r, c)

    def _make_tile(self, row: dict) -> QPushButton:
        b = QPushButton(row["name"])
        tid = int(row["id"])
        b.setObjectName(f"dept_tile_{tid}")
        b.setMinimumHeight(46); b.setMaximumHeight(46)
        bf = QFont(styles.FONT_FAMILY, 12); bf.setBold(True)
        b.setFont(bf)
        color = row.get("color") or "#1F88E5"
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: 1px solid #888; border-radius: 6px;"
            f" font-weight: bold; padding: 4px 6px; }}"
            f"QPushButton:pressed {{ padding: 6px 6px 2px 8px; }}"
        )
        b.clicked.connect(lambda _ck=False, rr=row, t=tid: self._on_click(rr, t))
        b.pressed.connect(lambda t=tid: self._start_long_press(t))
        b.released.connect(lambda t=tid: self._cancel_long_press(t))
        return b

    def _make_plus(self) -> QPushButton:
        b = QPushButton("+")
        b.setObjectName("dept_tile_plus")
        b.setMinimumHeight(46); b.setMaximumHeight(46)
        pf = QFont(styles.FONT_FAMILY, 22); pf.setBold(True)
        b.setFont(pf)
        b.setStyleSheet(
            "QPushButton { background-color: white; color: #1B3A6B;"
            " border: 2px dashed #B0BEC5; border-radius: 8px; }"
            "QPushButton:hover { border-color: #1B3A6B; }"
        )
        b.clicked.connect(self.add_requested.emit)
        return b

    def _on_click(self, row: dict, tile_id: int) -> None:
        # Suppress click ONLY if long-press fired (admin edit path).
        if self._press_timers.pop(tile_id, None) == "_FIRED":
            return
        price = int(row.get("price_cents") or 0)
        if price > 0:
            # Quick-add tile — instant cart add, no popup.
            self.quick_add_requested.emit(
                row["name"], price, bool(row.get("taxable", 1)),
            )
            return
        self.dept_clicked.emit(row.get("dept_id") or "")

    def _start_long_press(self, tile_id: int) -> None:
        if not self._admin_mode:
            return
        timer = QTimer(self); timer.setSingleShot(True); timer.setInterval(600)
        timer.timeout.connect(lambda t=tile_id: self._on_long_press(t))
        self._press_timers[tile_id] = timer
        timer.start()

    def _cancel_long_press(self, tile_id: int) -> None:
        t = self._press_timers.pop(tile_id, None)
        if t is not None and t != "_FIRED":
            try: t.stop()
            except Exception: pass

    def _on_long_press(self, tile_id: int) -> None:
        # Mark timer fired so the upcoming click is suppressed.
        self._press_timers[tile_id] = "_FIRED"
        self.edit_requested.emit(tile_id)
