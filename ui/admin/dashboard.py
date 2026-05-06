"""Admin dashboard — quick stats header + 2×3 nav cards.

Holds an internal QStackedWidget so each card → child screen.
Back button on each child returns to this dashboard view.
Logout button ends the admin session.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import db
from core.logger import get_logger
from core.models import User
from core.payment.detector import is_mock
from ui import styles

log = get_logger("ui.admin.dashboard")


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


# ─── Dashboard card ──────────────────────────────────────────────────────────

class DashboardCard(QPushButton):
    """Large nav card — icon + title + subtitle. Clickable like a button."""

    def __init__(
        self, *, icon: str, title: str, subtitle: str,
        color: str, name: str, parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName(name)
        self._icon = icon
        self._title = title
        self._subtitle = subtitle
        self._color = color
        self._build()

    def _build(self) -> None:
        self.setMinimumHeight(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._qss(self._color))
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(4)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel(self._icon)
        icon_lbl.setObjectName(f"{self.objectName()}_icon")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icf = QFont(styles.FONT_FAMILY, 36)
        icon_lbl.setFont(icf)
        icon_lbl.setStyleSheet("color: white; background: transparent;")
        v.addWidget(icon_lbl)

        title_lbl = QLabel(self._title)
        title_lbl.setObjectName(f"{self.objectName()}_title")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf = QFont(styles.FONT_FAMILY, 14); tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setStyleSheet("color: white; background: transparent;")
        v.addWidget(title_lbl)

        self._sub_lbl = QLabel(self._subtitle)
        self._sub_lbl.setObjectName(f"{self.objectName()}_sub")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont(styles.FONT_FAMILY, 11))
        self._sub_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.85); background: transparent;"
        )
        v.addWidget(self._sub_lbl)

    def update_subtitle(self, text: str) -> None:
        self._sub_lbl.setText(text)

    @staticmethod
    def _qss(color: str) -> str:
        return (
            f"QPushButton {{ background-color: {color};"
            f" border: none; border-radius: 10px; padding: 10px;"
            f" text-align: center; }}"
            f"QPushButton:hover {{ background-color: {color}; opacity: 0.9; }}"
            f"QPushButton:pressed {{ padding: 12px 8px 8px 12px; }}"
        )


# ─── Admin dashboard screen ──────────────────────────────────────────────────

class AdminDashboard(QWidget):
    """Admin home: stats header + 2×3 cards + back/logout."""

    logout_requested = pyqtSignal()
    register_requested = pyqtSignal()   # admin wants to switch to register

    def __init__(
        self,
        admin_user: User,
        *,
        terminal=None,
        sound_player=None,
        store: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("admin_dashboard")
        self.admin_user = admin_user
        self.terminal = terminal
        self.sound_player = sound_player
        self.store = store or {"name": "CityLink Convenience"}

        # Stack: dashboard view + child screens (mounted on demand)
        self._stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        # Build dashboard view (always at index 0)
        self._dashboard_view = QWidget()
        self._dashboard_view.setObjectName("admin_dash_view")
        self._build_dashboard(self._dashboard_view)
        self._stack.addWidget(self._dashboard_view)

        # Active child screen reference (for cleanup on back)
        self._active_child: Optional[QWidget] = None

        # Refresh stats periodically (every 30s) — keeps card subtitles live
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(30 * 1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ─── Dashboard view ──────────────────────────────────────────────────────

    def _build_dashboard(self, root: QWidget) -> None:
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_header())
        v.addWidget(self._build_stats_strip())
        v.addWidget(self._build_cards(), stretch=1)
        v.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("admin_dash_header")
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame#admin_dash_header {{ background-color: {styles.COLORS['navy']}; }}"
            f"QFrame#admin_dash_header QLabel {{ color: #FFFFFF;"
            f" background: transparent; font-weight: bold; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 6, 20, 6)
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title = QLabel(f"ADMIN — {self.admin_user.name}")
        title.setObjectName("admin_dash_title")
        f = QFont(styles.FONT_FAMILY, 17); f.setBold(True)
        title.setFont(f)
        h.addWidget(title)
        h.addStretch(1)

        self._store_label = QLabel(self.store.get("name", "CityLink"))
        self._store_label.setObjectName("admin_dash_store")
        sf = QFont(styles.FONT_FAMILY, 13); sf.setBold(True)
        self._store_label.setFont(sf)
        h.addWidget(self._store_label)
        return bar

    def _build_stats_strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("admin_stats_strip")
        strip.setFixedHeight(72)
        strip.setStyleSheet(
            f"QFrame#admin_stats_strip {{ background-color: {styles.COLORS['blue_mid']}; }}"
        )
        h = QHBoxLayout(strip)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(40)

        def _stat(label: str, value: str, name: str) -> tuple[QLabel, QLabel]:
            wrap = QVBoxLayout()
            lab = QLabel(label.upper())
            lab.setStyleSheet(
                "color: rgba(255,255,255,0.75); background: transparent;"
            )
            lab.setFont(QFont(styles.FONT_FAMILY, 9))
            val = QLabel(value); val.setObjectName(name)
            vf = QFont(styles.FONT_FAMILY, 18); vf.setBold(True)
            val.setFont(vf)
            val.setStyleSheet("color: white; background: transparent;")
            wrap.addWidget(lab)
            wrap.addWidget(val)
            wrap.setSpacing(2)
            cont = QWidget()
            cont.setLayout(wrap)
            cont.setStyleSheet("background: transparent;")
            h.addWidget(cont)
            return lab, val

        _, self._stat_sales = _stat("Today's Sales", "$0.00", "stat_sales")
        _, self._stat_txns = _stat("Transactions", "0", "stat_txns")
        _, self._stat_terminal = _stat("Terminal", "—", "stat_terminal")
        _, self._stat_deals = _stat("Active Deals", "0", "stat_deals")
        _, self._stat_low_inv = _stat("Low Inventory", "—", "stat_low_inv")
        h.addStretch(1)

        return strip

    def _build_cards(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet(f"background-color: {styles.COLORS['bg']};")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(40, 16, 40, 16)
        v.setSpacing(14)

        grid = QGridLayout()
        grid.setSpacing(16)
        for c in range(3):
            grid.setColumnStretch(c, 1)

        self._cards: dict[str, DashboardCard] = {}

        cards = [
            ("📦", "Inventory",    "—",     styles.COLORS["btn_hold"],     "card_inventory",  self._open_inventory),
            ("🎯", "Deals",        "—",     styles.COLORS["btn_lottery_s"], "card_deals",      self._open_deals),
            ("📊", "Reports",      "—",     styles.COLORS["btn_card"],     "card_reports",    self._open_reports),
            ("💰", "Cash Mgmt",    "—",     styles.COLORS["btn_cash"],     "card_cash",       self._open_cash_mgmt),
            ("👥", "Users",        "—",     styles.COLORS["btn_split"],    "card_users",      self._open_users),
            ("🔧", "Tools",        "Diagnostics · Restart · Reboot", "#E74C3C", "card_tools",  self._open_tools),
            ("⚙",  "Settings",    "Terminal · Sound · Backup", styles.COLORS["btn_void"], "card_settings",  self._open_settings),
        ]
        for i, (icon, title, sub, color, name, slot) in enumerate(cards):
            card = DashboardCard(icon=icon, title=title, subtitle=sub, color=color, name=name)
            card.clicked.connect(slot)
            r, c = divmod(i, 3)
            grid.addWidget(card, r, c)
            self._cards[name] = card
        v.addLayout(grid)
        v.addStretch(1)

        # Initial stats fill
        self._refresh_stats()
        return wrap

    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("admin_dash_footer")
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"QFrame#admin_dash_footer {{ background-color: {styles.COLORS['navy']}; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 6, 20, 6)
        h.addStretch(1)
        # "Open Register" — admin can switch to cashier surface without
        # logging out (admin role inherits cashier privileges).
        reg_btn = QPushButton("Open Register")
        reg_btn.setObjectName("admin_dash_register")
        reg_btn.setMinimumSize(180, 36)
        rf = QFont(styles.FONT_FAMILY, 12); rf.setBold(True)
        reg_btn.setFont(rf)
        reg_btn.setStyleSheet(
            "QPushButton { background-color: rgba(255,255,255,0.1); color: white;"
            " border: 1px solid white; border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.2); }"
        )
        reg_btn.clicked.connect(self.register_requested.emit)
        h.addWidget(reg_btn)
        h.addSpacing(8)

        b = QPushButton("Lock — Back to PIN")
        b.setObjectName("admin_dash_logout")
        b.setMinimumSize(220, 36)
        f = QFont(styles.FONT_FAMILY, 12); f.setBold(True)
        b.setFont(f)
        b.setStyleSheet(
            "QPushButton { background-color: rgba(255,255,255,0.1); color: white;"
            " border: 1px solid white; border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.2); }"
        )
        b.clicked.connect(self.logout_requested.emit)
        h.addWidget(b)
        return bar

    # ─── Stats refresh ───────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        try:
            today = date.today().isoformat()
            row = db.conn().execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS total
                   FROM transactions
                   WHERE status = 'completed'
                     AND date(created_at, 'localtime') = ?""",
                (today,),
            ).fetchone()
            n_txns = row["n"]
            sales = row["total"]
            self._stat_sales.setText(_money(sales))
            self._stat_txns.setText(str(n_txns))

            n_deals = len(db.list_active_deals())
            self._stat_deals.setText(str(n_deals))

            n_items = len([r for r in db.list_all_items(active_only=True)])
            n_inactive = len(db.list_all_items(active_only=False)) - n_items

            # Card subtitles
            self._cards["card_inventory"].update_subtitle(
                f"{n_items} active items" + (f"  ·  {n_inactive} inactive" if n_inactive else "")
            )
            self._cards["card_deals"].update_subtitle(
                f"{n_deals} active deal{'s' if n_deals != 1 else ''}"
            )
            self._cards["card_reports"].update_subtitle("PDF reports + tax summary")
            self._cards["card_cash"].update_subtitle("Drops · petty · till count")
            n_users = len(db.list_users(active_only=True))
            self._cards["card_users"].update_subtitle(
                f"{n_users} active staff member{'s' if n_users != 1 else ''}"
            )

            # Terminal status
            if self.terminal is not None and self.terminal.is_connected():
                if is_mock(self.terminal):
                    self._stat_terminal.setText("● MOCK")
                    self._stat_terminal.setStyleSheet(
                        f"color: {styles.COLORS['warning']};"
                        " background: transparent; font-weight: bold;"
                    )
                else:
                    self._stat_terminal.setText("● TCP")
                    self._stat_terminal.setStyleSheet(
                        f"color: {styles.COLORS['success']};"
                        " background: transparent; font-weight: bold;"
                    )
            else:
                self._stat_terminal.setText("● OFFLINE")
                self._stat_terminal.setStyleSheet(
                    f"color: {styles.COLORS['danger']};"
                    " background: transparent; font-weight: bold;"
                )

            # Low inventory placeholder (Phase 2 feature when stock is tracked)
            self._stat_low_inv.setText("—")
        except Exception:
            log.exception("admin dashboard stats refresh failed")

    # ─── Navigation ──────────────────────────────────────────────────────────

    def _show_screen(self, screen: QWidget) -> None:
        screen.back_requested.connect(self._show_dashboard_view)
        self._stack.addWidget(screen)
        self._stack.setCurrentWidget(screen)
        self._active_child = screen

    def _show_dashboard_view(self) -> None:
        if self._active_child is not None:
            self._stack.removeWidget(self._active_child)
            self._active_child.deleteLater()
            self._active_child = None
        self._stack.setCurrentWidget(self._dashboard_view)
        self._refresh_stats()

    def _open_inventory(self) -> None:
        from ui.admin.inventory import InventoryScreen
        self._show_screen(InventoryScreen(admin_name=self.admin_user.name))

    def _open_deals(self) -> None:
        from ui.admin.deals_admin import DealsAdminScreen
        self._show_screen(DealsAdminScreen())

    def _open_reports(self) -> None:
        from ui.admin.reports import AdminReportsScreen
        self._show_screen(AdminReportsScreen(store=self.store))

    def _open_cash_mgmt(self) -> None:
        from ui.admin.cash_management import CashManagementScreen
        self._show_screen(CashManagementScreen(admin_name=self.admin_user.name))

    def _open_users(self) -> None:
        from ui.admin.users import UsersAdminScreen
        self._show_screen(UsersAdminScreen(admin_user=self.admin_user))

    def _open_settings(self) -> None:
        from ui.admin.settings import SettingsScreen
        self._show_screen(SettingsScreen())

    def _open_tools(self) -> None:
        from ui.admin.tools import ToolsScreen
        # _show_screen already wires back_requested → _show_dashboard_view.
        self._show_screen(ToolsScreen(admin_name=self.admin_user.name))
