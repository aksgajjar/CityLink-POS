"""Cashier register screen — main POS view.

Layout (per .claude/ui.md):
  ┌─────── header (navy): logo · store · cashier · time · terminal dot ───────┐
  │                                                                            │
  │                    deals banner (yellow stub for now)                      │
  │  ┌──────────────────┬──────────────────────────────────────────────────┐  │
  │  │                  │  DepartmentGrid (top)                            │  │
  │  │    CartWidget    ├──────────────────────────────────────────────────┤  │
  │  │     (40%)        │  Numpad (with_ok=False)                          │  │
  │  │                  ├──────────────────────────────────────────────────┤  │
  │  │                  │  Action buttons grid                              │  │
  │  └──────────────────┴──────────────────────────────────────────────────┘  │
  │            footer:  Menu · Calculator · Receipts · Reprint · EOD · Admin  │
  └────────────────────────────────────────────────────────────────────────────┘

Cash payment is wired end-to-end. Card flow is a stub dialog for checkpoint 1.

Barcode scanner: keyPressEvent buffers printable chars, processes on Enter.
Manual price entry: type price on numpad, click any dept button (not ALL).
"""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import db
from core.cart import Cart
from core.departments import DEPT_BY_ID
from core.logger import get_logger
from core.models import CartItem, Transaction, User
from core.payment.base import (
    PaymentRequest,
    PaymentResponse,
    PaymentTerminal,
    RESULT_DECLINED,
    RESULT_TIMEOUT,
)
from core.payment.detector import is_mock
from ui import styles
from ui.cashier.cart_widget import CartWidget
from ui.cashier.departments import ALL_ID
# (Numpad widget intentionally not imported — register has an integrated inline numpad)

log = get_logger("ui.register")


