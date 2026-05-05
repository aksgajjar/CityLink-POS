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


# ─── Admin dashboard stub ────────────────────────────────────────────────────

class AdminDashboard(QWidget):
    """Placeholder admin home. Real dashboard built in Phase 1 step 27."""

    logout_requested = pyqtSignal()

    def __init__(self, admin_user: User, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("admin_dashboard")
        self._build(admin_user)

    def _build(self, admin_user: User) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 40, 40, 40)
        v.setSpacing(16)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(f"ADMIN — {admin_user.name}")
        title.setObjectName("admin_title")
        f = QFont(styles.FONT_FAMILY, 24); f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        msg = QLabel(
            "Admin dashboard not built yet.\n\n"
            "Phase 1 steps 22-27 will add: Inventory · Deals · Cash Mgmt ·\n"
            "Reports · Users · Terminal config · Store settings."
        )
        msg.setObjectName("admin_msg")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setFont(QFont(styles.FONT_FAMILY, 12))
        v.addWidget(msg)

        v.addStretch(1)

        back = QPushButton("Lock — Back to PIN")
        back.setObjectName("admin_back")
        back.setMinimumSize(240, 56)
        bf = QFont(styles.FONT_FAMILY, 14); bf.setBold(True)
        back.setFont(bf)
        back.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_void']}; color: white;"
            f" border: none; border-radius: 6px; padding: 10px 24px; }}"
        )
        back.clicked.connect(self.logout_requested.emit)
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignCenter)


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

        # Sound feedback (click/success/error). WAVs auto-generated at boot.
        snd_cfg = config.get("sound", {}) or {}
        self.sound_player: SoundPlayer = SoundPlayer(
            enabled=bool(snd_cfg.get("enabled", True)),
            volume_pct=int(snd_cfg.get("volume", 80)),
        )

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
        if user.role == "admin":
            self._show_admin()
        else:
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
        self._admin = AdminDashboard(self._current_user)
        self._admin.logout_requested.connect(self._on_logout)
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
        """Cashier tapped Admin — log them out and require admin PIN re-auth."""
        log.info("admin requested by cashier %s — locking",
                 self._current_user.name if self._current_user else "?")
        self._on_logout()

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

    if args.seed_admin:
        _seed_admin_if_needed(args.seed_admin)

    # Generate sound assets if missing (one-time, idempotent)
    try:
        generate_sounds_if_missing()
    except Exception:
        log.exception("sound asset generation failed; continuing without sound")

    app = QApplication(sys.argv)
    app.setStyleSheet(styles.get_stylesheet())
    win = MainWindow(config)
    # Install app-wide click sound filter
    sound_filter = ClickSoundFilter(win.sound_player)
    app.installEventFilter(sound_filter)
    win._sound_filter_ref = sound_filter   # keep alive for app lifetime
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
