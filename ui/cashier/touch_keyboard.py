"""On-screen keyboards for touch-only deployment.

Two visual modes share one component family:
  - TouchNumKeyboard : compact 3-col numpad with ENTER + ⌫ + .  (~320x340)
  - TouchTextKeyboard: QWERTY rows + integrated numpad column on the right
                       (~960x260) — matches commercial-POS reference

Use `install_touch_keyboard(root_widget)` to install an event filter that
auto-pops the appropriate keyboard whenever a QLineEdit gains keyboard focus.
Fields opt in via `lineedit.setProperty("touchKeyboard", "off"|"num"|"text")`;
otherwise `_guess_kind` heuristically picks based on objectName/placeholder.

Positioning is dialog-aware: when the focused input lives inside a QDialog,
the keyboard is placed outside that dialog's geometry (below → above → right →
left → fallback shrink). This guarantees the dialog's fields and buttons stay
visible while typing.
"""

from __future__ import annotations

from typing import Optional

import time

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ─── Visual constants (single source of truth) ───────────────────────────────

_NAVY = "#1B3A6B"
_KEY_BG = "#FFFFFF"
_KEY_BORDER = "#C8CFD7"
_KEY_PRESSED = "#E2E7EE"
_ACCENT_GREEN = "#27AE60"
_ACCENT_GREEN_HOVER = "#1F8B4D"
_KEY_DARK_BG = "#A8B0BA"
_HEADER_H = 26


def _key_qss(font_pt: int = 14, dark: bool = False) -> str:
    bg = _KEY_DARK_BG if dark else _KEY_BG
    fg = "white" if dark else "#1A1A1A"
    return (
        f"QPushButton {{"
        f" background-color: {bg}; color: {fg};"
        f" border: 1px solid {_KEY_BORDER}; border-radius: 6px;"
        f" font-size: {font_pt}pt; font-weight: bold;"
        f"}}"
        f"QPushButton:pressed {{ background-color: {_KEY_PRESSED}; }}"
    )


def _enter_qss(font_pt: int = 13) -> str:
    return (
        f"QPushButton {{"
        f" background-color: {_ACCENT_GREEN}; color: white;"
        f" border: 1px solid {_ACCENT_GREEN}; border-radius: 6px;"
        f" font-size: {font_pt}pt; font-weight: bold;"
        f"}}"
        f"QPushButton:pressed {{ background-color: {_ACCENT_GREEN_HOVER}; }}"
    )


# ─── Base ────────────────────────────────────────────────────────────────────

class _BaseKeyboard(QDialog):
    """Frameless tool window pinned outside the host dialog."""

    def __init__(self, target: QLineEdit, parent: Optional[QWidget] = None):
        super().__init__(parent or target.window())
        self._target = target
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setModal(False)
        self.setStyleSheet(
            f"QDialog {{ background-color: #ECEFF3; border: 1px solid {_KEY_BORDER}; }}"
            f"QLabel#kbHeader {{"
            f"  background: {_NAVY}; color: white;"
            f"  font-size: 9pt; font-weight: bold;"
            f"  padding: 2px 8px; border-top-left-radius: 4px;"
            f"  border-top-right-radius: 4px;"
            f"}}"
        )
        try:
            target.destroyed.connect(self.close)
        except Exception:
            pass
        self._build()

    def _build(self) -> None:
        raise NotImplementedError

    def _btn(self, text: str, *, font_pt: int = 14, dark: bool = False,
             min_w: int = 0, min_h: int = 44) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet(_key_qss(font_pt, dark=dark))
        if min_w:
            b.setMinimumWidth(min_w)
        b.setMinimumHeight(min_h)
        return b

    # ─── Send char to target ─────────────────────────────────────────────────

    def _send(self, ch: str) -> None:
        if self._target is None:
            return
        if ch == "BKSP":
            t = self._target.text()
            self._target.setText(t[:-1])
        elif ch == "CLR":
            self._target.clear()
        elif ch == "SPACE":
            self._target.insert(" ")
        elif ch == "TAB":
            try:
                self._target.focusNextChild()
            except Exception:
                pass
        elif ch == "ENTER" or ch == "DONE":
            # Tell global filter to suppress reopening for the next focus
            # change — Enter typically advances focus to the next input,
            # which would otherwise pop a fresh keyboard.
            global _ACTIVE_FILTER
            if _ACTIVE_FILTER is not None:
                _ACTIVE_FILTER._suppress_until = time.monotonic() + 0.4
            try:
                self._target.returnPressed.emit()
            except Exception:
                pass
            self.close()
        elif ch == "CLOSE":
            self.close()
        else:
            self._target.insert(ch)


