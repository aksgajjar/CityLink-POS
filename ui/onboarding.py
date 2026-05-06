"""First-launch onboarding helpers — currently just the forced PIN change.

Production POS rule: factory default PINs (Admin 1234 / Cashier 9999) must be
changed before the device can be used to ring real sales. This module owns:

- `is_default_pin(user)` — true if the user's PIN still matches a known
  factory default.
- `ForcePinChangeDialog` — modal that appears on first admin login;
  cashier cannot reach the admin dashboard until the PIN is changed.

Cashier (non-admin) accounts are not forced through this flow — admin
manages cashier PINs from the Users screen.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import db
from core.logger import get_logger
from core.models import User
from ui import styles

log = get_logger("onboarding")

# Known factory default PINs seeded by main._seed_default_users_if_empty.
# Any user whose stored hash matches one of these must change PIN at login.
_FACTORY_PINS = ("1234", "9999")


def is_default_pin(user: User) -> bool:
    """True when the user's pin_hash matches a known factory default PIN."""
    if not user or not getattr(user, "pin_hash", None):
        return False
    factory_hashes = {db.hash_pin(p) for p in _FACTORY_PINS}
    return user.pin_hash in factory_hashes


class ForcePinChangeDialog(QDialog):
    """Modal that locks the user on screen until they pick a new PIN.

    Touch-friendly numpad. Two-step flow: enter new PIN, confirm new PIN.
    Rejects if PIN matches a factory default or is shorter than 4 digits.
    """

    PIN_LENGTH = 4

    def __init__(self, user: User, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.user = user
        self.setObjectName("force_pin_change")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(
            styles.premium_dialog_qss() + styles.dialog_titlebar_qss()
            + "QFrame#fpcShadow { background: white; border-radius: 14px;"
            "  border: 1px solid #E1E4EA; }"
        )

        self._step = 1   # 1 = enter new, 2 = confirm
        self._first: str = ""
        self._buffer: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        shadow = QFrame()
        shadow.setObjectName("fpcShadow")
        sv = QVBoxLayout(shadow)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel("Set Your Admin PIN")
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        sv.addWidget(title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(28, 22, 28, 22)
        body.setSpacing(14)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        intro = QLabel(
            f"Welcome, {user.name}. The factory default PIN is in use — "
            f"please pick a new 4-digit PIN before continuing."
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        intro.setStyleSheet("color: #5A6573; font-size: 11pt;")
        body.addWidget(intro)

        self._prompt = QLabel("Enter new PIN")
        pf = QFont(styles.FONT_FAMILY, 13); pf.setBold(True)
        self._prompt.setFont(pf)
        self._prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._prompt.setStyleSheet(f"color: {styles.COLORS['navy']};")
        body.addWidget(self._prompt)

        # 4 dot indicators
        dot_row = QHBoxLayout()
        dot_row.setSpacing(20)
        dot_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots: list[QLabel] = []
        for _ in range(self.PIN_LENGTH):
            d = QLabel("○")
            df = QFont(styles.FONT_FAMILY, 26); df.setBold(True)
            d.setFont(df)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            d.setStyleSheet("color: #B0BEC5; background: transparent;")
            self._dots.append(d)
            dot_row.addWidget(d)
        body.addLayout(dot_row)

        # Numpad
        grid = QGridLayout()
        grid.setSpacing(8)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        digits = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("CLR", 3, 0), ("0", 3, 1), ("⌫", 3, 2),
        ]
        for label, r, c in digits:
            b = QPushButton(label)
            b.setMinimumSize(72, 60)
            b.setStyleSheet(self._key_qss(label))
            b.clicked.connect(lambda _ck=False, x=label: self._press(x))
            grid.addWidget(b, r, c)
        body.addLayout(grid)

        # Status / error line
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("color: #C0392B; font-size: 11pt;")
        body.addWidget(self._status)

        sv.addLayout(body)
        outer.addWidget(shadow)
        self.setMinimumSize(420, 540)

    @staticmethod
    def _key_qss(label: str) -> str:
        if label == "CLR":
            return (
                "QPushButton { background: #E67E22; color: white;"
                " border: none; border-radius: 8px;"
                " font-size: 14pt; font-weight: bold; }"
                "QPushButton:hover { background: #D35400; }"
            )
        if label == "⌫":
            return (
                "QPushButton { background: #3F4C5C; color: white;"
                " border: none; border-radius: 8px;"
                " font-size: 16pt; font-weight: bold; }"
                "QPushButton:hover { background: #4F5D6E; }"
            )
        return (
            "QPushButton { background: #2D3A4A; color: white;"
            " border: 1px solid #3A4A5C; border-radius: 8px;"
            " font-size: 18pt; font-weight: bold; }"
            "QPushButton:hover { background: #34465B; }"
        )

    def _press(self, key: str) -> None:
        if key == "CLR":
            self._buffer = ""
        elif key == "⌫":
            self._buffer = self._buffer[:-1]
        elif key.isdigit() and len(self._buffer) < self.PIN_LENGTH:
            self._buffer += key
        self._render_dots()
        if len(self._buffer) == self.PIN_LENGTH:
            self._on_full()

    def _render_dots(self) -> None:
        for i, d in enumerate(self._dots):
            filled = i < len(self._buffer)
            d.setText("●" if filled else "○")
            d.setStyleSheet(
                f"color: {styles.COLORS['blue_mid']}; background: transparent;"
                if filled else
                "color: #B0BEC5; background: transparent;"
            )

    def _on_full(self) -> None:
        if self._step == 1:
            # Reject obvious bad PINs.
            if self._buffer in _FACTORY_PINS:
                self._error(
                    "That's still the factory default — pick a different PIN."
                )
                return
            if len(set(self._buffer)) == 1:
                self._error("PIN can't be all the same digit (e.g. 0000).")
                return
            # Move to confirm step.
            self._first = self._buffer
            self._buffer = ""
            self._step = 2
            self._prompt.setText("Confirm new PIN")
            self._status.setText("")
            self._render_dots()
            return
        # Step 2 — confirm
        if self._buffer != self._first:
            self._error("PINs don't match. Try again.")
            self._buffer = ""; self._first = ""
            self._step = 1
            self._prompt.setText("Enter new PIN")
            self._render_dots()
            return
        # Persist.
        try:
            db.update_user_pin(self.user.id, self._buffer)
        except Exception:
            log.exception("update_user_pin failed for user=%s", self.user.id)
            self._error("Could not save PIN. See errors.log.")
            return
        log.info("admin %s changed PIN from factory default", self.user.name)
        self.accept()

    def _error(self, msg: str) -> None:
        self._status.setText(msg)

    def keyPressEvent(self, ev) -> None:
        # Hardware keypad / keyboard support.
        k = ev.key()
        if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
            self._press(chr(k))
            return
        if k == Qt.Key.Key_Backspace:
            self._press("⌫"); return
        if k == Qt.Key.Key_Escape:
            # Disallow ESC — admin must change PIN to proceed.
            return
        super().keyPressEvent(ev)
