"""CityLink POS entry point.

Boots logger + DB, loads config.json, applies BC tax rates, then runs the Qt
app. The shell is a `QMainWindow` holding a `QStackedWidget` that switches
between LoginScreen / cashier register / admin dashboard.

Roles:
  - cashier login   → OpenShiftDialog (or resume open shift) → RegisterScreen
  - admin login     → AdminDashboard (stub for now; step 27 builds real one)

Lock paths (cart auto-held, returns to PIN):
  - footer Lock button (logout_requested)
  - footer Admin button (admin_requested) — current cashier session ends; admin
    must PIN in to access admin
  - inactivity timer fires after `features.inactivity_timeout_seconds`

Run:
    .venv/bin/python main.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import db, tax
from core.cart import Cart
from core.logger import get_logger, setup_logger
from core.models import User
from core.payment.base import PaymentTerminal
from core.payment.detector import get_terminal
from core.sound import ClickSoundFilter, SoundPlayer, generate_sounds_if_missing

from ui import styles
from ui.cashier.numpad import MODE_PRICE, Numpad
from ui.cashier.register import RegisterScreen
from ui.login import LoginScreen


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
DEFAULT_CONFIG: dict = {
    "store": {
        "name": "CityLink Convenience",
        "location_id": "LOC001",
        "address": "",
        "terminal_id": "T001",
    },
    "tax": {
        "gst_rate": 0.05,
        "pst_rate": 0.07,
        "bottle_deposit_355ml": 0.10,
        "bottle_deposit_1L": 0.25,
        "bag_charge_cents": 25,
    },
    "payment": {
        "terminal_type": "mock",
        "connection": "tcp",
        "host": "192.168.1.100",
        "port": 9100,
        "timeout_seconds": 60,
    },
    "features": {
        "lottery_enabled": True,
        "bottle_deposit_enabled": True,
        "bag_charge_enabled": True,
        "require_pin_for_void": True,
        "require_pin_for_price_override": True,
        "inactivity_timeout_seconds": 120,
    },
    "sound": {
        "enabled": True,
        "volume": 80,
    },
}

log = get_logger("main")


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict:
    """Read config.json. Falls back to DEFAULT_CONFIG if missing/invalid."""
    if not path.exists():
        log.warning("config.json missing — using defaults")
        return DEFAULT_CONFIG
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("config.json invalid — using defaults")
        return DEFAULT_CONFIG


def apply_tax_rates_from_config(cfg: dict) -> None:
    t = cfg.get("tax", {})
    tax.set_rates(
        gst_rate=t.get("gst_rate"),
        pst_rate=t.get("pst_rate"),
        bottle_deposit_355ml=t.get("bottle_deposit_355ml"),
        bottle_deposit_1L=t.get("bottle_deposit_1L"),
        bag_charge_cents=t.get("bag_charge_cents"),
    )


# ─── Shift open dialog ───────────────────────────────────────────────────────

class OpenShiftDialog(QDialog):
    """Cashier enters opening cash float. Numpad in price mode."""

    def __init__(self, cashier_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("open_shift_dialog")
        self.setWindowTitle("Open Shift")
        self.setModal(True)
        self.setMinimumSize(400, 520)
        self.float_cents: int = 0
        self._build(cashier_name)

    def _build(self, cashier_name: str) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        title = QLabel(f"Welcome, {cashier_name}")
        title.setObjectName("open_shift_title")
        f = QFont(styles.FONT_FAMILY, 16); f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        sub = QLabel("Enter opening cash float:")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont(styles.FONT_FAMILY, 12))
        v.addWidget(sub)

        self.numpad = Numpad(mode=MODE_PRICE, with_ok=False)
        v.addWidget(self.numpad, stretch=1)

        h = QHBoxLayout()
        skip = QPushButton("Skip ($0)")
        skip.setObjectName("open_shift_skip")
        skip.clicked.connect(self._on_skip)
        ok = QPushButton("Start Shift")
        ok.setObjectName("open_shift_ok")
        ok.setDefault(True)
        ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }}"
        )
        ok.clicked.connect(self._on_ok)
        h.addWidget(skip)
        h.addWidget(ok)
        v.addLayout(h)

    def _on_skip(self) -> None:
        self.float_cents = 0
        self.accept()

    def _on_ok(self) -> None:
        self.float_cents = self.numpad.current_cents()
        self.accept()


# Admin dashboard imported from ui.admin.dashboard (see _show_admin)


# ─── Main window ─────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """App shell: routes login → register/admin, manages inactivity, holds carts."""

    INACTIVITY_CHECK_MS = 5000   # poll interval for inactivity check

    def __init__(self, config: dict):
        super().__init__()
        self.setObjectName("main_window")
        self.setWindowTitle("CityLink POS")
        self.setMinimumSize(1280, 800)

        self.config = config
        self.store_name: str = config.get("store", {}).get("name", "CityLink Convenience")
        self.inactivity_seconds: int = int(
            config.get("features", {}).get("inactivity_timeout_seconds", 120)
        )

        # Build payment terminal once at app startup (mock / ingenico / pax).
        # Returned regardless of connect outcome — UI inspects is_connected().
        self.terminal: PaymentTerminal = get_terminal(config)

        # Sound feedback (click/success/error + payment-completion MP3s).
        snd_cfg = config.get("sound", {}) or {}
        self.sound_player: SoundPlayer = SoundPlayer(
            enabled=bool(snd_cfg.get("enabled", True)),
            volume_pct=int(snd_cfg.get("volume", 80)),
        )
        # Bind centralized sound manager so any module can call
        # sound_manager.play_cash_sound() / play_card_sound() without holding
        # a SoundPlayer reference.
        try:
            from core import sound_manager
            sound_manager.bind(self.sound_player)
            sound_manager.preload_sounds()
        except Exception:
            log.exception("sound_manager bind failed")

        self.setStyleSheet(styles.get_stylesheet())

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self._cart: Optional[Cart] = None
        self._current_user: Optional[User] = None
        self._current_shift_id: Optional[int] = None
        self._login: Optional[LoginScreen] = None
        self._register: Optional[RegisterScreen] = None
        self._admin: Optional[AdminDashboard] = None

        self._last_activity = time.time()
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setInterval(self.INACTIVITY_CHECK_MS)
        self._inactivity_timer.timeout.connect(self._check_inactivity)

        self._show_login()

    # ─── Routing ─────────────────────────────────────────────────────────────

    def _show_login(self) -> None:
        if self._login is None:
            self._login = LoginScreen(store_name=self.store_name)
            self._login.login_succeeded.connect(self._on_login_succeeded)
            self.stack.addWidget(self._login)
        self.stack.setCurrentWidget(self._login)
        self._inactivity_timer.stop()
        self._login.setFocus()

    def _on_login_succeeded(self, user: User) -> None:
        log.info("login OK: %s (role=%s)", user.name, user.role)
        self._current_user = user
        # Admins start in the register so they can ring sales immediately,
        # then jump to admin dashboard via the footer button without
        # re-entering their PIN. Cashiers go straight to register.
        self._show_cashier_flow()

    def _show_cashier_flow(self) -> None:
        # Resume open shift if one exists; otherwise prompt for opening float.
        existing = db.get_open_shift(self._current_user.id)
        if existing is not None:
            self._current_shift_id = existing["id"]
            log.info("resuming open shift id=%s", self._current_shift_id)
        else:
            dlg = OpenShiftDialog(self._current_user.name, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                # User dismissed; abort login (back to PIN)
                self._on_logout()
                return
            try:
                self._current_shift_id = db.open_shift(
                    self._current_user.id, self._current_user.name, dlg.float_cents
                )
                log.info("opened shift %s float=%s", self._current_shift_id, dlg.float_cents)
            except Exception:
                log.exception("open_shift failed")
                QMessageBox.critical(self, "POS", "Failed to open shift. See errors.log.")
                self._show_login()
                return

        if self._cart is None:
            self._cart = Cart()

        self._register = RegisterScreen(
            cart=self._cart,
            cashier=self._current_user,
            shift_id=self._current_shift_id,
            store_name=self.store_name,
            terminal=self.terminal,
            sound_player=self.sound_player,
        )
        self._register.logout_requested.connect(self._on_logout)
        self._register.admin_requested.connect(self._on_admin_requested)
        self.stack.addWidget(self._register)
        self.stack.setCurrentWidget(self._register)
        self._register.setFocus()
        self._restart_inactivity_timer()

    def _show_admin(self) -> None:
        from ui.admin.dashboard import AdminDashboard as _AdminDashboard
        self._admin = _AdminDashboard(
            self._current_user,
            terminal=self.terminal,
            sound_player=self.sound_player,
            store=self.config.get("store", {}),
        )
        self._admin.logout_requested.connect(self._on_logout)
        self._admin.register_requested.connect(self._on_register_requested)
        self.stack.addWidget(self._admin)
        self.stack.setCurrentWidget(self._admin)
        self._restart_inactivity_timer()

    # ─── Lock / logout / admin-switch ────────────────────────────────────────

    def _on_logout(self) -> None:
        """Lock screen: hold cart, tear down active screen, return to PIN.

        The shift stays OPEN — same cashier resumes it on next PIN entry.
        Admin closes shifts via EOD report (Phase 1 step 25).
        """
        self._auto_hold_cart()
        self._teardown_active()
        self._show_login()

    def _on_admin_requested(self) -> None:
        """Admin tapped Admin from register — switch to admin dashboard
        without re-PIN. Cashiers (non-admin) get locked out → PIN screen.
        """
        u = self._current_user
        if u is not None and u.role == "admin":
            log.info("admin %s switching: register → dashboard", u.name)
            # Hide the register stack entry but keep its instance + cart
            # state alive so we can return to it via _on_register_requested.
            self._show_admin_overlay()
            return
        log.info("non-admin tapped Admin — locking to PIN")
        self._on_logout()

    def _on_register_requested(self) -> None:
        """Admin pressed 'Open Register' on the dashboard — return to the
        cashier surface without tearing down its state."""
        if self._register is None:
            self._show_cashier_flow()
            return
        if self._admin is not None:
            self.stack.removeWidget(self._admin)
            self._admin.deleteLater()
            self._admin = None
        self.stack.setCurrentWidget(self._register)
        self._register.setFocus()
        self._restart_inactivity_timer()

    def _show_admin_overlay(self) -> None:
        """Mount AdminDashboard on top of the existing register session."""
        from ui.admin.dashboard import AdminDashboard as _AdminDashboard
        if self._admin is not None:
            # Already mounted — just bring forward.
            self.stack.setCurrentWidget(self._admin)
            self._restart_inactivity_timer()
            return
        self._admin = _AdminDashboard(
            self._current_user,
            terminal=self.terminal,
            sound_player=self.sound_player,
            store=self.config.get("store", {}),
        )
        self._admin.logout_requested.connect(self._on_logout)
        self._admin.register_requested.connect(self._on_register_requested)
        self.stack.addWidget(self._admin)
        self.stack.setCurrentWidget(self._admin)
        self._restart_inactivity_timer()

    def _auto_hold_cart(self) -> None:
        if self._cart is None or self._cart.is_empty():
            return
        cashier = self._current_user.name if self._current_user else "system"
        try:
            db.hold_transaction(self._cart.to_json(), cashier, label="auto-hold")
            log.info("auto-held cart on lock (%d lines)", len(self._cart.lines))
        except Exception:
            log.exception("auto-hold failed")
        self._cart.clear()

    def _teardown_active(self) -> None:
        if self._register is not None:
            self.stack.removeWidget(self._register)
            self._register.deleteLater()
            self._register = None
        if self._admin is not None:
            self.stack.removeWidget(self._admin)
            self._admin.deleteLater()
            self._admin = None
        self._inactivity_timer.stop()
        self._current_user = None
        # Keep _cart instance for reuse; it's cleared by _auto_hold_cart already.
        self._current_shift_id = None

    # ─── Inactivity ──────────────────────────────────────────────────────────

    def _restart_inactivity_timer(self) -> None:
        self._mark_activity()
        self._inactivity_timer.start()

    def _mark_activity(self) -> None:
        self._last_activity = time.time()

    def _check_inactivity(self) -> None:
        idle = time.time() - self._last_activity
        if idle >= self.inactivity_seconds:
            log.info("inactivity timeout (%.0fs idle ≥ %ds limit) — locking",
                     idle, self.inactivity_seconds)
            self._on_logout()

    def event(self, ev: QEvent) -> bool:
        # Treat any user input as activity
        t = ev.type()
        if t in (
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.Wheel,
        ):
            self._mark_activity()
        return super().event(ev)

    def closeEvent(self, event) -> None:
        try:
            if self.terminal is not None:
                self.terminal.disconnect()
        except Exception:
            log.exception("terminal disconnect on close failed")
        super().closeEvent(event)


# ─── Entry point ─────────────────────────────────────────────────────────────

def _seed_admin_if_needed(pin: str) -> None:
    """Create an 'Admin' user with the given PIN if no admin already exists."""
    admins = [u for u in db.list_users(active_only=False) if u["role"] == "admin"]
    if admins:
        log.info("--seed-admin: %d admin user(s) already exist; skipping", len(admins))
        return
    uid = db.create_user("Admin", pin, role="admin")
    log.info("--seed-admin: created admin user id=%s with provided PIN", uid)


def _seed_default_users_if_empty() -> None:
    """First-run safety net: ensure the store has a usable login.

    On a fresh DB (e.g. cloned repo, new device) the users table is empty,
    so every PIN entry fails with 'Wrong PIN'. We seed two factory default
    accounts so the cashier can log in immediately:

        Admin   PIN 1234   role=admin
        Cashier PIN 9999   role=cashier

    Idempotent — only runs when the users table is empty (covers both
    'never had any user' and 'admin deactivated and no cashier' cases).
    """
    try:
        existing = db.list_users(active_only=False)
    except Exception:
        log.exception("default-user seed: list_users failed; skipping")
        return
    if existing:
        return
    try:
        db.create_user("Admin", "1234", role="admin")
        db.create_user("Cashier", "9999", role="cashier")
        log.info("seeded factory default users (Admin/1234, Cashier/9999)")
    except Exception:
        log.exception("default-user seed: create_user failed")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="citylink-pos", description="CityLink POS")
    p.add_argument(
        "--seed-admin",
        metavar="PIN",
        dest="seed_admin",
        default=None,
        help="Create admin user 'Admin' with the given PIN if no admin exists, then exit-or-continue.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    setup_logger()
    log.info("=== CityLink POS startup ===")
    args = parse_args(argv)
    config = load_config()
    apply_tax_rates_from_config(config)
    db.init_db()

    # Factory default users — only inserted when the users table is empty.
    _seed_default_users_if_empty()

    if args.seed_admin:
        _seed_admin_if_needed(args.seed_admin)

    # Generate sound assets if missing (one-time, idempotent)
    try:
        generate_sounds_if_missing()
    except Exception:
        log.exception("sound asset generation failed; continuing without sound")

    app = QApplication(sys.argv)
    # Force Fusion style — overrides macOS native renderer so dark-mode
    # appearance + per-system widget chrome don't override our QSS. Same
    # rendering on Intel + Apple Silicon Macs and Windows.
    try:
        from PyQt6.QtWidgets import QStyleFactory
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        log.exception("Fusion style apply failed; falling back to default")
    # Force light-mode palette so macOS dark mode doesn't bleed dark
    # backgrounds into our light-themed dialogs.
    try:
        from PyQt6.QtGui import QColor, QPalette
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#F4F6F9"))
        pal.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.Text, QColor("#1A1A1A"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#1A1A1A"))
        pal.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor("#1A1A1A"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#2E5BA8"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#FAFBFC"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#1A1A1A"))
        app.setPalette(pal)
    except Exception:
        log.exception("palette apply failed")
    app.setStyleSheet(styles.get_stylesheet())
    # Touch-only deployment: auto-pop on-screen keyboard for QLineEdit focus.
    try:
        from ui.cashier.touch_keyboard import install_touch_keyboard
        install_touch_keyboard(app)
    except Exception:
        log.exception("touch keyboard install failed")
    win = MainWindow(config)
    # Install app-wide click sound filter
    sound_filter = ClickSoundFilter(win.sound_player)
    app.installEventFilter(sound_filter)
    win._sound_filter_ref = sound_filter   # keep alive for app lifetime
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