# ─── Numeric-only compact pad ────────────────────────────────────────────────

class TouchNumKeyboard(_BaseKeyboard):
    """Compact 3-col digit pad. Buttons: 7-9, 4-6, 1-3, ., 0, ⌫, CLR, ENTER."""

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(4)

        head = QLabel("Numpad")
        head.setObjectName("kbHeader")
        head.setFixedHeight(_HEADER_H)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(head)

        g = QGridLayout()
        g.setSpacing(4)
        rows = [
            ["7", "8", "9"],
            ["4", "5", "6"],
            ["1", "2", "3"],
            [".", "0", "BKSP"],
        ]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                label = "⌫" if ch == "BKSP" else ch
                b = self._btn(label, font_pt=18, min_h=52)
                b.clicked.connect(lambda _ck=False, x=ch: self._send(x))
                g.addWidget(b, r, c)
        v.addLayout(g)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        clr = self._btn("CLR", font_pt=12, min_h=46)
        clr.clicked.connect(lambda: self._send("CLR"))
        bar.addWidget(clr, stretch=1)
        enter = QPushButton("Enter")
        enter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        enter.setStyleSheet(_enter_qss(font_pt=13))
        enter.setMinimumHeight(46)
        enter.clicked.connect(lambda: self._send("ENTER"))
        bar.addWidget(enter, stretch=2)
        v.addLayout(bar)

        self.setFixedSize(300, 340)


# ─── Combo QWERTY + numpad column ───────────────────────────────────────────

class TouchTextKeyboard(_BaseKeyboard):
    """QWERTY rows + integrated numpad column on the right."""

    ROWS = [
        list("qwertyuiop"),
        list("asdfghjkl"),
        list("zxcvbnm"),
    ]

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(4)

        head = QLabel("Keyboard")
        head.setObjectName("kbHeader")
        head.setFixedHeight(_HEADER_H)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(head)

        body = QHBoxLayout()
        body.setSpacing(8)

        # ── Left: QWERTY block ──
        left = QVBoxLayout()
        left.setSpacing(4)
        self._shift = False
        self._key_buttons: list[QPushButton] = []

        # Row 1: q-p + backspace
        r1 = QHBoxLayout()
        r1.setSpacing(3)
        for ch in self.ROWS[0]:
            b = self._btn(ch.upper(), font_pt=13, min_h=46)
            b.clicked.connect(lambda _ck=False, x=ch: self._send_letter(x))
            self._key_buttons.append(b)
            r1.addWidget(b, stretch=1)
        bksp = self._btn("⌫", font_pt=13, dark=True, min_h=46, min_w=70)
        bksp.clicked.connect(lambda: self._send("BKSP"))
        r1.addWidget(bksp, stretch=1)
        left.addLayout(r1)

        # Row 2: TAB + a-l + ' + Enter
        r2 = QHBoxLayout()
        r2.setSpacing(3)
        tab = self._btn("TAB", font_pt=10, dark=True, min_h=46, min_w=60)
        tab.clicked.connect(lambda: self._send("TAB"))
        r2.addWidget(tab, stretch=1)
        for ch in self.ROWS[1]:
            b = self._btn(ch.upper(), font_pt=13, min_h=46)
            b.clicked.connect(lambda _ck=False, x=ch: self._send_letter(x))
            self._key_buttons.append(b)
            r2.addWidget(b, stretch=1)
        apo = self._btn("'", font_pt=13, min_h=46)
        apo.clicked.connect(lambda: self._send("'"))
        r2.addWidget(apo, stretch=1)
        enter = QPushButton("⏎ Enter")
        enter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        enter.setStyleSheet(_enter_qss(font_pt=11))
        enter.setMinimumHeight(46)
        enter.setMinimumWidth(80)
        enter.clicked.connect(lambda: self._send("ENTER"))
        r2.addWidget(enter, stretch=2)
        left.addLayout(r2)

        # Row 3: shift + z-m + , . ? + shift
        r3 = QHBoxLayout()
        r3.setSpacing(3)
        sh1 = self._btn("⇧", font_pt=13, dark=True, min_h=46, min_w=58)
        sh1.clicked.connect(self._toggle_shift)
        r3.addWidget(sh1, stretch=1)
        for ch in self.ROWS[2]:
            b = self._btn(ch.upper(), font_pt=13, min_h=46)
            b.clicked.connect(lambda _ck=False, x=ch: self._send_letter(x))
            self._key_buttons.append(b)
            r3.addWidget(b, stretch=1)
        for sym in (",", ".", "?"):
            b = self._btn(sym, font_pt=13, min_h=46)
            b.clicked.connect(lambda _ck=False, x=sym: self._send(x))
            r3.addWidget(b, stretch=1)
        sh2 = self._btn("⇧", font_pt=13, dark=True, min_h=46, min_w=58)
        sh2.clicked.connect(self._toggle_shift)
        r3.addWidget(sh2, stretch=1)
        left.addLayout(r3)

        # Row 4: &123 + space + close
        r4 = QHBoxLayout()
        r4.setSpacing(3)
        sym = self._btn("&123", font_pt=11, dark=True, min_h=46, min_w=72)
        sym.clicked.connect(self._toggle_shift)  # cosmetic; still toggles caps
        r4.addWidget(sym, stretch=1)
        space = self._btn(" ", font_pt=12, min_h=46)
        space.clicked.connect(lambda: self._send("SPACE"))
        r4.addWidget(space, stretch=10)
        close = self._btn("✕", font_pt=12, dark=True, min_h=46, min_w=60)
        close.clicked.connect(lambda: self._send("CLOSE"))
        r4.addWidget(close, stretch=1)
        left.addLayout(r4)

        body.addLayout(left, stretch=4)

        # ── Right: integrated numpad column ──
        right = QGridLayout()
        right.setSpacing(4)
        digits = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
            ("0", 3, 0), (".", 3, 1), ("BKSP", 3, 2),
        ]
        for ch, r, c in digits:
            label = "⌫" if ch == "BKSP" else ch
            b = self._btn(label, font_pt=15, min_h=46, min_w=46)
            b.clicked.connect(lambda _ck=False, x=ch: self._send(x))
            right.addWidget(b, r, c)
        body.addLayout(right, stretch=1)

        outer.addLayout(body)
        self.setFixedSize(960, 260)

    def _send_letter(self, ch: str) -> None:
        self._send(ch.upper() if self._shift else ch)

    def _toggle_shift(self) -> None:
        self._shift = not self._shift
        flat = "".join("".join(r) for r in self.ROWS)
        for b, ch in zip(self._key_buttons, flat):
            b.setText(ch.upper() if self._shift else ch)


