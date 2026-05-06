"""4-digit PIN login screen.

Touch-friendly numpad. Auto-submits on 4 digits. Shake animation on wrong PIN.
Polls lockout state and disables digit buttons + shows countdown when locked.

Emits `login_succeeded(User)` on a successful PIN. Wire from `MainWindow`.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from pathlib import Path

from PyQt6.QtGui import QFont, QPixmap
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

from core import auth
from core.logger import get_logger
from core.models import User
from ui import styles

log = get_logger("ui.login")

PIN_LENGTH = 4
SUBMIT_DELAY_MS = 120          # tiny pause so user sees the last dot fill
LOCK_POLL_INTERVAL_MS = 1000
LOGO_PATH = Path("assets/logo.png")
LOGO_TARGET_HEIGHT = 96        # px; preserves aspect ratio


def _login_qss() -> str:
    """Premium dark-grey gradient + per-element styling for login surface."""
    c = styles.COLORS
    return (
        # Whole screen background gradient — subtle navy-grey blend
        f"QWidget#login_screen {{"
        f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f"      stop:0 #2A3949, stop:0.55 #1F2A38, stop:1 #16202D);"
        f"}}"
        # Center card
        f"QFrame#login_card {{"
        f"  background-color: rgba(255,255,255,0.04);"
        f"  border: 1px solid rgba(255,255,255,0.08);"
        f"  border-radius: 16px;"
        f"}}"
        # Logo subtitle
        f"QLabel#logo_subtitle {{ color: #A8B2BD; letter-spacing: 2px;"
        f"  font-size: 11pt; font-weight: bold; }}"
        f"QLabel#store_name {{ color: #DCE3EA; font-size: 11pt; }}"
        # PIN dots frame + dots
        f"QFrame#pin_display_frame {{"
        f"  background-color: rgba(0,0,0,0.18);"
        f"  border: 1px solid rgba(255,255,255,0.10);"
        f"  border-radius: 10px;"
        f"}}"
        f"QLabel[pinDot=\"true\"] {{ color: rgba(255,255,255,0.25); }}"
        f"QLabel[pinDot=\"true\"][filled=\"true\"] {{ color: #6FB3FF; }}"
        # Numpad digits (matte charcoal, blue accent on hover)
        f"QPushButton[loginKey=\"digit\"] {{"
        f"  background-color: #2D3A4A; color: #FFFFFF;"
        f"  border: 1px solid #3A4A5C; border-radius: 12px;"
        f"  font-size: 22pt; font-weight: bold;"
        f"  min-width: 84px; min-height: 76px;"
        f"}}"
        f"QPushButton[loginKey=\"digit\"]:hover {{"
        f"  background-color: #34465B; border: 1px solid {c['blue_mid']};"
        f"}}"
        f"QPushButton[loginKey=\"digit\"]:pressed {{"
        f"  background-color: {c['blue_mid']}; padding-top: 2px;"
        f"}}"
        f"QPushButton[loginKey=\"digit\"]:disabled {{"
        f"  background-color: #25303D; color: #5A6573; border: 1px solid #2D3A4A;"
        f"}}"
        # CLR (orange/red)
        f"QPushButton[loginKey=\"clr\"] {{"
        f"  background-color: #E67E22; color: white;"
        f"  border: none; border-radius: 12px;"
        f"  font-size: 14pt; font-weight: bold;"
        f"  min-width: 84px; min-height: 76px;"
        f"}}"
        f"QPushButton[loginKey=\"clr\"]:hover {{ background-color: #D35400; }}"
        # Backspace (slate)
        f"QPushButton[loginKey=\"back\"] {{"
        f"  background-color: #3F4C5C; color: white;"
        f"  border: none; border-radius: 12px;"
        f"  font-size: 22pt; font-weight: bold;"
        f"  min-width: 84px; min-height: 76px;"
        f"}}"
        f"QPushButton[loginKey=\"back\"]:hover {{ background-color: #4F5D6E; }}"
        # Status label
        f"QLabel#login_status {{ color: #A8B2BD; font-size: 11pt; }}"
        f"QLabel#login_status[class=\"danger\"] {{ color: #FF7C7C; font-weight: bold; }}"
    )


class LoginScreen(QWidget):
    """Touch-friendly 4-digit PIN entry."""

    login_succeeded = pyqtSignal(object)   # User

    def __init__(
        self,
        store_name: str = "CityLink Convenience",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("login_screen")
        # Premium login QSS overrides global app theme on this surface only.
        self.setStyleSheet(_login_qss())
        self._pin_buffer: str = ""
        self._shake_anim: Optional[QPropertyAnimation] = None

        self._build_ui(store_name)
        self._fade_in()

        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(LOCK_POLL_INTERVAL_MS)
        self._lock_timer.timeout.connect(self._refresh_lock_state)
        self._refresh_lock_state()

    # ─── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self, store_name: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

        # Centered card holds header + pin dots + numpad + status
        card = QFrame()
        card.setObjectName("login_card")
        card.setFixedWidth(420)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(28, 32, 28, 28)
        cv.setSpacing(20)
        cv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        cv.addWidget(self._build_header(store_name),
                     alignment=Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(self._build_pin_display(),
                     alignment=Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(self._build_numpad(),
                     alignment=Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(self._build_status(),
                     alignment=Qt.AlignmentFlag.AlignCenter)

        root.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

    def _build_header(self, store_name: str) -> QWidget:
        header = QFrame()
        header.setObjectName("login_header")
        h = QVBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)

        logo = self._build_logo_label()
        h.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("CityLink Convenience POS")
        sub.setObjectName("logo_subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(sub)

        if store_name and store_name != "CityLink Convenience":
            store = QLabel(store_name)
            store.setObjectName("store_name")
            store.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.addWidget(store)
        return header

    def _build_logo_label(self) -> QLabel:
        """Load assets/logo.png. Fall back to text 'CITYLINK' if missing or invalid.
        Soft glow behind the mark via QGraphicsDropShadowEffect.
        """
        logo = QLabel()
        logo.setObjectName("logo_text")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        pixmap: QPixmap | None = None
        if LOGO_PATH.exists():
            pm = QPixmap(str(LOGO_PATH))
            if not pm.isNull():
                # Smooth aspect-ratio-preserving scale.
                pixmap = pm.scaledToHeight(
                    LOGO_TARGET_HEIGHT,
                    Qt.TransformationMode.SmoothTransformation,
                )

        if pixmap is not None:
            logo.setPixmap(pixmap)
            logo.setStyleSheet("background: transparent;")
        else:
            log.warning("logo asset missing or invalid (%s) — using text fallback", LOGO_PATH)
            logo.setText("CITYLINK")
            f = QFont(styles.FONT_FAMILY, 36)
            f.setBold(True)
            logo.setFont(f)
            logo.setStyleSheet("color: white; background: transparent;"
                               " letter-spacing: 4px;")

        # Soft glow behind the logo.
        try:
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect
            from PyQt6.QtGui import QColor as _QC
            shadow = QGraphicsDropShadowEffect(logo)
            shadow.setBlurRadius(36)
            shadow.setOffset(0, 0)
            shadow.setColor(_QC(91, 155, 213, 140))   # blue glow
            logo.setGraphicsEffect(shadow)
        except Exception:
            pass
        return logo

    def _build_pin_display(self) -> QWidget:
        self.pin_display_frame = QFrame()
        self.pin_display_frame.setObjectName("pin_display_frame")
        self.pin_display_frame.setFixedSize(280, 64)
        self.pin_display_frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self.pin_display_frame)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(24)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pin_dots: list[QLabel] = []
        for i in range(PIN_LENGTH):
            d = QLabel("●")
            d.setObjectName(f"pin_dot_{i}")
            d.setProperty("pinDot", True)
            d.setProperty("filled", False)
            df = QFont(styles.FONT_FAMILY, 28); df.setBold(True)
            d.setFont(df)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pin_dots.append(d)
            lay.addWidget(d)
        return self.pin_display_frame

    def _build_numpad(self) -> QWidget:
        self.numpad_frame = QFrame()
        self.numpad_frame.setObjectName("numpad_frame")
        grid = QGridLayout(self.numpad_frame)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)

        digit_positions = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
        ]
        self._digit_buttons: list[QPushButton] = []
        for d, r, c in digit_positions:
            b = self._mk_btn(d, f"pin_btn_{d}", role="digit")
            b.clicked.connect(lambda _checked=False, x=d: self._on_digit(x))
            grid.addWidget(b, r, c)
            self._digit_buttons.append(b)

        clr = self._mk_btn("CLR", "pin_btn_clr", role="clr")
        clr.clicked.connect(self._on_clear)
        grid.addWidget(clr, 3, 0)

        zero = self._mk_btn("0", "pin_btn_0", role="digit")
        zero.clicked.connect(lambda _checked=False: self._on_digit("0"))
        grid.addWidget(zero, 3, 1)
        self._digit_buttons.append(zero)

        back = self._mk_btn("⌫", "pin_btn_back", role="back")
        back.clicked.connect(self._on_back)
        grid.addWidget(back, 3, 2)
        self._back_button = back

        return self.numpad_frame

    def _mk_btn(self, text: str, name: str, *, role: str = "digit") -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setProperty("loginKey", role)
        return b

    def _build_status(self) -> QWidget:
        self.status_label = QLabel("Enter PIN")
        self.status_label.setObjectName("login_status")
        self.status_label.setProperty("class", "")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFont(QFont(styles.FONT_FAMILY, 12))
        self.status_label.setMinimumWidth(360)
        return self.status_label

    # ─── digit / control handlers ────────────────────────────────────────────

    def _on_digit(self, d: str) -> None:
        if auth.is_locked():
            return
        if len(self._pin_buffer) >= PIN_LENGTH:
            return
        self._pin_buffer += d
        self._render_dots()
        if len(self._pin_buffer) == PIN_LENGTH:
            QTimer.singleShot(SUBMIT_DELAY_MS, self._try_login)

    def _on_clear(self) -> None:
        self._pin_buffer = ""
        self._render_dots()
        self._set_status("Enter PIN", style_class="")

    def _on_back(self) -> None:
        if not self._pin_buffer:
            return
        self._pin_buffer = self._pin_buffer[:-1]
        self._render_dots()

    def _render_dots(self) -> None:
        for i, d in enumerate(self._pin_dots):
            filled = i < len(self._pin_buffer)
            d.setProperty("filled", filled)
            # Re-polish so dynamic property change repaints with QSS rule.
            d.style().unpolish(d)
            d.style().polish(d)

    def _fade_in(self) -> None:
        """Soft fade-in on first show. Cheap; no UI thread blocking."""
        try:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.0)
            self.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(280)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
            self._fade_anim = anim
        except Exception:
            pass

    # ─── keyboard support ───────────────────────────────────────────────────

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        # Digits
        if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
            self._on_digit(chr(k))
            return
        if k == Qt.Key.Key_Backspace:
            self._on_back(); return
        if k == Qt.Key.Key_Escape:
            self._on_clear(); return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if len(self._pin_buffer) == PIN_LENGTH:
                self._try_login()
            return
        super().keyPressEvent(ev)

    # ─── login attempt ───────────────────────────────────────────────────────

    def _try_login(self) -> None:
        pin = self._pin_buffer
        user = auth.verify_pin(pin)
        if user is not None:
            log.info("login OK for %s", user.name)
            self._on_clear()
            self.login_succeeded.emit(user)
            return

        # Failure path
        self._pin_buffer = ""
        self._render_dots()
        if auth.is_locked():
            self._refresh_lock_state()
        else:
            self._set_status("Wrong PIN", style_class="danger")
        self._shake()

    # ─── lockout handling ────────────────────────────────────────────────────

    def _refresh_lock_state(self) -> None:
        if auth.is_locked():
            secs = auth.seconds_until_unlock()
            self._set_status(
                f"Locked. Try again in {self._fmt_remaining(secs)}",
                style_class="danger",
            )
            self._set_numpad_enabled(False)
            if not self._lock_timer.isActive():
                self._lock_timer.start()
        else:
            if self._lock_timer.isActive():
                self._lock_timer.stop()
            self._set_status("Enter PIN", style_class="")
            self._set_numpad_enabled(True)

    @staticmethod
    def _fmt_remaining(secs: int) -> str:
        m, s = divmod(max(0, secs), 60)
        return f"{m}:{s:02d}"

    def _set_numpad_enabled(self, enabled: bool) -> None:
        for b in self._digit_buttons:
            b.setEnabled(enabled)
        self._back_button.setEnabled(enabled)

    def _set_status(self, text: str, style_class: str = "") -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("class", style_class)
        # Re-polish so the dynamic property change takes visual effect
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ─── shake animation ─────────────────────────────────────────────────────

    def _shake(self) -> None:
        target = self.pin_display_frame
        anim = QPropertyAnimation(target, b"pos", self)
        anim.setDuration(280)
        start = target.pos()
        offsets = [0, -12, 12, -8, 8, -4, 4, 0]
        n = len(offsets)
        for i, off in enumerate(offsets):
            anim.setKeyValueAt(i / (n - 1), QPoint(start.x() + off, start.y()))
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.finished.connect(lambda: target.move(start))
        anim.start()
        # Hold a reference so the animation isn't garbage-collected mid-play.
        self._shake_anim = anim
