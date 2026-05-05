"""Reusable touch numpad. Calculator-style layout (7 on top).

Three modes:
  - "price"   : digits accumulate as cents; display formatted as $X.XX. Decimal disabled.
                e.g. press 1, 2, 3, 4 → buffer "1234" → display "$12.34" → current_cents() == 1234
  - "integer" : plain integer. Decimal disabled. e.g. for quantity entry.
  - "free"    : raw decimal string allowed. e.g. for amounts that need explicit ".".

Signals:
  - value_changed(str) : fires on every keypress (digit, 00, ., backspace, clear)
  - value_entered(str) : fires only when OK is pressed (only if `with_ok=True`)

Caller controls commit. CASH/CARD/QTY action buttons in the register screen
will read `current_cents()` / `current_int()` / `text()` and call `clear()`.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui import styles

log = get_logger("ui.numpad")

MODE_PRICE = "price"
MODE_INTEGER = "integer"
MODE_FREE = "free"
MODES = (MODE_PRICE, MODE_INTEGER, MODE_FREE)

PRICE_MAX_DIGITS = 7    # $99,999.99
INT_MAX_DIGITS = 6
FREE_MAX_DIGITS = 10


class Numpad(QWidget):
    """Touch numpad widget. Reusable across price entry, qty, lottery amount."""

    value_changed = pyqtSignal(str)
    value_entered = pyqtSignal(str)

    def __init__(
        self,
        *,
        mode: str = MODE_PRICE,
        with_ok: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode!r}")
        self.setObjectName("numpad")
        self._mode: str = mode
        self._buffer: str = ""
        self._with_ok: bool = with_ok
        self._build_ui()
        self._sync_mode_ui()
        self._render()

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Numpad must expand horizontally to fill its parent (right panel),
        # otherwise the rightmost column (decimal/9) clips at the panel edge.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMaximumWidth(16777215)   # explicitly clear any inherited cap
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Display panel
        self.display = QLabel("")
        self.display.setObjectName("numpad_display")
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        df = QFont(styles.FONT_FAMILY, 24)
        df.setBold(True)
        self.display.setFont(df)
        self.display.setMinimumHeight(56)
        self.display.setStyleSheet(
            f"background-color: {styles.COLORS['white']};"
            f"color: {styles.COLORS['text_dark']};"
            f"border: 1px solid {styles.COLORS['blue_mid']};"
            f"border-radius: 6px;"
            f"padding: 4px 12px;"
        )
        root.addWidget(self.display)

        # Digit grid 7-top calculator layout
        grid = QGridLayout()
        grid.setSpacing(8)
        # Equal column widths so 0/00/. align under 7/8/9.
        for col in range(3):
            grid.setColumnStretch(col, 1)
        digit_layout = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
        ]
        self._digit_buttons: list[QPushButton] = []
        for d, r, c in digit_layout:
            b = self._mk_btn(d, f"numpad_btn_{d}")
            b.clicked.connect(lambda _checked=False, x=d: self._on_input(x))
            grid.addWidget(b, r, c)
            self._digit_buttons.append(b)

        # Bottom row: 0, 00, .
        b_zero = self._mk_btn("0", "numpad_btn_0")
        b_zero.clicked.connect(lambda _checked=False: self._on_input("0"))
        grid.addWidget(b_zero, 3, 0)
        self._digit_buttons.append(b_zero)

        b_dbl = self._mk_btn("00", "numpad_btn_00")
        b_dbl.clicked.connect(lambda _checked=False: self._on_input("00"))
        grid.addWidget(b_dbl, 3, 1)
        self._digit_buttons.append(b_dbl)

        self._decimal_btn = self._mk_btn(".", "numpad_btn_dot")
        self._decimal_btn.clicked.connect(lambda _checked=False: self._on_input("."))
        grid.addWidget(self._decimal_btn, 3, 2)

        root.addLayout(grid)

        # Control row: CLR / ← / OK
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        clr = self._mk_btn("CLR", "numpad_btn_clr")
        clr.clicked.connect(self.clear)
        ctrl.addWidget(clr)

        back = self._mk_btn("←", "numpad_btn_back")
        back.clicked.connect(self._on_back)
        ctrl.addWidget(back)

        if self._with_ok:
            ok = self._mk_btn("OK", "numpad_btn_ok")
            ok.clicked.connect(self._on_ok)
            ctrl.addWidget(ok)
            self._ok_button = ok
        else:
            self._ok_button = None

        root.addLayout(ctrl)

    def _mk_btn(self, text: str, name: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        w, h = styles.SIZES["numpad_btn"]
        b.setMinimumSize(w, h)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        f = QFont(styles.FONT_FAMILY, 20)
        f.setBold(True)
        b.setFont(f)
        return b

    def _sync_mode_ui(self) -> None:
        """Decimal button enabled only in free mode."""
        self._decimal_btn.setEnabled(self._mode == MODE_FREE)

    # ─── Public API ──────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch mode. Clears buffer."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode!r}")
        if mode == self._mode and not self._buffer:
            return
        self._mode = mode
        self._buffer = ""
        self._sync_mode_ui()
        self._render()
        self.value_changed.emit(self._buffer)

    def text(self) -> str:
        """Raw buffer string."""
        return self._buffer

    def current_cents(self) -> int:
        """Buffer value as integer cents. Only valid in price mode."""
        if self._mode != MODE_PRICE:
            raise ValueError(f"current_cents not valid in mode {self._mode!r}")
        return int(self._buffer) if self._buffer else 0

    def current_int(self) -> int:
        """Buffer value as integer. Only valid in integer mode."""
        if self._mode != MODE_INTEGER:
            raise ValueError(f"current_int not valid in mode {self._mode!r}")
        return int(self._buffer) if self._buffer else 0

    def clear(self) -> None:
        """Reset buffer."""
        if self._buffer == "":
            return
        self._buffer = ""
        self._render()
        self.value_changed.emit(self._buffer)

    # ─── Internal handlers ───────────────────────────────────────────────────

    def _on_input(self, token: str) -> None:
        if token == ".":
            if self._mode != MODE_FREE:
                return
            if "." in self._buffer:
                return
        candidate = self._buffer + token
        # Cap on number of digits (decimal point not counted).
        digits_only = candidate.replace(".", "")
        if len(digits_only) > self._cap_for_mode():
            return
        self._buffer = candidate
        self._render()
        self.value_changed.emit(self._buffer)

    def _on_back(self) -> None:
        if not self._buffer:
            return
        self._buffer = self._buffer[:-1]
        self._render()
        self.value_changed.emit(self._buffer)

    def _on_ok(self) -> None:
        self.value_entered.emit(self._buffer)

    def _cap_for_mode(self) -> int:
        return {
            MODE_PRICE: PRICE_MAX_DIGITS,
            MODE_INTEGER: INT_MAX_DIGITS,
            MODE_FREE: FREE_MAX_DIGITS,
        }[self._mode]

    def _render(self) -> None:
        if self._mode == MODE_PRICE:
            cents = int(self._buffer) if self._buffer else 0
            self.display.setText(f"${cents / 100:.2f}")
        elif self._mode == MODE_INTEGER:
            self.display.setText(self._buffer or "0")
        else:   # free
            self.display.setText(self._buffer or "")