# ─── Auto-popup event filter ─────────────────────────────────────────────────

class _TouchKeyboardFilter(QObject):
    """Watches FocusIn events on QLineEdits and pops the right keyboard.

    Singleton lifecycle:
      - Only ONE keyboard instance globally at any time.
      - Opening a new keyboard always closes the previous.
      - The active keyboard is auto-closed on FocusOut (debounced 120ms),
        the host window's hide/destroy events, and on Enter (suppress flag
        prevents reopen when focus advances to the next input).
    """

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self._kb: Optional[_BaseKeyboard] = None
        self._kb_target_window: Optional[QWidget] = None
        self._suppress_until: float = 0.0
        # Debounced FocusOut close — gives the next input a brief window to
        # claim focus (without it, every Tab/click would close+reopen).
        self._focus_out_timer = QTimer()
        self._focus_out_timer.setSingleShot(True)
        self._focus_out_timer.setInterval(120)
        self._focus_out_timer.timeout.connect(self._close_if_no_focus_target)

    def eventFilter(self, obj, ev) -> bool:
        et = ev.type()
        if et == QEvent.Type.FocusIn and isinstance(obj, QLineEdit):
            # Suppress reopen briefly after Enter so focus chains don't
            # spawn a fresh keyboard.
            if time.monotonic() < self._suppress_until:
                return False
            opt = obj.property("touchKeyboard")
            if opt == "off":
                return False
            self._focus_out_timer.stop()
            self._open_for(obj, opt)
            return False

        if et == QEvent.Type.FocusOut and isinstance(obj, QLineEdit):
            # Defer close — if focus moves to another QLineEdit, the
            # FocusIn cancels the timer.
            if self._kb is not None:
                self._focus_out_timer.start()
            return False

        # Window-level events: if the host window is hidden / destroyed,
        # the keyboard belongs to a now-invisible surface — close it.
        if (et in (QEvent.Type.Hide, QEvent.Type.Close)
                and self._kb_target_window is not None
                and obj is self._kb_target_window):
            self._close_kb()
        return False

    def _open_for(self, target: QLineEdit, opt) -> None:
        # Close any prior keyboard first — guarantees singleton.
        self._close_kb()
        kind = opt if opt in ("num", "text") else self._guess_kind(target)
        cls = TouchNumKeyboard if kind == "num" else TouchTextKeyboard
        self._kb = cls(target, target.window())
        self._position(self._kb, target)
        # Track the host window so we can auto-close when it hides.
        win = target.window()
        if win is not None and win is not self._kb_target_window:
            if self._kb_target_window is not None:
                try:
                    self._kb_target_window.removeEventFilter(self)
                except Exception:
                    pass
            self._kb_target_window = win
            try:
                win.installEventFilter(self)
            except Exception:
                pass
        self._kb.show()

    def _close_if_no_focus_target(self) -> None:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            self._close_kb(); return
        focused = app.focusWidget()
        # If the new focus is a QLineEdit eligible for a keyboard, leave it
        # be — the FocusIn for that widget will (re)open the keyboard with
        # the correct kind. Otherwise close.
        if isinstance(focused, QLineEdit) and focused.property("touchKeyboard") != "off":
            return
        self._close_kb()

    def _close_kb(self) -> None:
        kb = self._kb
        self._kb = None
        if kb is not None:
            try:
                kb.close()
                kb.deleteLater()
            except Exception:
                pass

    @staticmethod
    def _guess_kind(le: QLineEdit) -> str:
        n = (le.objectName() or "").lower()
        ph = (le.placeholderText() or "").lower()
        for hint in ("price", "amount", "pin", "barcode", "qty", "cents", "number"):
            if hint in n or hint in ph:
                return "num"
        if le.echoMode() == QLineEdit.EchoMode.Password:
            return "num"
        return "text"

    @staticmethod
    def _position(kb: QWidget, target: QLineEdit) -> None:
        """Place keyboard outside any host dialog; never overlap.

        Order tried (first that fits without overlap wins): below dialog,
        above dialog, right of dialog, left of dialog, then fallback —
        bottom-of-screen pin + dialog max-height clamp so both stay visible.
        """
        screen = (target.screen() if hasattr(target, "screen") else
                  QGuiApplication.primaryScreen())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        kb.adjustSize()
        kb_w, kb_h = kb.width(), kb.height()
        gap = 8

        host = _TouchKeyboardFilter._find_host_dialog(target)
        if host is not None and host.isVisible():
            host_rect = host.frameGeometry()
            candidates = [
                # Below dialog
                QRect(host_rect.center().x() - kb_w // 2,
                      host_rect.bottom() + gap, kb_w, kb_h),
                # Above dialog
                QRect(host_rect.center().x() - kb_w // 2,
                      host_rect.top() - kb_h - gap, kb_w, kb_h),
                # Right of dialog
                QRect(host_rect.right() + gap,
                      host_rect.center().y() - kb_h // 2, kb_w, kb_h),
                # Left of dialog
                QRect(host_rect.left() - kb_w - gap,
                      host_rect.center().y() - kb_h // 2, kb_w, kb_h),
            ]
            for r in candidates:
                if avail.contains(r) and not r.intersects(host_rect):
                    kb.setGeometry(r)
                    return
            # Fallback: pin to bottom-center of available area + shrink dialog.
            x = avail.center().x() - kb_w // 2
            y = avail.bottom() - kb_h - 10
            kb.setGeometry(x, y, kb_w, kb_h)
            try:
                max_h = max(220, y - avail.top() - 20)
                if host.height() > max_h:
                    host.setMaximumHeight(max_h)
                    host.adjustSize()
            except Exception:
                pass
            return

        # No host dialog: place below the input, fall back to above.
        tl = target.mapToGlobal(target.rect().topLeft())
        br = target.mapToGlobal(target.rect().bottomRight())
        x = tl.x() + (br.x() - tl.x()) // 2 - kb_w // 2
        x = max(avail.left() + 10, min(avail.right() - kb_w - 10, x))
        below_y = br.y() + gap
        if below_y + kb_h <= avail.bottom() - 10:
            y = below_y
        else:
            y = max(avail.top() + 10, tl.y() - kb_h - gap)
        kb.setGeometry(x, y, kb_w, kb_h)

    @staticmethod
    def _find_host_dialog(target: QLineEdit) -> Optional[QDialog]:
        w = target.parentWidget()
        while w is not None:
            if isinstance(w, QDialog):
                return w
            w = w.parentWidget()
        return None


_FILTER_INSTALLED = False
_ACTIVE_FILTER: Optional["_TouchKeyboardFilter"] = None


def close_active_keyboard() -> None:
    """Close any open on-screen keyboard. Safe no-op if none open."""
    global _ACTIVE_FILTER
    if _ACTIVE_FILTER is None:
        return
    _ACTIVE_FILTER._close_kb()


def install_touch_keyboard(root: QWidget) -> None:
    """Install a single global filter on the QApplication. Idempotent."""
    global _FILTER_INSTALLED
    if _FILTER_INSTALLED:
        return
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    f = _TouchKeyboardFilter(app)
    app.installEventFilter(f)
    app._touch_kb_filter = f   # keep reference alive
    global _ACTIVE_FILTER
    _ACTIVE_FILTER = f
    _FILTER_INSTALLED = True