class RegisterScreen(QWidget):
    """Cashier register screen. Cash flow end-to-end; other actions stubbed."""

    logout_requested = pyqtSignal()
    admin_requested = pyqtSignal()

    def __init__(
        self,
        cart: Cart,
        cashier: User,
        shift_id: Optional[int] = None,
        store_name: str = "CityLink Convenience",
        terminal: Optional[PaymentTerminal] = None,
        sound_player=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("register_screen")
        self.cart = cart
        self.cashier = cashier
        self.shift_id = shift_id
        self.store_name = store_name
        self.terminal: Optional[PaymentTerminal] = terminal
        self.sound_player = sound_player
        self._barcode_buffer = ""
        # Tracks the most recent dept-tap-and-add so a SECOND tap on the same
        # dept (numpad empty) acts as undo. Cleared on any other cart mutation.
        # Format: (dept_id, line_index).
        self._last_dept_add: Optional[tuple] = None
        # Most recently completed transaction — used by the Reprint footer button.
        self._last_txn: Optional[Transaction] = None
        self._last_tid: Optional[int] = None
        # Split payment running tally — cash portion paid before final tender.
        # Reset on cart.clear / hold / successful finalize.
        self._cash_partial_cents: int = 0
        # Guards against double-tap on Cash/Card while a sale is being saved
        # to DB or routed to the card worker. Cleared after finalize / error.
        self._payment_locked: bool = False

        # Card payment state (QThread + worker held during a transaction)
        self._payment_thread: Optional[QThread] = None
        self._payment_worker: Optional["PaymentWorker"] = None
        self._card_sheet: Optional["CardPaymentSheet"] = None
        self._pending_card_req: Optional[PaymentRequest] = None

        self._build_ui()
        self._wire_clock()
        self._wire_signals()

        # Receive key events without an inner widget stealing focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ─── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_deals_banner())

        body = QHBoxLayout()
        body.setContentsMargins(8, 8, 12, 8)   # extra right margin so CLR doesn't clip
        body.setSpacing(8)
        body.addWidget(self._build_left_panel(), stretch=45)
        body.addWidget(self._build_right_panel(), stretch=55)

        body_holder = QWidget()
        body_holder.setLayout(body)
        root.addWidget(body_holder, stretch=1)

        root.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("header")
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame#header {{ background-color: #1B3A6B; }}"
            f"QFrame#header QLabel {{ color: #FFFFFF; background: transparent;"
            f" font-weight: bold; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 6, 16, 6)
        h.setSpacing(12)

        # Left: cashier name (large) + clock (large).
        cashier = QLabel(self.cashier.name)
        cashier.setObjectName("hdr_cashier")
        cf = QFont(styles.FONT_FAMILY, 14); cf.setBold(True)
        cashier.setFont(cf)

        self._clock_label = QLabel("")
        self._clock_label.setObjectName("hdr_clock")
        kf = QFont(styles.FONT_FAMILY, 14); kf.setBold(True)
        self._clock_label.setFont(kf)
        self._update_clock()

        self._terminal_dot = QLabel()
        self._terminal_dot.setObjectName("hdr_terminal_dot")
        self._terminal_dot.setFont(QFont(styles.FONT_FAMILY, 11))
        self._refresh_terminal_dot()

        # Center: logo (image preferred, text fallback). Store-name removed.
        logo_path = Path(__file__).resolve().parents[2] / "assets" / "logo.png"
        title = QLabel()
        title.setObjectName("hdr_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if logo_path.exists():
            from PyQt6.QtGui import QPixmap
            pm = QPixmap(str(logo_path)).scaledToHeight(44, Qt.TransformationMode.SmoothTransformation)
            title.setPixmap(pm)
        else:
            title.setText("CITYLINK")
            tf = QFont(styles.FONT_FAMILY, 18); tf.setBold(True)
            title.setFont(tf)

        # Right: Login / Logout / LOCK — all return to PIN screen via existing
        # logout_requested signal. (LOCK is the canonical name.)
        def _hdr_btn(label: str, name: str) -> QPushButton:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(36)
            bf = QFont(styles.FONT_FAMILY, 11); bf.setBold(True)
            b.setFont(bf)
            b.setStyleSheet(
                "QPushButton { background-color: rgba(255,255,255,0.1);"
                " color: white; border: 1px solid rgba(255,255,255,0.5);"
                " border-radius: 4px; padding: 4px 14px; }"
                "QPushButton:hover { background-color: rgba(255,255,255,0.25); }"
            )
            b.clicked.connect(self.logout_requested.emit)
            return b

        h.addWidget(cashier)
        h.addWidget(self._clock_label)
        h.addWidget(self._terminal_dot)
        h.addStretch(1)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(_hdr_btn("Login", "hdr_login"))
        h.addWidget(_hdr_btn("Logout", "hdr_logout"))
        h.addWidget(_hdr_btn("🔒 LOCK", "hdr_lock"))
        return bar

    def _build_deals_banner(self) -> QWidget:
        self.deals_banner = DealsBanner()
        return self.deals_banner

    def _refresh_deals_banner(self) -> None:
        """Pull active deals + hints; update the banner. Cheap — runs after every cart change."""
        try:
            from core.models import Deal as _Deal
            active = [_Deal.from_row(r) for r in db.list_active_deals()]
            hints = self.cart.deal_hints(deals=active)
            triggered_ids = {ln.deal_id for ln in self.cart.lines if ln.deal_id is not None}
            self.deals_banner.update_deals(active, hints, triggered_ids)
        except Exception:
            log.exception("deals banner refresh failed")

    def _on_cart_inline_mutation(self, *_args) -> None:
        """Single hook for inline cart edits (qty +/-, remove). Clears the
        dept-toggle marker AND debounces the deals banner refresh."""
        self._last_dept_add = None
        self._schedule_deals_refresh()

    def _schedule_deals_refresh(self) -> None:
        """Debounce rapid cart-mutation signals into one banner refresh (80ms).

        Without this, holding +/− or rapid scanner bursts hit the DB every
        keystroke. With it, repeated signals collapse to a single delayed call.
        """
        if not hasattr(self, "_deals_refresh_timer") or self._deals_refresh_timer is None:
            self._deals_refresh_timer = QTimer(self)
            self._deals_refresh_timer.setSingleShot(True)
            self._deals_refresh_timer.timeout.connect(self._refresh_deals_banner)
        self._deals_refresh_timer.start(80)

    # ─── Left panel: cart only (Hold/Cancel live inside cart_widget now) ─────

    def _build_left_panel(self) -> QWidget:
        self.cart_widget = CartWidget(self.cart)
        self.cart_widget.setObjectName("register_cart")
        self.cart_widget.hold_clicked.connect(self._on_hold)
        self.cart_widget.cancel_clicked.connect(self._on_clear_cart)
        self.cart_widget.print_receipt_clicked.connect(self._on_print_button)
        self.cart_widget.restore_held_requested.connect(self._on_restore_held)
        # Refresh deals banner whenever the cart changes via inline controls.
        # Debounced — under fast +/- or scanner burst, collapses to one DB hit.
        # Also clears _last_dept_add so dept-tap-undo only works as the *next*
        # action after a dept-add (not after the user fiddled with the cart).
        self.cart_widget.qty_changed.connect(self._on_cart_inline_mutation)
        self.cart_widget.item_removed.connect(self._on_cart_inline_mutation)
        # Sync HELD pill with DB on load
        self._refresh_held_count()
        # Initial deals-banner population (deferred until banner exists)
        QTimer.singleShot(0, self._refresh_deals_banner)
        return self.cart_widget

    def _refresh_held_count(self) -> None:
        try:
            self.cart_widget.update_held_count(len(db.list_held()))
        except Exception:
            log.exception("held count refresh failed")

    def _on_restore_held(self, held_id: int) -> None:
        if not self.cart.is_empty():
            if not self._confirm("Replace current cart with held cart?"):
                return
            self.cart.clear()
        popped = db.retrieve_held(held_id)
        if popped is None:
            self._error("Hold no longer exists.")
            self._refresh_held_count()
            return
        try:
            from core.cart import Cart as _C
            restored = _C.from_json(popped["cart_json"])
        except Exception:
            log.exception("hold parse failed")
            self._error("Failed to restore held cart.")
            return
        for ln in restored.lines:
            self.cart.lines.append(ln)
        self.cart.recompute()
        self.cart_widget.refresh()
        self._refresh_held_count()
        self._refresh_deals_banner()
        self._info("Cart retrieved.")

    # ─── Right panel: pixel-perfect NRS clone ────────────────────────────────

    # NRS dept tiles: (label, color, citylink dept_id_or_None)
    NRS_DEPT_TILES: list[tuple[str, str, Optional[str]]] = [
        ("Ice Cream",          "#F4793D", "ice_cream"),
        ("Snacks",             "#F4C430", "snacks"),
        ("Medicine",           "#7FBA28", "medicine"),
        ("Carbonated\nDrinks", "#E03A3E", "carbonated"),
        ("NON\nCARBONATED",    "#F4C430", "non_carbonated"),
        ("RETAIL",             "#3F8942", "retail"),
        ("STATIONARY",         "#3B2C7E", "stationary"),
        ("Uber",               "#9B27B0", None),  # NRS-only label, no matching dept
        ("Lottery",            "#1F88E5", "lottery"),
    ]

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("register_right_panel")
        panel.setMinimumWidth(700)
        panel.setStyleSheet("QWidget#register_right_panel { background-color: white; }")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Numpad buffer + display — display is mounted inside the search bar row.
        self._numpad_buffer: str = ""
        self._numpad_display = QLabel("ENTERED: $0.00")
        self._numpad_display.setObjectName("numpad_display")
        self._numpad_display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        df = QFont(styles.FONT_FAMILY, 24); df.setBold(True)
        self._numpad_display.setFont(df)
        self._numpad_display.setMinimumHeight(44)
        self._numpad_display.setStyleSheet(
            f"QLabel#numpad_display {{ color: #1B3A6B; background: white;"
            f" padding: 4px 16px; border: 2px solid #1B3A6B;"
            f" border-radius: 6px; }}"
        )

        v.addWidget(self._build_tabs_bar())
        # Dept tiles + per-dept quick-button area (combined widget).
        v.addWidget(self._build_dept_tiles())
        v.addWidget(self._build_separator())
        v.addWidget(self._build_search_bar())

        # Numpad gets the leftover stretch so digits + Cash button grow with screen.
        v.addWidget(self._build_numpad_grid(), stretch=1)
        v.addWidget(self._build_sec_rows())
        v.addWidget(self._build_info_bar())
        return panel

    # ─── Secondary action rows (Hold / Cancel / Clear / Override / Price Check) ──

    def _build_sec_rows(self) -> QWidget:
        container = QFrame()
        container.setObjectName("sec_rows_container")
        container.setStyleSheet("QFrame#sec_rows_container { background-color: white; }")
        v = QVBoxLayout(container)
        v.setSpacing(2)
        v.setContentsMargins(2, 2, 2, 2)
        # Single compact row — frees ~50px of vertical space for the numpad.
        h1 = QHBoxLayout(); h1.setSpacing(3)
        for label, name, color_key, slot in [
            ("Cancel Item",    "act_cancel_item",    "btn_cancel",  self._on_cancel_item),
            ("No Sale",        "act_no_sale",        "btn_no_sale", self._on_no_sale),
            ("Override Price", "act_override_price", "btn_void",    self._on_override_price),
            ("Price Check",    "act_price_check",    "btn_hold",    self._on_price_check),
        ]:
            b = self._mk_action(label, name, color_key, height=42)
            b.clicked.connect(slot)
            h1.addWidget(b)
        v.addLayout(h1)
        return container

    # ─── Top tabs ────────────────────────────────────────────────────────────

    def _build_tabs_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("tabs_bar")
        bar.setFixedHeight(44)
        bar.setStyleSheet("QFrame#tabs_bar { background-color: white; }")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)
        h.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Active tab — Departments
        active = QPushButton("Departments")
        active.setObjectName("tab_departments")
        active.setStyleSheet(
            "QPushButton { background-color: #F1C40F; color: #1B3A6B;"
            " border: none; border-radius: 16px; padding: 6px 24px;"
            " font-weight: bold; font-size: 11pt; }"
        )
        h.addWidget(active)

        # Inactive tabs (label-only style)
        for label, name in [("RETAIL", "tab_retail"), ("Promotion Combo", "tab_promotion")]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setStyleSheet(
                "QPushButton { background-color: transparent; color: #555;"
                " border: none; padding: 6px 16px;"
                " font-weight: bold; font-size: 11pt; }"
            )
            b.clicked.connect(lambda _ck=False, x=label: self._stub(f"Tab: {x}"))
            h.addWidget(b)

        h.addStretch(1)
        return bar

    # ─── Dept tile grid (NRS) ────────────────────────────────────────────────

    def _build_dept_tiles(self) -> QWidget:
        """Editable 3×3 dept tile grid. Admin can add/edit/delete; max 9 slots."""
        from ui.cashier.dept_tiles import DeptTileGrid
        is_admin = (getattr(self.cashier, "role", "") == "admin")
        self._dept_grid = DeptTileGrid(admin_mode=is_admin)
        self._dept_grid.dept_clicked.connect(self._on_dept_tile_click)
        self._dept_grid.add_requested.connect(self._on_dept_tile_add)
        self._dept_grid.edit_requested.connect(self._on_dept_tile_edit)
        self._dept_grid.quick_add_requested.connect(self._on_quick_sell)
        return self._dept_grid

    def _on_dept_tile_click(self, dept_id: str) -> None:
        """Tile click → existing typed-price + tap manual-add workflow."""
        if not dept_id:
            return
        if self._numpad_cents() > 0:
            self._on_dept_selected(dept_id)

    def _require_admin_pin(self) -> bool:
        """Prompt for admin PIN; return True iff verified admin."""
        if getattr(self.cashier, "role", "") == "admin":
            return True
        from PyQt6.QtWidgets import QInputDialog
        pin, ok = QInputDialog.getText(
            self, "Admin PIN", "Enter admin PIN:",
            echo=QLineEdit.EchoMode.Password,
        )
        if not ok or not pin:
            return False
        try:
            user = db.get_user_by_pin(pin)
        except Exception:
            log.exception("get_user_by_pin failed")
            user = None
        if user is None or user.get("role") != "admin":
            self._error("Invalid admin PIN.")
            return False
        return True

    def _on_dept_tile_add(self) -> None:
        if not self._require_admin_pin():
            return
        from ui.cashier.dept_tiles import DeptTileEditDialog
        dlg = DeptTileEditDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_data is None:
            return
        try:
            db.create_dept_tile(
                name=dlg.result_data["name"],
                color=dlg.result_data["color"],
                dept_id="",
                price_cents=int(dlg.result_data.get("price_cents", 0)),
                taxable=bool(dlg.result_data.get("taxable", True)),
            )
        except Exception:
            log.exception("create_dept_tile failed")
            self._error("Could not save tile.")
            return
        self._dept_grid.refresh()

    def _on_dept_tile_edit(self, tile_id: int) -> None:
        if not self._require_admin_pin():
            return
        from ui.cashier.dept_tiles import DeptTileEditDialog
        try:
            tiles = db.list_dept_tiles()
        except Exception:
            log.exception("list_dept_tiles failed"); return
        existing = next((t for t in tiles if int(t["id"]) == tile_id), None)
        if existing is None:
            return
        dlg = DeptTileEditDialog(tile_id=tile_id, existing=existing, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dlg.deleted:
                db.delete_dept_tile(tile_id)
            elif dlg.result_data is not None:
                db.update_dept_tile(
                    tile_id,
                    name=dlg.result_data["name"],
                    color=dlg.result_data["color"],
                    price_cents=int(dlg.result_data.get("price_cents", 0)),
                    taxable=bool(dlg.result_data.get("taxable", True)),
                )
        except Exception:
            log.exception("dept_tile save failed")
            self._error("Save failed.")
            return
        self._dept_grid.refresh()

    def _empty_tile(self, height: int, *, bg: str = "white") -> QWidget:
        w = QWidget()
        w.setMinimumHeight(height)
        w.setMaximumHeight(height)
        w.setStyleSheet(f"background-color: {bg};")
        return w

    def _build_separator(self) -> QWidget:
        """Thin horizontal separator line between dept area and search bar."""
        line = QFrame()
        line.setObjectName("dept_search_separator")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #999;")
        return line

    # ─── NRS numpad + action grid ────────────────────────────────────────────

    def _build_numpad_grid(self) -> QWidget:
        container = QFrame()
        container.setObjectName("numpad_grid_container")
        container.setStyleSheet("QFrame#numpad_grid_container { background-color: white; }")
        grid = QGridLayout(container)
        grid.setSpacing(4)
        grid.setContentsMargins(4, 4, 4, 4)
        for c in range(6):
            grid.setColumnStretch(c, 1)
        # All 4 rows distribute height EQUALLY — keeps every cell same size
        # so the keypad reads as a square cluster, not stretched columns.
        for r in range(4):
            grid.setRowStretch(r, 1)

        DIGIT_QSS = (
            "QPushButton { background-color: white; color: #333;"
            " border: 1px solid #DDD; font-weight: bold; font-size: 22pt; }"
            "QPushButton:pressed { background-color: #EEE; }"
        )

        def mk_digit(text: str, name: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(name)
            b.setMinimumHeight(54)   # min — grid row stretch grows it equally
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            b.setStyleSheet(DIGIT_QSS)
            return b

        def mk_act(text: str, name: str, color: str, *, font_pt: int = 11) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(name)
            b.setMinimumHeight(54)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: none; font-weight: bold; font-size: {font_pt}pt; }}"
            )
            return b

        # NRS-style 6-col layout (matches reference screenshot):
        # Row 0: 7 | 8 | 9 | LOTTERY PAYOUT | $20 (orange) | $10 (orange)
        # Row 1: 4 | 5 | 6 | $5 (orange)    | -            | Basket Discount (blue)
        # Row 2: 1 | 2 | 3 | Credit/Debit (red) | -        | CASH (green, span rows 2-3)
        # Row 3: 0 | 00| ⌫ | Refund (red)   | -            | (CASH cont)
        digit_layout = [
            (0, ["7", "8", "9"]),
            (1, ["4", "5", "6"]),
            (2, ["1", "2", "3"]),
            (3, ["0", "00", "BACK"]),
        ]
        for r, row in digit_layout:
            for ci, d in enumerate(row):
                if d == "BACK":
                    b = mk_digit("⌫", "npd_btn_back")
                    b.clicked.connect(lambda _ck=False: self._numpad_back())
                else:
                    b = mk_digit(d, f"npd_btn_{d}")
                    b.clicked.connect(lambda _ck=False, x=d: self._numpad_input(x))
                grid.addWidget(b, r, ci)

        # ── Right action cluster (cols 3-5) ──
        b = mk_act("LOTTERY\nPAYOUT", "act_lottery_minus", "#4CAF50", font_pt=13)
        b.clicked.connect(self._on_lottery_minus); grid.addWidget(b, 0, 3)

        b = mk_act("$20", "act_cash_20", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(2000)); grid.addWidget(b, 0, 4)
        b = mk_act("$10", "act_cash_10", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(1000)); grid.addWidget(b, 0, 5)

        b = mk_act("$5", "act_cash_5", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(500)); grid.addWidget(b, 1, 3)
        grid.addWidget(self._empty_tile(46), 1, 4)
        b = mk_act("Basket\nDiscount", "act_basket_discount", "#2196F3", font_pt=11)
        b.clicked.connect(self._on_basket_discount); grid.addWidget(b, 1, 5)

        b = mk_act("Credit\nDebit", "act_card", "#E53935", font_pt=13)
        b.clicked.connect(self._on_card); grid.addWidget(b, 2, 3)
        grid.addWidget(self._empty_tile(46), 2, 4)
        cash_btn = mk_act("CASH", "act_cash", "#27AE60", font_pt=20)
        cash_btn.clicked.connect(self._on_cash)
        grid.addWidget(cash_btn, 2, 5, 2, 1)

        b = mk_act("Refund", "act_refund", "#E53935", font_pt=11)
        b.setToolTip("Refund (manager PIN required). Enter amount on numpad first.")
        b.clicked.connect(self._on_refund); grid.addWidget(b, 3, 3)
        grid.addWidget(self._empty_tile(46), 3, 4)

        return container

    # ─── Bottom info bar ─────────────────────────────────────────────────────

    def _build_info_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("info_bar")
        bar.setFixedHeight(20)
        bar.setStyleSheet("QFrame#info_bar { background-color: #555; }")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 12, 4)
        lab = QLabel("Ask the customer for their CityLink Club Account Number for Savings")
        lab.setObjectName("info_bar_text")
        lab.setStyleSheet("color: white; font-size: 10pt; background: transparent;")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(lab)
        return bar

    # ─── Search bar (NRS-style) ──────────────────────────────────────────────

    def _build_search_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("search_bar")
        bar.setFixedHeight(60)
        bar.setStyleSheet(
            "QFrame#search_bar { background-color: #1B3A6B;"
            " border-top: 2px solid #F1C40F;"
            " border-bottom: 2px solid #F1C40F; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(8)

        # ENTERED display moved to LEFT (priority for cashier glance).
        h.addWidget(self._numpad_display)

        # X clear (grey circle with white X)
        self._search_clear_btn = QPushButton("✕")
        self._search_clear_btn.setObjectName("search_clear")
        self._search_clear_btn.setFixedSize(36, 36)
        self._search_clear_btn.setStyleSheet(
            "QPushButton { background-color: #B0B0B0; color: white;"
            " border: none; border-radius: 18px;"
            " font-weight: bold; font-size: 13pt; }"
            "QPushButton:hover { background-color: #999; }"
        )
        self._search_clear_btn.clicked.connect(self._on_search_clear)
        h.addWidget(self._search_clear_btn)

        # White rounded input area with magnifier prefix
        input_wrap = QFrame()
        input_wrap.setObjectName("search_input_wrap")
        input_wrap.setStyleSheet(
            "QFrame#search_input_wrap { background-color: white;"
            " border: 2px solid #F1C40F; border-radius: 6px; }"
        )
        iw = QHBoxLayout(input_wrap)
        iw.setContentsMargins(6, 0, 6, 0)
        iw.setSpacing(6)

        icon = QLabel("🔍")
        icon.setObjectName("search_icon")
        icon.setFixedSize(32, 32)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            "background-color: #B0B0B0; color: white;"
            " border-radius: 16px; font-size: 13pt;"
        )
        iw.addWidget(icon)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("search_input")
        self._search_input.setPlaceholderText("Scan barcode or enter SKU")
        # Force compact text-keyboard popup (not the numeric variant).
        self._search_input.setProperty("touchKeyboard", "text")
        self._search_input.setStyleSheet(
            "QLineEdit { background: transparent; border: none;"
            " font-size: 16pt; color: #333; padding: 4px; }"
            "QLineEdit::placeholder { color: #888; }"
        )
        self._search_input.returnPressed.connect(self._on_search_submit)
        iw.addWidget(self._search_input, stretch=1)
        h.addWidget(input_wrap, stretch=1)

        # < back (grey circle)
        self._search_back_btn = QPushButton("<")
        self._search_back_btn.setObjectName("search_back")
        self._search_back_btn.setFixedSize(36, 36)
        self._search_back_btn.setStyleSheet(
            "QPushButton { background-color: #B0B0B0; color: white;"
            " border: none; border-radius: 18px;"
            " font-weight: bold; font-size: 14pt; }"
        )
        self._search_back_btn.clicked.connect(self._on_search_back)
        h.addWidget(self._search_back_btn)

        # SKU PLU blue rectangle (2-line text)
        self._search_sku_btn = QPushButton("SKU\nPLU")
        self._search_sku_btn.setObjectName("search_sku_plu")
        self._search_sku_btn.setFixedSize(72, 40)
        self._search_sku_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white;"
            " border: none; border-radius: 4px;"
            " font-weight: bold; font-size: 10pt; }"
        )
        self._search_sku_btn.clicked.connect(self._on_sku_plu)
        h.addWidget(self._search_sku_btn)

        return bar

    def _on_search_clear(self) -> None:
        # X clears BOTH the search box and the ENTERED amount buffer.
        # Closes any open touch keyboard and drops focus from the search input.
        self._search_input.clear()
        self._numpad_clear()
        self._search_input.clearFocus()
        try:
            from ui.cashier.touch_keyboard import close_active_keyboard
            close_active_keyboard()
        except Exception:
            log.exception("close_active_keyboard failed")

    def _on_search_submit(self) -> None:
        text = self._search_input.text().strip()
        if not text:
            return
        # Try exact barcode match first.
        row = db.get_item_by_barcode(text)
        if row is not None:
            from core.models import Item
            ln = self.cart.add_item(Item.from_row(row))
            idx = self.cart.lines.index(ln)
            self.cart_widget.refresh(flash_index=idx)
            self._refresh_deals_banner()
            self._search_input.clear()
            return
        # Fallback: partial name/barcode search → picker dialog.
        try:
            results = db.search_items(text, active_only=True, limit=50)
        except Exception:
            log.exception("search_items failed")
            results = []
        if not results:
            self._info(f"No results for '{text}'.")
            self._search_input.clear()
            return
        dlg = ItemPickerDialog(results, parent=self, initial_query=text)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.picked is None:
            self._search_input.clear()
            return
        from core.models import Item
        ln = self.cart.add_item(Item.from_row(dlg.picked))
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()
        self._search_input.clear()

    def _on_search_back(self) -> None:
        self._stub("Search history back")

    def _on_sku_plu(self) -> None:
        self._stub("SKU/PLU lookup")

    # ─── Button factories ────────────────────────────────────────────────────

    def _mk_action(self, text: str, name: str, color_key: Optional[str], *, height: int = 44) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        f = QFont(styles.FONT_FAMILY, 10); f.setBold(True)
        b.setFont(f)
        b.setMinimumHeight(height)
        b.setMaximumHeight(height)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if color_key:
            color = styles.COLORS[color_key]
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: none; border-radius: 6px; padding: 6px; }}"
                f"QPushButton:disabled {{ background-color: #BDBDBD; color: #757575; }}"
            )
        else:
            b.setStyleSheet(
                f"QPushButton {{ background-color: {styles.COLORS['white']};"
                f" color: {styles.COLORS['text_dark']};"
                f" border: 1px solid {styles.COLORS['blue_mid']};"
                f" border-radius: 6px; padding: 6px; font-weight: bold; }}"
                f"QPushButton:pressed {{ background-color: {styles.COLORS['blue_light']};"
                f" color: white; }}"
            )
        return b

    def _mk_numpad_btn(self, text: str, name: str, *, height: int = 44) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        f = QFont(styles.FONT_FAMILY, 16); f.setBold(True)
        b.setFont(f)
        b.setMinimumHeight(height)
        b.setMaximumHeight(height)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['white']};"
            f" color: {styles.COLORS['text_dark']};"
            f" border: 1px solid {styles.COLORS['blue_mid']};"
            f" border-radius: 6px; padding: 6px; }}"
            f"QPushButton:pressed {{ background-color: {styles.COLORS['blue_light']};"
            f" color: white; }}"
        )
        return b

    def _add_digit(self, grid: QGridLayout, token: str, row: int, col: int) -> None:
        b = self._mk_numpad_btn(token, f"npd_btn_{token}")
        b.clicked.connect(lambda _checked=False, x=token: self._numpad_input(x))
        grid.addWidget(b, row, col)

    # ─── Inline numpad logic (replaces Numpad widget) ────────────────────────

    PRICE_MAX_DIGITS = 7   # $99,999.99

    def _numpad_input(self, token: str) -> None:
        candidate = self._numpad_buffer + token
        if len(candidate) > self.PRICE_MAX_DIGITS:
            return
        self._numpad_buffer = candidate
        self._numpad_render()

    def _numpad_clear(self) -> None:
        if not self._numpad_buffer:
            return
        self._numpad_buffer = ""
        self._numpad_render()

    def _numpad_back(self) -> None:
        if not self._numpad_buffer:
            return
        self._numpad_buffer = self._numpad_buffer[:-1]
        self._numpad_render()

    def _numpad_render(self) -> None:
        cents = int(self._numpad_buffer) if self._numpad_buffer.isdigit() else 0
        self._numpad_display.setText(f"ENTERED: ${cents / 100:.2f}")

    def _numpad_cents(self) -> int:
        return int(self._numpad_buffer) if self._numpad_buffer.isdigit() else 0

    def _numpad_text(self) -> str:
        return self._numpad_buffer

    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("register_footer")
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background-color: {styles.COLORS['navy']};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        def mk_footer(text: str, name: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(name)
            b.setFont(QFont(styles.FONT_FAMILY, 11))
            b.setStyleSheet(
                "QPushButton { background-color: transparent; color: white;"
                "border: 1px solid white; border-radius: 4px; padding: 4px 12px; }"
            )
            return b

        for label, name, slot in [
            ("≡ Menu",     "ftr_menu",     lambda: self._stub("Menu")),
            ("Calculator", "ftr_calc",     lambda: self._stub("Calculator")),
            ("Receipts",   "ftr_receipts", lambda: self._stub("Receipts")),
            ("Reprint",    "ftr_reprint",  self._on_reprint_last),
            ("EOD",        "ftr_eod",      self._on_eod),
        ]:
            b = mk_footer(label, name)
            b.clicked.connect(slot)
            h.addWidget(b)

        h.addStretch(1)

        b_admin = mk_footer("Admin ▶", "ftr_admin")
        b_admin.clicked.connect(self.admin_requested.emit)
        h.addWidget(b_admin)

        b_logout = mk_footer("Lock", "ftr_logout")
        b_logout.clicked.connect(self.logout_requested.emit)
        h.addWidget(b_logout)
        return bar

    # ─── wiring ──────────────────────────────────────────────────────────────

    def _wire_clock(self) -> None:
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()

    def _wire_signals(self) -> None:
        # NRS dept tile clicks are wired directly in _build_dept_tiles().
        # No DepartmentGrid widget in the NRS-style layout.
        pass

    def _update_clock(self) -> None:
        self._clock_label.setText(_time.strftime("%-I:%M %p", _time.localtime()))

    # ─── Sound helpers (no-op if sound_player is None) ───────────────────────

    def _play_success(self) -> None:
        if self.sound_player is not None:
            self.sound_player.play_success()

    def _play_chaching(self) -> None:
        # Prefer the production MP3 cash-register clip when present; fall
        # back to the legacy synthesized cha-ching WAV otherwise.
        if self.sound_player is None:
            return
        try:
            if self.sound_player._mp3.get("cash") is not None:
                self.sound_player.play_cash_sound()
                return
        except Exception:
            pass
        self.sound_player.play_chaching()

    def _play_card_approved(self) -> None:
        """Play CardPayment.mp3 on card approval completion. Failsafe."""
        if self.sound_player is None:
            return
        try:
            self.sound_player.play_card_sound()
        except Exception:
            pass

    def _play_error(self) -> None:
        if self.sound_player is not None:
            self.sound_player.play_error()

    def _refresh_terminal_dot(self) -> None:
        if self.terminal is not None and self.terminal.is_connected():
            if is_mock(self.terminal):
                text = "● MOCK"
                color = styles.COLORS["warning"]   # orange
            else:
                text = "● TCP"
                color = styles.COLORS["success"]   # green
        else:
            text = "● CASH-ONLY"
            color = styles.COLORS["danger"]        # red
        self._terminal_dot.setText(text)
        self._terminal_dot.setStyleSheet(
            f"color: {color}; font-weight: bold;"
        )

    # ─── barcode ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        key = ev.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            buf = self._barcode_buffer
            self._barcode_buffer = ""
            if buf:
                self._handle_barcode(buf)
            ev.accept()
            return
        text = ev.text()
        if text and text.isprintable():
            self._barcode_buffer += text
            ev.accept()
            return
        super().keyPressEvent(ev)

    def _on_basket_discount(self) -> None:
        """Apply a cart-level discount (% or flat $). Reflected in GST/PST."""
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        from PyQt6.QtWidgets import QInputDialog
        # Choose mode: percent OR amount.
        mode, ok = QInputDialog.getItem(
            self, "Basket Discount", "Discount type:",
            ["Percent (%)", "Amount ($)"], 0, False,
        )
        if not ok:
            return
        if mode.startswith("Percent"):
            pct, ok = QInputDialog.getDouble(
                self, "Basket Discount", "Percent off subtotal:",
                10.0, 0.0, 100.0, 1,
            )
            if not ok:
                return
            self.cart.set_basket_discount(pct=pct)
        else:
            dollars, ok = QInputDialog.getDouble(
                self, "Basket Discount", "Amount off ($):",
                1.00, 0.0, 9999.0, 2,
            )
            if not ok:
                return
            self.cart.set_basket_discount(cents=int(round(dollars * 100)))
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _on_refund(self) -> None:
        """Refund flow: numpad cents → manager PIN → record neg txn → print."""
        cents = self._numpad_cents()
        if cents <= 0:
            self._error("Enter refund amount on the numpad first.")
            return
        if not self._confirm(f"Refund ${cents/100:.2f} to customer?"):
            return
        # Manager PIN required.
        from PyQt6.QtWidgets import QInputDialog
        pin, ok = QInputDialog.getText(
            self, "Manager PIN", "Enter manager PIN:",
            echo=QInputDialog.EchoMode.Password.value if hasattr(QInputDialog, 'EchoMode') else 2,
        )
        if not ok or not pin:
            return
        try:
            user = db.get_user_by_pin(pin)
        except Exception:
            log.exception("verify_pin failed")
            user = None
        if user is None or user.get("role") != "admin":
            self._error("Invalid manager PIN.")
            return
        # Record refund as a negative-amount transaction with payment_method='refund'.
        ref = db.next_transaction_ref()
        try:
            tid = db.insert_transaction({
                "transaction_ref": ref,
                "subtotal_cents": -cents, "discount_cents": 0,
                "gst_cents": 0, "pst_cents": 0,
                "deposit_cents": 0, "bag_charge_cents": 0,
                "total_cents": -cents, "rounded_total_cents": -cents,
                "payment_method": "refund",
                "cash_tendered_cents": 0, "change_cents": 0,
                "card_amount_cents": 0,
                "card_auth_code": None, "card_last4": None,
                "status": "refunded",
                "cashier_id": self.cashier.id,
                "cashier_name": self.cashier.name,
                "shift_id": self.shift_id,
            }, items=[])
        except Exception:
            log.exception("refund insert failed")
            self._error("Failed to save refund. See errors.log.")
            return
        # Print refund slip (re-uses receipt path with negative txn).
        try:
            from core.models import Transaction
            txn = Transaction(
                transaction_ref=ref,
                subtotal_cents=-cents, discount_cents=0,
                gst_cents=0, pst_cents=0, deposit_cents=0, bag_charge_cents=0,
                total_cents=-cents, rounded_total_cents=-cents,
                payment_method="refund",
                cash_tendered_cents=0, change_cents=0,
                cashier_id=self.cashier.id, cashier_name=self.cashier.name,
                shift_id=self.shift_id, items=[],
            )
            self._print_receipt(txn, tid)
        except Exception:
            log.exception("refund receipt print failed")
        self._open_cash_drawer()
        self._numpad_clear()
        self._info(f"Refund ${cents/100:.2f} approved by {user['name']}.\nRef: {ref}")

    def _on_quick_sell(self, name: str, price_cents: int, taxable: bool) -> None:
        """Quick-sell tile click → add manual line, no numpad needed."""
        if price_cents < 0:
            return
        # Map taxable flag to a dept that matches BC tax defaults so existing
        # line-tax logic stays intact:
        dept = "snacks" if taxable else "gift_cards"
        try:
            ln = self.cart.add_manual(
                name=name, unit_price_cents=price_cents,
                department=dept, quantity=1,
            )
        except Exception:
            log.exception("quick-sell add failed")
            self._error("Could not add quick-sell item.")
            return
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()

    def _on_unknown_barcode(self, barcode: str) -> None:
        """Cashier-side missing-item flow: prompt to Add or Cancel.
        On Add → reuse admin's ItemEditDialog with prefilled barcode; on save
        the new item is auto-added to the current cart.
        """
        from PyQt6.QtWidgets import QMessageBox
        try:
            db.log_barcode_miss(barcode)
        except Exception:
            log.exception("log_barcode_miss failed")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Item not found")
        msg.setText(f"Barcode {barcode} not found.\n\nAdd it to inventory?")
        add_btn = msg.addButton("Add Item", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(add_btn)
        msg.exec()
        if msg.clickedButton() is not add_btn:
            return
        from ui.admin.inventory import ItemEditDialog
        dlg = ItemEditDialog(item_id=None, prefill_barcode=barcode,
                             admin_name=self.cashier.name, parent=self)
        # Description focus is set inside ItemEditDialog when prefill_barcode
        # is provided (scanner-flow). Cashier types name → tabs to price → Save.
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Re-fetch the freshly-saved item by barcode and add to cart.
        try:
            row = db.get_item_by_barcode(barcode)
        except Exception:
            log.exception("post-add lookup failed")
            row = None
        if row is None:
            self._error("Item saved but lookup failed. Re-scan to add.")
            return
        from core.models import Item
        ln = self.cart.add_item(Item.from_row(row))
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()

    def _handle_barcode(self, barcode: str) -> None:
        log.info("scan: %s", barcode)
        self._last_dept_add = None
        if hasattr(self, "_search_input") and self._search_input is not None:
            self._search_input.setText(barcode)
            QTimer.singleShot(1500, lambda: self._search_input.clear()
                              if self._search_input.text() == barcode else None)
        row = db.get_item_by_barcode(barcode)
        if row is None:
            # New flow: offer to add the item right now (cashier-side quick add).
            self._on_unknown_barcode(barcode)
            return
        from core.models import Item
        ln = self.cart.add_item(Item.from_row(row))
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()

    # ─── department / manual entry ───────────────────────────────────────────

    def _on_dept_selected(self, dept_id: str) -> None:
        # Numpad buffer = manual price for this dept. Empty buffer → no-op
        # (cashier needs to type a price first).
        if dept_id == ALL_ID:
            return
        d = DEPT_BY_ID.get(dept_id)
        if d is None:
            self._error(f"Unknown department: {dept_id}")
            return
        cents = self._numpad_cents()
        if cents <= 0:
            return
        ln = self.cart.add_manual(name=d["label"], unit_price_cents=cents,
                                  department=dept_id, quantity=1)
        idx = self.cart.lines.index(ln)
        self._last_dept_add = (dept_id, idx)
        self.cart_widget.refresh(flash_index=idx)
        self._numpad_clear()
        self._refresh_deals_banner()

    # ─── action handlers ─────────────────────────────────────────────────────

    def _on_cash_shortcut(self, cents: int) -> None:
        # Pre-fill numpad with shortcut amount; user still presses CASH to commit.
        self._numpad_clear()
        for ch in str(cents):
            self._numpad_input(ch)

    def _on_cash(self) -> None:
        """Cash press — supports split payments AND negative-total settlements.

        Tender model uses signed `net_owed = rounded_total - prior_cash_partial`:
          net_owed > 0  → customer owes; existing partial / exact / change flow.
          net_owed == 0 → balanced; finalize with zero tender + zero change.
          net_owed < 0  → store owes; cash back to customer (lottery payout
                          and/or partial refund of prior partials).

        The legacy "Already paid in full" message only fires when prior
        partials already covered the bill (rounded > 0 but net_owed ≤ 0).
        """
        self._last_dept_add = None
        if self._payment_locked:
            log.info("cash press ignored — payment in progress")
            return
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        rounded = self.cart.totals["rounded_total_cents"]
        partial = self._cash_partial_cents
        net_owed = rounded - partial

        # ── Negative or zero rounded total: settle as cash-back / payout.
        # Customer is either being paid out (rounded < 0) or breaks even
        # (rounded == 0). Skip tender/partial/paid-in-full logic.
        if rounded <= 0:
            cash_back = max(0, -rounded) + max(0, partial)
            self._payment_locked = True
            try:
                self._finalize_cash_back(prior_partial_cents=partial,
                                          cash_back_cents=cash_back)
            finally:
                self._payment_locked = False
            return

        # ── Positive rounded total but already covered by prior partials.
        if net_owed <= 0:
            # Overpaid via partials → return the excess as cash-back.
            excess = -net_owed
            if excess > 0:
                self._payment_locked = True
                try:
                    self._finalize_cash_back(prior_partial_cents=partial,
                                              cash_back_cents=excess)
                finally:
                    self._payment_locked = False
                return
            # Exactly covered → finalize cleanly.
            self._payment_locked = True
            try:
                self._cash_partial_cents = 0
                self._finalize_cash(tender_cents=partial, change_cents=0)
            finally:
                self._payment_locked = False
            return

        # ── Positive net_owed: existing tender flow.
        raw = self._numpad_cents()
        if raw == 0:
            # Empty buffer → tender exactly the remaining amount (no change).
            self._payment_locked = True
            try:
                tender_total = partial + net_owed
                self._cash_partial_cents = 0
                self._finalize_cash(tender_total, 0)
            finally:
                self._payment_locked = False
            return
        if raw < net_owed:
            # Partial cash payment — accumulate silently. No popup.
            self._cash_partial_cents += raw
            self._numpad_clear()
            self.cart_widget.totals_panel.set_partial_paid(self._cash_partial_cents)
            return
        # Full or over — finalize with combined tender.
        self._payment_locked = True
        try:
            change = raw - net_owed
            tender_total = partial + raw
            self._cash_partial_cents = 0
            self._finalize_cash(tender_total, change)
        finally:
            self._payment_locked = False

    def _is_payout_only_cart(self) -> bool:
        """True if the cart contains ONLY lottery payout lines (negative
        unit_price). Mixed carts (sale items + lottery winnings) follow the
        normal cash flow because a positive subtotal still needs tendering.
        """
        any_payout = False
        for ln in self.cart.lines:
            if ln.kind == "lottery" and ln.unit_price_cents < 0:
                any_payout = True
                continue
            # Any non-payout line disqualifies (regular item, bag, lottery sale)
            return False
        return any_payout

    def _finalize_cash_back(self, *, prior_partial_cents: int,
                             cash_back_cents: int) -> None:
        """Finalize when net_owed <= 0 (store owes customer or balanced).

        prior_partial_cents → money already collected from customer (from
        prior partial cash tenders that we now refund / reconcile).
        cash_back_cents     → total money to hand BACK to customer at the
                              drawer (lottery winnings + any partial refund).

        Saves transaction, opens drawer, optionally prints receipt, shows
        the appropriate completion popup.
        """
        t = self.cart.totals
        ref = db.next_transaction_ref()
        rounded = t["rounded_total_cents"]
        # Pure-payout cart → tag as payout for ledger reporting; mixed
        # carts (any retail/bag line) tag as cash for normal sale reporting.
        method = "payout" if self._is_payout_only_cart() else "cash"
        txn = Transaction(
            transaction_ref=ref,
            subtotal_cents=t["subtotal_cents"],
            discount_cents=t["discount_cents"],
            gst_cents=t["gst_cents"],
            pst_cents=t["pst_cents"],
            deposit_cents=t["deposit_cents"],
            bag_charge_cents=t["bag_charge_cents"],
            total_cents=t["total_cents"],
            rounded_total_cents=rounded,
            payment_method=method,
            cash_tendered_cents=prior_partial_cents,
            change_cents=cash_back_cents,
            cashier_id=self.cashier.id,
            cashier_name=self.cashier.name,
            shift_id=self.shift_id,
            items=list(self.cart.lines),
        )
        items_data = [ln.to_db_dict() for ln in self.cart.lines]
        lottery_records = [
            {
                "entry_type": "payout" if ln.unit_price_cents < 0 else "sale",
                "amount_cents": abs(ln.unit_price_cents) * ln.quantity,
                "cashier_name": self.cashier.name,
                "shift_id": self.shift_id,
                "description": ln.name,
            }
            for ln in self.cart.lines if ln.kind == "lottery"
        ]
        try:
            tid = db.insert_transaction_with_lottery(
                txn.header_dict(), items_data, lottery_records,
            )
        except Exception:
            log.exception("cash-back transaction insert failed")
            self._error("Failed to save transaction. See errors.log.")
            return

        self._open_cash_drawer()
        self._last_txn = txn
        self._last_tid = tid
        self._play_chaching()

        # Receipt prompt — same UX as card flow.
        prd_title = "Payout Receipt?" if method == "payout" else "Receipt Options"
        prd_subtitle = (
            f"Print payout receipt for ${cash_back_cents/100:.2f}?"
            if method == "payout" else "Print customer receipt?"
        )
        prd = PrintReceiptDialog(
            parent=self,
            title=prd_title,
            subtitle=prd_subtitle,
            detail=f"{'PAYOUT' if method == 'payout' else 'CASH'}  ·  {ref}",
        )
        print_ok = True
        if prd.exec() == QDialog.DialogCode.Accepted:
            try:
                self._print_receipt(txn, tid)
            except Exception:
                log.exception("receipt print failed")
                print_ok = False
                self._error("Saved but receipt failed to print. Use Reprint.")

        # Completion popup — distinct visuals for pure-payout vs mixed.
        if cash_back_cents > 0:
            if method == "payout":
                PayoutCompleteDialog(ref, cash_back_cents, parent=self).exec()
            else:
                # Mixed cart settling negative → use Change Due dialog.
                ChangeDialog(ref, cash_back_cents, parent=self).exec()
        # else (zero-net): cleanly clear without popup.

        # Reset register state.
        self.cart.clear()
        self._cash_partial_cents = 0
        try:
            self.cart_widget.totals_panel.set_partial_paid(0)
        except Exception:
            pass
        self._numpad_clear()
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _finalize_cash(self, tender_cents: int, change_cents: int) -> None:
        t = self.cart.totals
        ref = db.next_transaction_ref()
        txn = Transaction(
            transaction_ref=ref,
            subtotal_cents=t["subtotal_cents"],
            discount_cents=t["discount_cents"],
            gst_cents=t["gst_cents"],
            pst_cents=t["pst_cents"],
            deposit_cents=t["deposit_cents"],
            bag_charge_cents=t["bag_charge_cents"],
            total_cents=t["total_cents"],
            rounded_total_cents=t["rounded_total_cents"],
            payment_method="cash",
            cash_tendered_cents=tender_cents,
            change_cents=change_cents,
            cashier_id=self.cashier.id,
            cashier_name=self.cashier.name,
            shift_id=self.shift_id,
            items=list(self.cart.lines),
        )
        items_data = [ln.to_db_dict() for ln in self.cart.lines]
        # Build atomic lottery records — sale + ledger written in ONE db transaction.
        lottery_records = [
            {
                "entry_type": "payout" if ln.unit_price_cents < 0 else "sale",
                "amount_cents": abs(ln.unit_price_cents) * ln.quantity,
                "cashier_name": self.cashier.name,
                "shift_id": self.shift_id,
                "description": ln.name,
            }
            for ln in self.cart.lines if ln.kind == "lottery"
        ]
        try:
            tid = db.insert_transaction_with_lottery(
                txn.header_dict(), items_data, lottery_records,
            )
        except Exception:
            log.exception("cash transaction insert failed")
            self._error("Failed to save transaction. See errors.log.")
            return
        # Save → Print → Clear. Print failure surfaces a clear alert so cashier
        # can manually reprint via the Reprint button (we still stash the txn).
        self._open_cash_drawer()
        self._last_txn = txn
        self._last_tid = tid
        print_ok = True
        try:
            self._print_receipt(txn, tid)
        except Exception:
            log.exception("receipt print failed")
            print_ok = False
        # Cash finalize: cha-ching sound (cash only — card path doesn't trigger).
        self._play_chaching()
        if not print_ok:
            self._error("Sale saved but receipt failed to print. Use Reprint.")
        self._show_change_dialog(ref, change_cents)
        # Reset register state — also clear split-payment running tally.
        self.cart.clear()
        self._cash_partial_cents = 0
        try: self.cart_widget.totals_panel.set_partial_paid(0)
        except Exception: pass
        self._numpad_clear()
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _open_cash_drawer(self) -> None:
        log.info("[STUB] cash drawer kick signal")

    def _on_print_button(self) -> None:
        """Print Receipt button:
        - Cart has items → render preview receipt of current cart (no DB save).
        - Cart empty → ask 'Print last receipt?' YES → reprint last completed
          transaction (in-memory cache OR DB fallback).
        """
        if self.cart.is_empty():
            dlg = PrintReceiptDialog(
                parent=self,
                title="Reprint Last Receipt?",
                subtitle="Print the last completed receipt?",
                detail="Cart is empty — this will reprint the most recent transaction.",
                ok_label="Reprint",
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            # In-memory cached last txn first (fast).
            if self._last_txn is not None and self._last_tid is not None:
                try:
                    self._print_receipt(self._last_txn, self._last_tid)
                    return
                except Exception:
                    log.exception("reprint cached failed")
            # Fallback: load most recent completed txn from DB.
            try:
                rec = db.get_last_completed_transaction()
            except Exception:
                log.exception("get_last_completed_transaction failed")
                rec = None
            if rec is None:
                self._info("No completed transactions to reprint.")
                return
            try:
                from core.models import Transaction as _Txn
                txn = _Txn.from_db(rec["transaction"], rec["items"])
                self._print_receipt(txn, rec["transaction"]["id"])
            except Exception:
                log.exception("reprint from db failed")
                self._error("Failed to reprint last receipt.")
            return
        # Build a preview Transaction (status='preview', no DB insert).
        t = self.cart.totals
        from core.models import Transaction
        try:
            preview = Transaction(
                transaction_ref=f"PREVIEW-{int(_time.time())}",
                subtotal_cents=t["subtotal_cents"],
                discount_cents=t["discount_cents"],
                gst_cents=t["gst_cents"],
                pst_cents=t["pst_cents"],
                deposit_cents=t["deposit_cents"],
                bag_charge_cents=t["bag_charge_cents"],
                total_cents=t["total_cents"],
                rounded_total_cents=t["rounded_total_cents"],
                payment_method="preview",
                cash_tendered_cents=0,
                change_cents=0,
                cashier_id=self.cashier.id,
                cashier_name=self.cashier.name,
                shift_id=self.shift_id,
                items=list(self.cart.lines),
            )
            self._print_receipt(preview, tid=-1)
        except Exception:
            log.exception("preview print failed")
            self._error("Failed to print preview receipt.")

    def _on_reprint_last(self) -> None:
        """Reprint the most recent completed transaction's receipt."""
        if self._last_txn is None or self._last_tid is None:
            self._info("No recent transaction to reprint.")
            return
        log.info("reprint requested for tid=%s ref=%s",
                 self._last_tid, self._last_txn.transaction_ref)
        self._print_receipt(self._last_txn, self._last_tid)

    def _print_receipt(self, txn: Transaction, tid: int) -> None:
        # PDF fallback always; ESC/POS thermal attempted only when explicitly enabled.
        try:
            from core import receipt as _r
            path = _r.print_receipt(
                txn,
                store_name=self.store_name,
                cashier_name=self.cashier.name,
                prefer_thermal=False,    # Phase 1: PDF only; flip when printer wired
            )
            log.info("receipt for id=%s ref=%s saved to %s",
                     tid, txn.transaction_ref, path)
        except Exception:
            log.exception("receipt generation failed for id=%s ref=%s",
                          tid, txn.transaction_ref)

    def _show_change_dialog(self, ref: str, change_cents: int) -> None:
        dlg = ChangeDialog(ref, change_cents, self)
        dlg.exec()

    def _on_card(self) -> None:
        self._last_dept_add = None
        if self._payment_locked:
            log.info("card press ignored — payment in progress")
            return
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        if self.terminal is None or not self.terminal.is_connected():
            self._error("Card terminal not connected (cash-only mode).")
            return
        if self._payment_thread is not None:
            self._info("Payment already in progress.")
            return
        # Lock until card response handled (success, decline, cancel).
        self._payment_locked = True

        # Card charges only the REMAINING balance after any cash partials.
        # Card terminal cannot dispense cash, so negative/zero balances
        # must be settled via the Cash button (cash drawer).
        total_cents = self.cart.totals["total_cents"]
        partial = self._cash_partial_cents
        amount = total_cents - partial
        if amount <= 0:
            self._payment_locked = False
            if total_cents < 0:
                self._error("Cart has cash back due — press CASH to settle.")
            else:
                self._error("Nothing to charge — press CASH to settle.")
            return

        self._pending_card_req = PaymentRequest(
            amount_cents=amount,
            transaction_ref=db.next_transaction_ref(),
        )
        self._start_payment_worker()

    def _start_payment_worker(self) -> None:
        """Build sheet, spawn worker. Used both initially and on Try Again."""
        amount = self._pending_card_req.amount_cents

        if self._card_sheet is None:
            self._card_sheet = CardPaymentSheet(self)
            self._card_sheet.cancel_requested.connect(self._on_sheet_cancel)
            self._card_sheet.try_again_clicked.connect(self._on_sheet_try_again)
            self._card_sheet.accept_cash_clicked.connect(self._on_sheet_accept_cash)
        self._card_sheet.show_processing(amount)

        self._payment_thread = QThread(self)
        self._payment_worker = PaymentWorker(self.terminal, self._pending_card_req)
        self._payment_worker.moveToThread(self._payment_thread)
        self._payment_thread.started.connect(self._payment_worker.run)
        self._payment_worker.finished.connect(self._on_card_response)
        self._payment_worker.finished.connect(self._payment_thread.quit)
        self._payment_thread.finished.connect(self._payment_worker.deleteLater)
        self._payment_thread.finished.connect(self._payment_thread.deleteLater)
        self._payment_thread.start()
        log.info("card payment thread started, ref=%s",
                 self._pending_card_req.transaction_ref)

    def _on_card_response(self, resp: PaymentResponse) -> None:
        self._payment_worker = None
        self._payment_thread = None
        req = self._pending_card_req
        if req is None:
            log.error("card response received with no pending request")
            return
        if self._card_sheet is None:
            log.error("card response received with no sheet")
            return
        self._card_sheet.lock_cancel()

        if resp.approved:
            self._play_success()
            self._card_sheet.show_approved()
            QTimer.singleShot(1500, lambda: self._after_card_approved(req, resp))
        elif resp.result == RESULT_DECLINED:
            self._play_error()
            self._card_sheet.show_declined()
            QTimer.singleShot(1000, lambda: self._after_card_declined(resp))
        elif resp.result == RESULT_TIMEOUT:
            self._play_error()
            self._card_sheet.show_timeout()
        else:
            self._play_error()
            self._card_sheet.show_error(resp.error_message or "Payment error")
            QTimer.singleShot(1500, self._dismiss_sheet_and_clear_req)

    def _after_card_approved(self, req: PaymentRequest, resp: PaymentResponse) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None
        # Persist transaction FIRST so the receipt can read it back from the DB.
        try:
            self._finalize_card_db(req, resp)
            if self._confirm("Print receipt?"):
                self._print_receipt_card_stub(req, resp)
        finally:
            # Always release the payment lock, even if save/print raises.
            self._payment_locked = False

    def _after_card_declined(self, resp: PaymentResponse) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None
        self._payment_locked = False
        self._show_toast(f"Card Declined — {resp.error_message or 'try again'}", danger=True)

    def _on_sheet_cancel(self) -> None:
        # Cashier-initiated cancel BEFORE terminal responds.
        log.info("card payment cancel requested by cashier")
        self._dismiss_sheet()
        self._pending_card_req = None
        self._payment_locked = False
        # Worker still finishes; _on_card_response sees no sheet → bails out.

    def _on_sheet_try_again(self) -> None:
        if self._pending_card_req is None:
            self._dismiss_sheet()
            return
        log.info("retrying card payment ref=%s",
                 self._pending_card_req.transaction_ref)
        self._start_payment_worker()

    def _on_sheet_accept_cash(self) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None
        self._payment_locked = False
        self._info("Enter cash tender on the numpad, then press Cash.")

    def _dismiss_sheet(self) -> None:
        if self._card_sheet is not None:
            self._card_sheet.slide_out()

    def _dismiss_sheet_and_clear_req(self) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None
        self._payment_locked = False

    def _show_toast(self, message: str, *, danger: bool = False) -> None:
        toast = Toast(self, message, danger=danger)
        toast.show_for(2000)

    def _finalize_card_db(self, req: PaymentRequest, resp: PaymentResponse) -> None:
        t = self.cart.totals
        partial_cash = self._cash_partial_cents
        is_split = partial_cash > 0
        txn = Transaction(
            transaction_ref=req.transaction_ref,
            subtotal_cents=t["subtotal_cents"],
            discount_cents=t["discount_cents"],
            gst_cents=t["gst_cents"],
            pst_cents=t["pst_cents"],
            deposit_cents=t["deposit_cents"],
            bag_charge_cents=t["bag_charge_cents"],
            total_cents=t["total_cents"],
            rounded_total_cents=t["total_cents"],
            payment_method="split" if is_split else "card",
            cash_tendered_cents=partial_cash,
            change_cents=0,
            card_amount_cents=req.amount_cents,
            card_auth_code=resp.auth_code,
            card_last4=resp.card_last4,
            cashier_id=self.cashier.id,
            cashier_name=self.cashier.name,
            shift_id=self.shift_id,
            items=list(self.cart.lines),
        )
        items_data = [ln.to_db_dict() for ln in self.cart.lines]
        try:
            tid = db.insert_transaction(txn.header_dict(), items_data)
        except Exception:
            log.exception("card transaction insert failed")
            self._error("Failed to save transaction. See errors.log.")
            return
        for ln in self.cart.lines:
            if ln.kind == "lottery":
                try:
                    db.log_lottery(
                        "sale",
                        ln.unit_price_cents * ln.quantity,
                        self.cashier.name,
                        shift_id=self.shift_id,
                        transaction_id=tid,
                        description=ln.name,
                    )
                except Exception:
                    log.exception("lottery_ledger insert failed")
        self.cart.clear()
        self._cash_partial_cents = 0
        try: self.cart_widget.totals_panel.set_partial_paid(0)
        except Exception: pass   # reset split-payment running tally
        self._numpad_clear()
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _print_receipt_card_stub(self, req: PaymentRequest, resp: PaymentResponse) -> None:
        # Loads the saved card transaction by ref and renders via core.receipt
        try:
            saved = db.get_transaction_by_ref(req.transaction_ref)
            if saved is None:
                log.error("card receipt: no saved txn for ref=%s", req.transaction_ref)
                return
            from core import receipt as _r
            txn = Transaction.from_db(saved["transaction"], saved["items"])
            path = _r.print_receipt(
                txn,
                store_name=self.store_name,
                cashier_name=self.cashier.name,
                prefer_thermal=False,
            )
            log.info("card receipt ref=%s saved to %s", req.transaction_ref, path)
        except Exception:
            log.exception("card receipt generation failed ref=%s", req.transaction_ref)

    def _finalize_card(self, req: PaymentRequest, resp: PaymentResponse) -> None:
        t = self.cart.totals
        txn = Transaction(
            transaction_ref=req.transaction_ref,
            subtotal_cents=t["subtotal_cents"],
            discount_cents=t["discount_cents"],
            gst_cents=t["gst_cents"],
            pst_cents=t["pst_cents"],
            deposit_cents=t["deposit_cents"],
            bag_charge_cents=t["bag_charge_cents"],
            total_cents=t["total_cents"],
            rounded_total_cents=t["total_cents"],   # card = exact, no rounding
            payment_method="card",
            cash_tendered_cents=0,
            change_cents=0,
            card_amount_cents=req.amount_cents,
            card_auth_code=resp.auth_code,
            card_last4=resp.card_last4,
            cashier_id=self.cashier.id,
            cashier_name=self.cashier.name,
            shift_id=self.shift_id,
            items=list(self.cart.lines),
        )
        items_data = [ln.to_db_dict() for ln in self.cart.lines]
        lottery_records = [
            {
                "entry_type": "payout" if ln.unit_price_cents < 0 else "sale",
                "amount_cents": abs(ln.unit_price_cents) * ln.quantity,
                "cashier_name": self.cashier.name,
                "shift_id": self.shift_id,
                "description": ln.name,
            }
            for ln in self.cart.lines if ln.kind == "lottery"
        ]
        try:
            tid = db.insert_transaction_with_lottery(
                txn.header_dict(), items_data, lottery_records,
            )
        except Exception:
            log.exception("card transaction insert failed")
            self._error("Failed to save transaction. See errors.log.")
            return
        # Save first; receipt is OPTIONAL on card (cardholder usually has slip).
        self._last_txn = txn
        self._last_tid = tid
        last4 = resp.card_last4 or "????"
        # Card path: play CardPayment.mp3 (digital approval feel). No
        # cash-drawer side effects.
        self._play_card_approved()
        dlg = PrintReceiptDialog(
            parent=self,
            title="Card Approved",
            subtitle="Print merchant + customer card receipts?",
            detail=f"APPROVED  ·  {req.transaction_ref}  ·  Auth {resp.auth_code}  ·  …{last4}",
            ok_label="Yes",
            cancel_label="No",
            glyph="✓",
            glyph_color=styles.COLORS["btn_cash"],
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._print_receipt(txn, tid)
            except Exception:
                log.exception("receipt print failed")
                self._error("Receipt failed to print. Use Reprint button.")
        self.cart.clear()
        self._numpad_clear()
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _on_bag(self) -> None:
        ln = self.cart.add_bag_charge()
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()

    def _on_qty(self) -> None:
        line = self.cart_widget.selected_line()
        if line is None:
            self._info("Select a cart row first.")
            return
        raw = self._numpad_text()
        if not raw:
            self._info("Type a quantity on the numpad first.")
            return
        try:
            new_qty = int(raw)
        except ValueError:
            self._error(f"Invalid quantity: {raw!r}")
            return
        if new_qty <= 0:
            self._error("Quantity must be > 0. Use Cancel Item to remove.")
            return
        if new_qty > 99 and not self._confirm(
            f"Set quantity to {new_qty}? (Unusually large — confirm to proceed.)"
        ):
            return
        idx = self.cart.lines.index(line)
        self.cart.set_quantity(idx, new_qty)
        self.cart_widget.refresh()
        self._numpad_clear()

    def _on_void(self) -> None:
        # Void selected cart line (different from voiding a completed transaction).
        line = self.cart_widget.selected_line()
        if line is None:
            self._info("Select a cart row to void.")
            return
        idx = self.cart.lines.index(line)
        self.cart.remove_line(idx)
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _on_cancel_item(self) -> None:
        self._on_void()   # alias: cancel selected line

    def _on_clear_cart(self) -> None:
        if self.cart.is_empty():
            return
        if self._confirm("Clear the entire cart?"):
            self.cart.clear()
            self._cash_partial_cents = 0
            self._last_dept_add = None
            self.cart_widget.refresh()
            self._numpad_clear()
            self._refresh_deals_banner()

    def _on_lottery_plus(self) -> None:
        cents = self._numpad_cents()
        if cents <= 0:
            self._info("Enter lottery amount on numpad first.")
            return
        ln = self.cart.add_lottery_sale(cents)
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._numpad_clear()
        self._refresh_deals_banner()

    def _on_lottery_minus(self) -> None:
        """Lottery payout = NEGATIVE cart line. No confirm popup.
        Total reduces accordingly; if cart was empty, total goes negative.
        Logged via lottery_ledger at payment time (atomic with sale).
        """
        cents = self._numpad_cents()
        if cents <= 0:
            self._error("Enter payout amount on the numpad first.")
            return
        try:
            ln = self.cart.add_lottery_payout(cents)
        except Exception:
            log.exception("lottery payout add failed")
            self._error("Failed to add payout line.")
            return
        idx = self.cart.lines.index(ln)
        self._numpad_clear()
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()
        self._numpad_clear()

    def _on_eod(self) -> None:
        """Footer EOD: force closing-cash count, generate PDF, close shift, lock."""
        if self.shift_id is None:
            self._error("No active shift — nothing to close.")
            return
        if not self.cart.is_empty():
            if not self._confirm(
                "Cart has items. Close shift anyway?\n"
                "(Items will be discarded — hold or finish them first.)"
            ):
                return

        try:
            from core import reports as _r
            pre_data = _r.collect_eod(self.shift_id)
        except Exception:
            log.exception("EOD pre-collect failed")
            self._error("Could not collect shift data. See errors.log.")
            return

        expected = pre_data["reconciliation"]["expected_cash_cents"]

        # FORCE closing cash count — no skip allowed
        dlg = CashCountDialog(expected_cents=expected, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return   # cashier cancelled — abort entire EOD
        closing_cash = dlg.counted_cents
        variance = closing_cash - expected

        if abs(variance) > 500:
            sign = "+" if variance > 0 else "-"
            if not self._confirm(
                f"Cash variance {sign}${abs(variance)/100:.2f} exceeds $5.00.\n\n"
                f"Expected: ${expected/100:.2f}\n"
                f"Counted:  ${closing_cash/100:.2f}\n\n"
                f"Close shift anyway?"
            ):
                return

        try:
            db.close_shift(self.shift_id, closing_cash)
            data = _r.collect_eod(self.shift_id)
            store = self.config_store_dict()
            pdf = _r.render_eod_pdf(data, store=store)
            log.info("EOD PDF: %s (variance=%s)", pdf, variance)
            self._open_file(pdf)
            self._info(
                f"EOD report saved.\n{pdf.name}\n\n"
                f"Variance: ${variance/100:+.2f}"
            )
            self.logout_requested.emit()
        except Exception:
            log.exception("EOD generation failed")
            self._error("EOD generation failed. See errors.log.")

    def config_store_dict(self) -> dict:
        return {
            "name":     self.store_name,
            "address":  "",   # filled from main config later
        }

    def _open_file(self, path) -> None:
        """Open path in OS default viewer (best-effort, swallow errors)."""
        try:
            import sys as _sys
            import subprocess as _sp
            p = str(path)
            if _sys.platform == "darwin":
                _sp.Popen(["open", p])
            elif _sys.platform == "win32":
                import os as _os
                _os.startfile(p)
            else:
                _sp.Popen(["xdg-open", p])
        except Exception:
            log.exception("could not open %s in system viewer", path)

    def _on_no_sale(self) -> None:
        if not self._confirm("Open drawer (no sale)?"):
            return
        if self.shift_id is None:
            log.info("no_sale without active shift — drawer only, no DB event written")
        else:
            try:
                db.log_cash_event(
                    self.shift_id, "no_sale", 0, self.cashier.name,
                    note="register no-sale",
                )
            except Exception:
                log.exception("no_sale event insert failed")
        self._open_cash_drawer()

    def _on_hold(self) -> None:
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        try:
            db.hold_transaction(self.cart.to_json(), self.cashier.name)
        except Exception:
            log.exception("hold failed")
            self._error("Failed to hold cart.")
            return
        self.cart.clear()
        self._last_dept_add = None
        self.cart_widget.refresh()
        self._refresh_held_count()
        self._refresh_deals_banner()
        # No info dialog; HELD pill above the cart is the visual confirmation

    def _on_retrieve(self) -> None:
        # Legacy entry point — Retrieve button removed; HELD pill on cart panel
        # is the new path. Forward to the pill handler so existing callers keep
        # working.
        held = db.list_held()
        if not held:
            self._info("No held carts.")
            return
        self.cart_widget._on_held_pill_clicked()

    def _on_split(self) -> None:
        self._stub("Split tender")

    def _on_price_check(self) -> None:
        self._stub("Price Check")

    def _on_override_price(self) -> None:
        self._stub("Override Price (admin PIN required)")

    # ─── Dialog helpers ──────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "POS", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "POS", msg)

    def _confirm(self, msg: str) -> bool:
        ret = QMessageBox.question(
            self, "POS", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    def _stub(self, name: str) -> None:
        self._info(f"{name} — not implemented yet (Phase 1 checkpoint scope).")


# ─── Payment worker (runs in QThread) ────────────────────────────────────────

class PaymentWorker(QObject):
    """Runs `terminal.request_payment(req)` off the UI thread.

    moveToThread the worker onto a fresh QThread; connect `finished` to your
    UI handler. The worker emits exactly once and is safe to delete after.
    """

    finished = pyqtSignal(object)   # PaymentResponse

    def __init__(self, terminal: PaymentTerminal, req: PaymentRequest):
        super().__init__()
        self.terminal = terminal
        self.req = req

    def run(self) -> None:
        try:
            resp = self.terminal.request_payment(self.req)
        except Exception as exc:
            log.exception("payment worker raised")
            resp = PaymentResponse.error(str(exc))
        self.finished.emit(resp)


# ─── Deals banner (top of register, below header) ────────────────────────────

class DealsBanner(QFrame):
    """Yellow banner showing active deals.

    - Triggered deals (in cart already): green-tinted dot prefix, plain text.
    - Near-miss deals (hint available): yellow lightbulb prefix + nudge text.
    - Inactive deals: muted dot.

    `update_deals` is idempotent — call after any cart change.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("deals_banner")
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"QFrame#deals_banner {{ background-color: {styles.COLORS['warning']};"
            f" color: {styles.COLORS['text_dark']}; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 2, 12, 2)
        h.setSpacing(0)
        self._label = QLabel("ACTIVE DEALS — (no deals configured)")
        self._label.setObjectName("deals_banner_label")
        self._label.setFont(QFont(styles.FONT_FAMILY, 11))
        self._label.setStyleSheet("background: transparent;")
        h.addWidget(self._label)

    def update_deals(self, active_deals, hints, triggered_ids) -> None:
        if not active_deals:
            self._label.setText("ACTIVE DEALS — (none)")
            return
        hint_by_id = {h["deal_id"]: h for h in hints}
        parts: list[str] = []
        for d in active_deals:
            if d.id in triggered_ids:
                # Already applied to cart
                parts.append(f"✓ {d.name}")
            elif d.id in hint_by_id:
                h = hint_by_id[d.id]
                if "missing_item_ids" in h:
                    parts.append(
                        f"💡 {d.name} — add missing item(s) to save ${h['savings_cents']/100:.2f}"
                    )
                else:
                    needed = h["need_qty"] - h["have_qty"]
                    parts.append(
                        f"💡 {d.name} — add {needed} more to save ${h['savings_cents']/100:.2f}"
                    )
            else:
                parts.append(f"• {d.name}")
        self._label.setText("ACTIVE DEALS:  " + "   |   ".join(parts))


# ─── Card payment bottom sheet (slides up from bottom) ───────────────────────

SHEET_HEIGHT_RATIO = 0.35
SHEET_ANIM_MS = 300
DOT_TICK_MS = 400


class CardPaymentSheet(QFrame):
    """Bottom-sheet card payment popup. Lives as a child of the parent register.

    States: processing → approved | declined | timeout | error.
    Each state mutates labels / bg color / action button row.
    """

    cancel_requested = pyqtSignal()
    try_again_clicked = pyqtSignal()
    accept_cash_clicked = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("card_payment_sheet")
        self._build()
        self._slide_anim: Optional[QPropertyAnimation] = None
        self.hide()

    def _build(self) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["navy"]))

        v = QVBoxLayout(self)
        v.setContentsMargins(40, 16, 40, 16)
        v.setSpacing(6)

        self._top_label = QLabel("WAITING FOR CUSTOMER")
        self._top_label.setObjectName("sheet_top_label")
        self._top_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(styles.FONT_FAMILY, 11); f.setBold(True)
        self._top_label.setFont(f)
        self._top_label.setStyleSheet(
            "color: #B8C5D6; background: transparent; letter-spacing: 2px;"
        )
        v.addWidget(self._top_label)

        self._amount_label = QLabel("$0.00")
        self._amount_label.setObjectName("sheet_amount")
        self._amount_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        af = QFont(styles.FONT_FAMILY, 36); af.setBold(True)
        self._amount_label.setFont(af)
        self._amount_label.setStyleSheet("color: white; background: transparent;")
        v.addWidget(self._amount_label)

        self._subtitle = QLabel("Please tap, insert or swipe card")
        self._subtitle.setObjectName("sheet_subtitle")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setFont(QFont(styles.FONT_FAMILY, 12))
        self._subtitle.setStyleSheet(
            "color: #B8C5D6; background: transparent;"
        )
        v.addWidget(self._subtitle)

        self._dots_label = QLabel("•••")
        self._dots_label.setObjectName("sheet_dots")
        self._dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        df = QFont(styles.FONT_FAMILY, 22); df.setBold(True)
        self._dots_label.setFont(df)
        self._dots_label.setStyleSheet("color: white; background: transparent;")
        v.addWidget(self._dots_label)

        v.addStretch(1)

        # Action row — swappable per state
        self._action_holder = QFrame()
        self._action_holder.setStyleSheet("background: transparent;")
        self._action_layout = QHBoxLayout(self._action_holder)
        self._action_layout.setContentsMargins(0, 0, 0, 0)
        self._action_layout.setSpacing(8)
        v.addWidget(self._action_holder)

        # Default action: CANCEL full width
        self._cancel_btn = self._mk_action_btn(
            "CANCEL", "sheet_cancel", styles.COLORS["btn_cancel"]
        )
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._action_layout.addWidget(self._cancel_btn)

        # Hidden timeout buttons (lazy-shown)
        self._try_again_btn: Optional[QPushButton] = None
        self._accept_cash_btn: Optional[QPushButton] = None

        # Cycling-dots timer
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(DOT_TICK_MS)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_state = 0

    @staticmethod
    def _qss(bg_color: str) -> str:
        return (
            f"QFrame#card_payment_sheet {{ background-color: {bg_color};"
            f" border-top: 2px solid white; }}"
        )

    def _mk_action_btn(self, text: str, name: str, color: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumHeight(50)
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        b.setFont(f)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: none; border-radius: 6px; }}"
            f"QPushButton:disabled {{ background-color: #555; color: #999; }}"
        )
        return b

    # ─── State transitions ───────────────────────────────────────────────────

    def show_processing(self, amount_cents: int) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["navy"]))
        self._amount_label.setText(f"${amount_cents / 100:.2f}")
        self._top_label.setText("WAITING FOR CUSTOMER")
        f = QFont(styles.FONT_FAMILY, 11); f.setBold(True)
        self._top_label.setFont(f)
        self._top_label.setStyleSheet(
            "color: #B8C5D6; background: transparent; letter-spacing: 2px;"
        )
        self._subtitle.setText("Please tap, insert or swipe card")
        self._dots_label.show()
        self._dots_label.setText("•••")
        self._dot_state = 0
        self._dot_timer.start()
        self._reset_action_row(showing="cancel")
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("CANCEL")
        self.slide_in()

    def lock_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Transaction in progress…")

    def show_approved(self) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["btn_cash"]))
        self._top_label.setText("✓ APPROVED")
        f = QFont(styles.FONT_FAMILY, 18); f.setBold(True)
        self._top_label.setFont(f)
        self._top_label.setStyleSheet("color: white; background: transparent; letter-spacing: 2px;")
        self._subtitle.setText("Transaction complete")
        self._dots_label.hide()
        self._dot_timer.stop()

    def show_declined(self) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["btn_cancel"]))
        self._top_label.setText("✗ DECLINED")
        f = QFont(styles.FONT_FAMILY, 18); f.setBold(True)
        self._top_label.setFont(f)
        self._top_label.setStyleSheet("color: white; background: transparent; letter-spacing: 2px;")
        self._subtitle.setText("Card was not accepted")
        self._dots_label.hide()
        self._dot_timer.stop()

    def show_timeout(self) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["warning"]))
        self._top_label.setText("⚠ TERMINAL NOT RESPONDING")
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        self._top_label.setFont(f)
        self._top_label.setStyleSheet("color: white; background: transparent; letter-spacing: 2px;")
        self._subtitle.setText("Try again or accept cash")
        self._dots_label.hide()
        self._dot_timer.stop()
        self._reset_action_row(showing="timeout")

    def show_error(self, msg: str) -> None:
        self.setStyleSheet(self._qss(styles.COLORS["btn_cancel"]))
        self._top_label.setText("⚠ PAYMENT ERROR")
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        self._top_label.setFont(f)
        self._subtitle.setText(msg)
        self._dots_label.hide()
        self._dot_timer.stop()

    def _reset_action_row(self, showing: str) -> None:
        # Strip current widgets
        while self._action_layout.count():
            item = self._action_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()

        if showing == "cancel":
            self._action_layout.addWidget(self._cancel_btn)
            self._cancel_btn.show()
        elif showing == "timeout":
            if self._try_again_btn is None:
                self._try_again_btn = self._mk_action_btn(
                    "Try Again", "sheet_try_again", styles.COLORS["btn_hold"]
                )
                self._try_again_btn.clicked.connect(self.try_again_clicked.emit)
            if self._accept_cash_btn is None:
                self._accept_cash_btn = self._mk_action_btn(
                    "Accept Cash", "sheet_accept_cash", styles.COLORS["btn_cash"]
                )
                self._accept_cash_btn.clicked.connect(self.accept_cash_clicked.emit)
            self._action_layout.addWidget(self._try_again_btn)
            self._action_layout.addWidget(self._accept_cash_btn)
            self._try_again_btn.show()
            self._accept_cash_btn.show()

    def _tick_dots(self) -> None:
        states = ["•   ", "••  ", "••• ", "••••"]
        self._dots_label.setText(states[self._dot_state % 4])
        self._dot_state += 1

    # ─── Slide animation ─────────────────────────────────────────────────────

    def slide_in(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        pw, ph = parent.width(), parent.height()
        sheet_h = max(220, int(ph * SHEET_HEIGHT_RATIO))
        self.setGeometry(0, ph, pw, sheet_h)
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(SHEET_ANIM_MS)
        anim.setStartValue(QRect(0, ph, pw, sheet_h))
        anim.setEndValue(QRect(0, ph - sheet_h, pw, sheet_h))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._slide_anim = anim

    def slide_out(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.hide()
            return
        pw, ph = parent.width(), parent.height()
        sheet_h = self.height() or max(220, int(ph * SHEET_HEIGHT_RATIO))
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(SHEET_ANIM_MS)
        anim.setStartValue(self.geometry())
        anim.setEndValue(QRect(0, ph, pw, sheet_h))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._after_slide_out)
        anim.start()
        self._slide_anim = anim

    def _after_slide_out(self) -> None:
        self._dot_timer.stop()
        self.hide()


# ─── Toast notification (auto-dismissing bottom-right pill) ──────────────────

class Toast(QLabel):
    """Quick-flash notification. Auto-deletes after `show_for(ms)`."""

    def __init__(self, parent: QWidget, message: str, *, danger: bool = False):
        super().__init__(message, parent)
        bg = styles.COLORS["btn_cancel"] if danger else styles.COLORS["btn_cash"]
        self.setObjectName("toast_danger" if danger else "toast_ok")
        self.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: white;"
            f" border-radius: 8px; padding: 12px 24px;"
            f" font-weight: bold; font-size: 13pt; }}"
        )
        self.adjustSize()

    def show_for(self, ms: int) -> None:
        parent = self.parentWidget()
        if parent is not None:
            pw, ph = parent.width(), parent.height()
            x = pw - self.width() - 24
            y = ph - self.height() - 80
            self.move(x, y)
        self.show()
        self.raise_()
        QTimer.singleShot(ms, self.deleteLater)


# ─── Cash count dialog (EOD shift close) ─────────────────────────────────────

class CashCountDialog(QDialog):
    """Force cashier to enter actual cash drawer count. Variance shown live.

    On accept: `self.counted_cents` is set. No skip — cashier cancels by Cancel,
    which aborts the entire EOD flow.
    """

    def __init__(self, *, expected_cents: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cash_count_dialog")
        self.setWindowTitle("Closing Cash Count")
        self.setModal(True)
        self.setMinimumSize(420, 540)
        self.expected_cents = expected_cents
        self.counted_cents: int = 0
        self._buf = ""
        self._build()
        self._render()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        title = QLabel("Closing Cash Count")
        title.setObjectName("cash_count_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(styles.FONT_FAMILY, 16); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        sub = QLabel("Count the cash drawer. Required to close shift.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        v.addWidget(sub)

        # Expected
        exp_row = QHBoxLayout()
        exp_lbl = QLabel("Expected in drawer:")
        exp_val = QLabel(f"${self.expected_cents / 100:.2f}")
        ef = QFont(styles.FONT_FAMILY, 14); ef.setBold(True)
        exp_val.setFont(ef)
        exp_val.setStyleSheet(f"color: {styles.COLORS['navy']};")
        exp_row.addWidget(exp_lbl); exp_row.addStretch(1); exp_row.addWidget(exp_val)
        v.addLayout(exp_row)

        # Counted display
        self._counted_lbl = QLabel("$0.00")
        self._counted_lbl.setObjectName("cash_count_counted")
        self._counted_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(styles.FONT_FAMILY, 32); cf.setBold(True)
        self._counted_lbl.setFont(cf)
        self._counted_lbl.setStyleSheet(
            f"color: {styles.COLORS['navy']}; padding: 12px;"
            f" border: 2px solid {styles.COLORS['blue_mid']}; border-radius: 8px;"
            f" background-color: white;"
        )
        v.addWidget(self._counted_lbl)

        # Variance label (color-coded)
        self._var_lbl = QLabel("Variance: $0.00")
        self._var_lbl.setObjectName("cash_count_variance")
        self._var_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vf = QFont(styles.FONT_FAMILY, 12); vf.setBold(True)
        self._var_lbl.setFont(vf)
        v.addWidget(self._var_lbl)

        # Numpad grid
        grid = QGridLayout()
        grid.setSpacing(6)
        positions = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
            ("0", 3, 0), ("00", 3, 1), ("←", 3, 2),
        ]
        for txt, r, c in positions:
            b = QPushButton(txt)
            b.setObjectName(f"cash_count_btn_{txt}")
            b.setMinimumHeight(50)
            bf = QFont(styles.FONT_FAMILY, 16); bf.setBold(True)
            b.setFont(bf)
            if txt == "←":
                b.clicked.connect(self._back)
            else:
                b.clicked.connect(lambda _ck=False, x=txt: self._input(x))
            grid.addWidget(b, r, c)
        v.addLayout(grid)

        # Action row
        h = QHBoxLayout(); h.setSpacing(8)
        clr = QPushButton("CLR"); clr.setObjectName("cash_count_btn_clr")
        clr.setMinimumHeight(48)
        clr.clicked.connect(self._clear)
        h.addWidget(clr)
        cancel = QPushButton("Cancel"); cancel.setObjectName("cash_count_cancel")
        cancel.setMinimumHeight(48)
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        ok = QPushButton("Close Shift"); ok.setObjectName("cash_count_ok")
        ok.setMinimumHeight(48)
        of = QFont(styles.FONT_FAMILY, 13); of.setBold(True)
        ok.setFont(of)
        ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 16px; }}"
            f"QPushButton:disabled {{ background-color: #BDBDBD; color: #757575; }}"
        )
        ok.setEnabled(False)
        ok.clicked.connect(self._accept)
        self._ok_btn = ok
        h.addWidget(ok)
        v.addLayout(h)

    def _input(self, ch: str) -> None:
        if len(self._buf.replace(".", "")) + len(ch) > 7:
            return
        self._buf += ch
        self._render()

    def _back(self) -> None:
        if not self._buf:
            return
        self._buf = self._buf[:-1]
        self._render()

    def _clear(self) -> None:
        self._buf = ""
        self._render()

    def _render(self) -> None:
        cents = int(self._buf) if self._buf.isdigit() else 0
        self._counted_lbl.setText(f"${cents / 100:.2f}")
        var = cents - self.expected_cents
        sign = "+" if var > 0 else "-" if var < 0 else ""
        self._var_lbl.setText(f"Variance: {sign}${abs(var) / 100:.2f}")
        if abs(var) <= 500:
            color = styles.COLORS["btn_cash"]   # green within $5
        elif abs(var) <= 2000:
            color = styles.COLORS["warning"]    # yellow $5-$20
        else:
            color = styles.COLORS["danger"]     # red >$20
        self._var_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
        # Enable OK only when buffer non-empty
        self._ok_btn.setEnabled(bool(self._buf))

    def _accept(self) -> None:
        self.counted_cents = int(self._buf) if self._buf.isdigit() else 0
        self.accept()


# ─── Item picker (cashier search → choose from partial-match list) ──────────

class PayoutCompleteDialog(QDialog):
    """Premium 'Lottery Payout Completed' confirmation dialog.

    Shown after a payout-only cash transaction. Distinct from ChangeDialog
    so cashier sees clearly that money was paid OUT (not received).
    """

    def __init__(self, txn_ref: str, payout_cents: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("payout_complete_dialog")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(
            styles.premium_dialog_qss() + styles.dialog_titlebar_qss()
            + "QFrame#poShadow { background: white; border-radius: 14px;"
            "  border: 1px solid #E1E4EA; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        shadow = QFrame()
        shadow.setObjectName("poShadow")
        sv = QVBoxLayout(shadow); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar); tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel("Lottery Payout Completed")
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        sv.addWidget(title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(28, 22, 28, 22)
        body.setSpacing(8)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        ref_lbl = QLabel(f"Transaction {txn_ref}")
        ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ref_lbl.setStyleSheet("color: #8A8F95; font-size: 10pt; background: transparent;")
        body.addWidget(ref_lbl)

        check = QLabel("✓")
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(styles.FONT_FAMILY, 36); cf.setBold(True)
        check.setFont(cf)
        check.setStyleSheet(f"color: {styles.COLORS['btn_lottery_p']}; background: transparent;")
        body.addWidget(check)

        cap = QLabel("Paid Out")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("color: #5A6573; font-size: 12pt; background: transparent;")
        body.addWidget(cap)

        amt = QLabel(f"${payout_cents/100:.2f}")
        amt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        af = QFont(styles.FONT_FAMILY, 56); af.setBold(True)
        amt.setFont(af)
        amt.setStyleSheet(
            f"color: {styles.COLORS['btn_lottery_p']}; background: transparent;"
            f" padding: 4px;"
        )
        body.addWidget(amt)

        sub = QLabel("Transaction saved successfully.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #8A8F95; font-size: 10pt; background: transparent;")
        body.addWidget(sub)

        body.addSpacing(6)

        ok = QPushButton("Done")
        ok.setObjectName("payout_done")
        ok.setMinimumSize(220, 56)
        ok.setDefault(True)
        ok.setAutoDefault(True)
        ok.setStyleSheet(styles.pill_button_qss("primary"))
        ok.clicked.connect(self.accept)
        ok_row = QHBoxLayout()
        ok_row.addStretch(1); ok_row.addWidget(ok); ok_row.addStretch(1)
        body.addLayout(ok_row)

        sv.addLayout(body)
        outer.addWidget(shadow)
        self.setMinimumSize(440, 360)
        self.resize(460, 380)
        ok.setFocus()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            self.accept(); return
        super().keyPressEvent(ev)


class PrintReceiptDialog(QDialog):
    """Premium receipt confirmation dialog.

    Custom-styled QDialog (replaces the native QMessageBox.question with the
    big stock '?' icon). Navy title bar, soft body, two pill buttons:
    No Thanks (ghost) and Print Receipt (success / default).

    UX:
      - Enter accepts (prints).
      - ESC rejects (skips print).
      - Print button auto-focused.
    """

    def __init__(self, *, title: str = "Receipt Options",
                 subtitle: str = "Print customer receipt?",
                 detail: str = "You can also reprint later from Receipts.",
                 parent: Optional[QWidget] = None,
                 ok_label: str = "Print Receipt",
                 cancel_label: str = "No Thanks",
                 glyph: str = "🧾",
                 glyph_color: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("print_receipt_dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setStyleSheet(
            styles.premium_dialog_qss() + styles.dialog_titlebar_qss()
            + "QFrame#prdShadow { background: white; border-radius: 14px;"
            "  border: 1px solid #E1E4EA; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        shadow = QFrame()
        shadow.setObjectName("prdShadow")
        sv = QVBoxLayout(shadow)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        # Navy title bar
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel(title)
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        sv.addWidget(title_bar)

        # Body
        body = QVBoxLayout()
        body.setContentsMargins(28, 22, 28, 18)
        body.setSpacing(10)

        # Receipt glyph + subtitle
        head = QHBoxLayout()
        head.setSpacing(14)
        glyph_lbl = QLabel(glyph)
        gf = QFont(styles.FONT_FAMILY, 28); gf.setBold(True); glyph_lbl.setFont(gf)
        glyph_color_css = (
            f"color: {glyph_color}; " if glyph_color else ""
        )
        glyph_lbl.setStyleSheet(f"{glyph_color_css}background: transparent;")
        head.addWidget(glyph_lbl)
        sub_v = QVBoxLayout(); sub_v.setSpacing(2)
        sub = QLabel(subtitle)
        sf = QFont(styles.FONT_FAMILY, 13); sf.setBold(True)
        sub.setFont(sf)
        sub.setStyleSheet(f"color: {styles.COLORS['navy']}; background: transparent;")
        sub_v.addWidget(sub)
        if detail:
            det = QLabel(detail)
            det.setStyleSheet("color: #5A6573; font-size: 10pt; background: transparent;")
            det.setWordWrap(True)
            sub_v.addWidget(det)
        head.addLayout(sub_v, stretch=1)
        body.addLayout(head)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(12)
        cancel = QPushButton(cancel_label)
        cancel.setObjectName("prd_cancel")
        cancel.setMinimumSize(180, 56)
        cancel.setStyleSheet(styles.pill_button_qss("ghost"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel, stretch=1)

        ok = QPushButton(ok_label)
        ok.setObjectName("prd_ok")
        ok.setMinimumSize(180, 56)
        ok.setDefault(True)
        ok.setAutoDefault(True)
        ok.setStyleSheet(styles.pill_button_qss("success"))
        ok.clicked.connect(self.accept)
        btns.addWidget(ok, stretch=1)
        body.addLayout(btns)

        sv.addLayout(body)
        outer.addWidget(shadow)

        self.setMinimumSize(500, 240)
        self.resize(520, 260)
        # Auto-focus Print so Enter prints immediately.
        ok.setFocus()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept(); return
        if ev.key() == Qt.Key.Key_Escape:
            self.reject(); return
        super().keyPressEvent(ev)


class ItemPickerDialog(QDialog):
    """Premium product search picker.

    Layout: navy title bar + live debounced search field + table with
    UPC | Description | Department | Price columns + footer with result
    count and Cancel/Add buttons. Enter or double-click adds; ESC closes.
    """

    def __init__(self, items: list, parent: Optional[QWidget] = None,
                 *, initial_query: str = ""):
        super().__init__(parent)
        self.setObjectName("item_picker_dialog")
        self.setWindowTitle("Search Results")
        self.setModal(True)
        self.picked = None  # type: Optional[dict]
        self._initial_items = list(items)

        from PyQt6.QtWidgets import (
            QHeaderView as _QHeaderView,
            QTableWidget as _QTable,
            QTableWidgetItem as _QTI,
            QAbstractItemView as _QAIV,
        )
        self._QTable = _QTable
        self._QTI = _QTI

        self.setStyleSheet(
            styles.premium_dialog_qss()
            + styles.dialog_titlebar_qss()
            + styles.premium_table_qss()
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Navy title bar
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar); tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel("Product Search")
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        outer.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("card")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(18, 14, 18, 14)
        bv.setSpacing(10)
        wrap = QVBoxLayout(); wrap.setContentsMargins(14, 12, 14, 12); wrap.setSpacing(0)
        wrap.addWidget(body)
        outer.addLayout(wrap, stretch=1)

        # Live search field
        self._search = QLineEdit()
        self._search.setObjectName("picker_search")
        self._search.setProperty("touchKeyboard", "text")
        self._search.setPlaceholderText("Search by name or UPC…")
        self._search.setText(initial_query)
        self._search.setMinimumHeight(42)
        self._search.returnPressed.connect(self._accept)
        bv.addWidget(self._search)

        # Table
        self._table = _QTable()
        self._table.setObjectName("picker_table")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["UPC", "Description", "Department", "Price"])
        self._table.setEditTriggers(_QAIV.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(_QAIV.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(_QAIV.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_QHeaderView.ResizeMode.Stretch)
        hh.setStretchLastSection(False)
        self._table.itemDoubleClicked.connect(lambda _it: self._accept())
        bv.addWidget(self._table, stretch=1)

        # Footer
        footer = QHBoxLayout(); footer.setSpacing(12)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: #5A6573; font-size: 10pt;")
        footer.addWidget(self._count_lbl)
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("picker_cancel")
        cancel.setMinimumSize(120, 44)
        cancel.setStyleSheet(styles.pill_button_qss("ghost"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        ok = QPushButton("Add to Cart")
        ok.setObjectName("picker_ok")
        ok.setMinimumSize(180, 44)
        ok.setDefault(True)
        ok.setStyleSheet(styles.pill_button_qss("success"))
        ok.clicked.connect(self._accept)
        footer.addWidget(ok)
        bv.addLayout(footer)

        # Debounced search
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._refresh_table)
        self._search.textChanged.connect(lambda _t: self._debounce.start())

        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        self._refresh_table()
        self._table.setFocus()

    def _refresh_table(self) -> None:
        q = (self._search.text() or "").strip()
        if not q:
            rows = self._initial_items
        else:
            try:
                rows = db.search_items(q, active_only=True, limit=200)
            except Exception:
                log.exception("picker search failed")
                rows = []
        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            upc = self._QTI(r.get("barcode") or "")
            upc.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(ri, 0, upc)
            self._table.setItem(ri, 1, self._QTI(r.get("name", "")))
            self._table.setItem(ri, 2, self._QTI(r.get("department", "")))
            price = self._QTI(f"${r.get('price_cents', 0)/100:.2f}")
            price.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(ri, 3, price)
        if rows:
            self._table.selectRow(0)
        n = len(rows)
        self._count_lbl.setText(f"{n} result{'' if n == 1 else 's'}")

    def _accept(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        ri = rows[0].row()
        cell = self._table.item(ri, 0)
        if cell is None:
            return
        self.picked = cell.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def keyPressEvent(self, ev) -> None:
        # Enter on the table accepts; ESC closes (default).
        if ev.key() == Qt.Key.Key_Return or ev.key() == Qt.Key.Key_Enter:
            if self._table.hasFocus():
                self._accept(); return
        super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:
        try:
            self._debounce.stop()
        except Exception:
            pass
        # Ensure on-screen keyboard is closed alongside the picker.
        try:
            from ui.cashier.touch_keyboard import close_active_keyboard
            close_active_keyboard()
        except Exception:
            pass
        super().closeEvent(ev)


# ─── Generic amount-entry dialog (lottery payout, dept manual entry) ────────

class AmountEntryDialog(QDialog):
    """Small modal: numpad-only amount entry. Returns `cents` on accept.

    Buffer convention matches main numpad: digits-only string read as cents.
    """

    PRICE_MAX_DIGITS = 7

    def __init__(self, title: str, total_cents: int = 0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("amount_entry_dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(360, 480)
        self.cents = 0
        self._buf = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        title_lbl = QLabel(title)
        tf = QFont(styles.FONT_FAMILY, 16); tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title_lbl)

        self._input = QLineEdit("$0.00")
        self._input.setObjectName("amt_dlg_input")
        self._input.setReadOnly(True)
        self._input.setAlignment(Qt.AlignmentFlag.AlignRight)
        df = QFont(styles.FONT_FAMILY, 24); df.setBold(True)
        self._input.setFont(df)
        self._input.setStyleSheet(
            "QLineEdit { padding: 8px; border: 2px solid #B0BEC5;"
            " border-radius: 6px; background: white; }"
        )
        v.addWidget(self._input)

        kp = QGridLayout(); kp.setSpacing(4)
        for r, row in enumerate([["7","8","9"],["4","5","6"],["1","2","3"],["C","0","⌫"]]):
            for c, ch in enumerate(row):
                b = QPushButton(ch)
                b.setObjectName(f"amt_kp_{ch}")
                b.setMinimumHeight(50)
                kf = QFont(styles.FONT_FAMILY, 18); kf.setBold(True)
                b.setFont(kf)
                b.setStyleSheet(
                    "QPushButton { background-color: white; color: #333;"
                    " border: 1px solid #DDD; border-radius: 4px; }"
                    "QPushButton:pressed { background-color: #EEE; }"
                )
                b.clicked.connect(lambda _ck=False, x=ch: self._press(x))
                kp.addWidget(b, r, c)
        v.addLayout(kp)

        btns = QHBoxLayout(); btns.setSpacing(10)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("amt_dlg_cancel")
        cancel.setMinimumHeight(48)
        cancel.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_void']}; color: white;"
            f" border: none; border-radius: 6px; font-weight: bold; font-size: 13pt; }}"
        )
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        self._ok = QPushButton("CONFIRM")
        self._ok.setObjectName("amt_dlg_ok")
        self._ok.setMinimumHeight(48)
        self._ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; font-weight: bold; font-size: 14pt; }}"
            f"QPushButton:disabled {{ background-color: #BDBDBD; color: #757575; }}"
        )
        self._ok.setDefault(True)
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._accept)
        btns.addWidget(self._ok, stretch=2)
        v.addLayout(btns)

    def _press(self, ch: str) -> None:
        if ch == "⌫":
            self._buf = self._buf[:-1]
        elif ch == "C":
            self._buf = ""
        else:
            cand = self._buf + ch
            if len(cand) <= self.PRICE_MAX_DIGITS:
                self._buf = cand.lstrip("0") or ""
        cents = int(self._buf) if self._buf.isdigit() else 0
        self._input.setText(f"${cents / 100:.2f}")
        self._ok.setEnabled(cents > 0)

    def _accept(self) -> None:
        self.cents = int(self._buf) if self._buf.isdigit() else 0
        if self.cents > 0:
            self.accept()

    def keyPressEvent(self, e):
        from PyQt6.QtCore import Qt as _Qt
        k = e.key()
        if k in (_Qt.Key.Key_Return, _Qt.Key.Key_Enter):
            if self._ok.isEnabled(): self._accept()
            return
        if k == _Qt.Key.Key_Escape:
            self.reject(); return
        if k == _Qt.Key.Key_Backspace:
            self._press("⌫"); return
        if k == _Qt.Key.Key_Delete:
            self._press("C"); return
        t = e.text()
        if t and t in "0123456789":
            self._press(t); return
        super().keyPressEvent(e)


# ─── Cash payment dialog (numpad + quicks + live change) ─────────────────────

class CashPaymentDialog(QDialog):
    """Modal cash-tender entry. Built-in numpad + quick amounts.

    `received_cents` is exposed after `accept()`. Empty input → exact payment
    (received_cents == 0; caller substitutes total).
    Buffer convention matches main numpad: digits-only string interpreted as
    cents (e.g. "500" → $5.00).
    """

    PRICE_MAX_DIGITS = 7   # $99,999.99

    def __init__(self, total_cents: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cash_payment_dialog")
        self.setWindowTitle("Cash Payment")
        self.setModal(True)
        self.setMinimumSize(460, 640)
        self.total_cents = int(total_cents)
        self.received_cents = 0
        self._buf = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        # Total — large, navy
        lbl = QLabel("TOTAL")
        lf = QFont(styles.FONT_FAMILY, 13); lf.setBold(True)
        lbl.setFont(lf)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lbl)

        amt = QLabel(f"${self.total_cents / 100:.2f}")
        af = QFont(styles.FONT_FAMILY, 36); af.setBold(True)
        amt.setFont(af)
        amt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        amt.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(amt)

        # Input display (read-only, autofocus by being on top of focus chain)
        self._input = QLineEdit("$0.00")
        self._input.setObjectName("cash_dlg_input")
        self._input.setReadOnly(True)
        self._input.setAlignment(Qt.AlignmentFlag.AlignRight)
        df = QFont(styles.FONT_FAMILY, 24); df.setBold(True)
        self._input.setFont(df)
        self._input.setStyleSheet(
            "QLineEdit { padding: 10px; border: 2px solid #B0BEC5;"
            " border-radius: 6px; background: white; }"
        )
        v.addWidget(self._input)

        # Quick amounts: Exact / $5 / $10 / $20 / $50
        quick = QHBoxLayout(); quick.setSpacing(6)
        for label, cents in [("Exact", -1), ("$5", 500), ("$10", 1000),
                             ("$20", 2000), ("$50", 5000)]:
            b = QPushButton(label)
            b.setObjectName(f"cash_quick_{label}")
            b.setMinimumHeight(50)
            qf = QFont(styles.FONT_FAMILY, 13); qf.setBold(True)
            b.setFont(qf)
            b.setStyleSheet(
                "QPushButton { background-color: #F39C12; color: white;"
                " border: none; border-radius: 6px; }"
                "QPushButton:pressed { background-color: #C77E0E; }"
            )
            if cents == -1:
                b.clicked.connect(self._set_exact)
            else:
                b.clicked.connect(lambda _ck=False, c=cents: self._add_quick(c))
            quick.addWidget(b)
        v.addLayout(quick)

        # Numeric keypad
        kp = QGridLayout(); kp.setSpacing(4)
        layout_rows = [["7","8","9"], ["4","5","6"], ["1","2","3"], ["C","0","⌫"]]
        for r, row in enumerate(layout_rows):
            for c, ch in enumerate(row):
                b = QPushButton(ch)
                b.setObjectName(f"cash_kp_{ch}")
                b.setMinimumHeight(54)
                kf = QFont(styles.FONT_FAMILY, 18); kf.setBold(True)
                b.setFont(kf)
                b.setStyleSheet(
                    "QPushButton { background-color: white; color: #333;"
                    " border: 1px solid #DDD; border-radius: 4px; }"
                    "QPushButton:pressed { background-color: #EEE; }"
                )
                b.clicked.connect(lambda _ck=False, x=ch: self._press(x))
                kp.addWidget(b, r, c)
        v.addLayout(kp)

        # Live RECEIVED + CHANGE
        self._received_lbl = QLabel("Received: $0.00")
        rf = QFont(styles.FONT_FAMILY, 13); rf.setBold(True)
        self._received_lbl.setFont(rf)
        self._received_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._received_lbl)

        self._change_lbl = QLabel("Change: $0.00")
        cf = QFont(styles.FONT_FAMILY, 22); cf.setBold(True)
        self._change_lbl.setFont(cf)
        self._change_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._change_lbl)

        # Cancel | CONFIRM
        btns = QHBoxLayout(); btns.setSpacing(10)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("cash_dlg_cancel")
        cancel.setMinimumHeight(56)
        cancel.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_void']}; color: white;"
            f" border: none; border-radius: 6px; font-weight: bold; font-size: 14pt; }}"
        )
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        self._ok = QPushButton("CONFIRM")
        self._ok.setObjectName("cash_dlg_ok")
        self._ok.setMinimumHeight(56)
        self._ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; font-weight: bold; font-size: 16pt; }}"
            f"QPushButton:disabled {{ background-color: #BDBDBD; color: #757575; }}"
        )
        self._ok.setDefault(True)
        self._ok.clicked.connect(self._accept)
        btns.addWidget(self._ok, stretch=2)
        v.addLayout(btns)

        self._update()

    # ─── input handling ──────────────────────────────────────────────────────

    def _press(self, ch: str) -> None:
        if ch == "⌫":
            self._buf = self._buf[:-1]
        elif ch == "C":
            self._buf = ""
        else:  # digit
            cand = self._buf + ch
            if len(cand) <= self.PRICE_MAX_DIGITS:
                # Strip leading zeros silently to keep display clean.
                self._buf = cand.lstrip("0") or ch
                if self._buf == "0":
                    self._buf = ""
        self._update()

    def _add_quick(self, cents: int) -> None:
        """Quick buttons ADD to current — enables stacked tenders ($20 + $5)."""
        new = self._buf_to_cents() + cents
        self._set_cents(new)

    def _set_exact(self) -> None:
        self._set_cents(self.total_cents)

    def _set_cents(self, cents: int) -> None:
        s = str(int(cents))
        if len(s) > self.PRICE_MAX_DIGITS:
            return
        self._buf = s
        self._update()

    def _buf_to_cents(self) -> int:
        return int(self._buf) if self._buf.isdigit() else 0

    def _update(self) -> None:
        cents = self._buf_to_cents()
        self._input.setText(f"${cents / 100:.2f}")
        self._received_lbl.setText(f"Received: ${cents / 100:.2f}")
        if cents == 0:
            # Empty input → exact payment fallback on confirm.
            self._change_lbl.setText("Exact payment")
            self._change_lbl.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
            self._ok.setEnabled(True)
            return
        if cents < self.total_cents:
            short = self.total_cents - cents
            self._change_lbl.setText(f"Short: -${short / 100:.2f}")
            self._change_lbl.setStyleSheet(f"color: {styles.COLORS['danger']};")
            self._ok.setEnabled(False)
            return
        change = cents - self.total_cents
        self._change_lbl.setText(f"Change: ${change / 100:.2f}")
        self._change_lbl.setStyleSheet(f"color: {styles.COLORS['btn_cash']};")
        self._ok.setEnabled(True)

    def _accept(self) -> None:
        self.received_cents = self._buf_to_cents()
        self.accept()

    def keyPressEvent(self, e):
        from PyQt6.QtCore import Qt as _Qt
        k = e.key()
        if k in (_Qt.Key.Key_Return, _Qt.Key.Key_Enter):
            if self._ok.isEnabled():
                self._accept()
            return
        if k == _Qt.Key.Key_Escape:
            self.reject(); return
        if k == _Qt.Key.Key_Backspace:
            self._press("⌫"); return
        if k == _Qt.Key.Key_Delete:
            self._press("C"); return
        t = e.text()
        if t and t in "0123456789":
            self._press(t); return
        super().keyPressEvent(e)


# ─── Change dialog ───────────────────────────────────────────────────────────

class ChangeDialog(QDialog):
    """Premium 'Payment Complete' dialog shown after cash sale completes.

    Visual: navy title bar + soft body + green ✓ glyph + huge change amount +
    'Cash payment accepted' caption + Done pill.

    UX:
      - Centered modal
      - Enter / Esc → accept (close)
      - Optional auto-close after 3s (caller controls via auto_close_ms)
    """

    def __init__(self, txn_ref: str, change_cents: int,
                 parent: Optional[QWidget] = None,
                 *, auto_close_ms: int = 0):
        super().__init__(parent)
        self.setObjectName("change_dialog")
        self.setWindowTitle("Payment Complete")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setStyleSheet(
            styles.premium_dialog_qss() + styles.dialog_titlebar_qss()
            + "QFrame#cdShadow { background: white; border-radius: 14px;"
            "  border: 1px solid #E1E4EA; }"
            f"QLabel#cd_amount {{ color: {styles.COLORS['btn_cash']}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)

        shadow = QFrame()
        shadow.setObjectName("cdShadow")
        sv = QVBoxLayout(shadow)
        sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar); tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel("Payment Complete")
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        sv.addWidget(title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(28, 22, 28, 22)
        body.setSpacing(8)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Transaction ref (small, muted)
        ref_lbl = QLabel(f"Transaction {txn_ref}")
        ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ref_lbl.setStyleSheet("color: #8A8F95; font-size: 10pt; background: transparent;")
        body.addWidget(ref_lbl)

        # Success glyph
        check = QLabel("✓")
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(styles.FONT_FAMILY, 36); cf.setBold(True)
        check.setFont(cf)
        check.setStyleSheet(
            f"color: {styles.COLORS['btn_cash']}; background: transparent;"
        )
        body.addWidget(check)

        # "Change Due"
        change_lbl = QLabel("Change Due")
        change_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clf = QFont(styles.FONT_FAMILY, 12)
        change_lbl.setFont(clf)
        change_lbl.setStyleSheet("color: #5A6573; background: transparent;")
        body.addWidget(change_lbl)

        # Amount — huge, green
        amount = QLabel(f"${change_cents / 100:.2f}")
        amount.setObjectName("cd_amount")
        amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        af = QFont(styles.FONT_FAMILY, 56); af.setBold(True)
        amount.setFont(af)
        amount.setStyleSheet(
            f"color: {styles.COLORS['btn_cash']}; background: transparent;"
            f" padding: 4px;"
        )
        body.addWidget(amount)

        # Caption
        caption = QLabel("Cash payment accepted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet("color: #8A8F95; font-size: 10pt; background: transparent;")
        body.addWidget(caption)

        body.addSpacing(6)

        # Done button
        ok = QPushButton("Done")
        ok.setObjectName("change_ok")
        ok.setMinimumSize(220, 56)
        ok.setDefault(True)
        ok.setAutoDefault(True)
        ok.setStyleSheet(styles.pill_button_qss("success"))
        ok.clicked.connect(self.accept)
        ok_row = QHBoxLayout()
        ok_row.addStretch(1); ok_row.addWidget(ok); ok_row.addStretch(1)
        body.addLayout(ok_row)

        sv.addLayout(body)
        outer.addWidget(shadow)

        self.setMinimumSize(440, 360)
        self.resize(460, 380)
        ok.setFocus()

        # Optional auto-close (caller decides) — non-blocking timer.
        if auto_close_ms > 0:
            self._auto_timer = QTimer(self)
            self._auto_timer.setSingleShot(True)
            self._auto_timer.setInterval(int(auto_close_ms))
            self._auto_timer.timeout.connect(self.accept)
            self._auto_timer.start()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            self.accept(); return
        super().keyPressEvent(ev)
