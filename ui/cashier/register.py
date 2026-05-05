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
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRect,
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
        bar.setStyleSheet(f"background-color: {styles.COLORS['navy']}; color: white;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(16)

        title = QLabel("CITYLINK")
        title.setObjectName("hdr_title")
        f = QFont(styles.FONT_FAMILY, 14)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: white;")

        store = QLabel(self.store_name)
        store.setObjectName("hdr_store")
        store.setFont(QFont(styles.FONT_FAMILY, 11))
        store.setStyleSheet("color: white;")

        cashier = QLabel(f"Cashier: {self.cashier.name}")
        cashier.setObjectName("hdr_cashier")
        cashier.setFont(QFont(styles.FONT_FAMILY, 11))
        cashier.setStyleSheet("color: white;")

        self._clock_label = QLabel("")
        self._clock_label.setObjectName("hdr_clock")
        self._clock_label.setFont(QFont(styles.FONT_FAMILY, 11))
        self._clock_label.setStyleSheet("color: white;")
        self._update_clock()

        # Header dot reflects terminal state:
        #   green "● TCP"    = real terminal connected
        #   orange "● MOCK"  = mock terminal connected (test mode)
        #   red    "● CASH-ONLY" = no terminal / disconnected
        self._terminal_dot = QLabel()
        self._terminal_dot.setObjectName("hdr_terminal_dot")
        self._terminal_dot.setFont(QFont(styles.FONT_FAMILY, 11))
        self._refresh_terminal_dot()

        h.addWidget(title)
        h.addWidget(store)
        h.addStretch(1)
        h.addWidget(cashier)
        h.addWidget(self._clock_label)
        h.addWidget(self._terminal_dot)
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

    # ─── Left panel: cart only (Hold/Cancel live inside cart_widget now) ─────

    def _build_left_panel(self) -> QWidget:
        self.cart_widget = CartWidget(self.cart)
        self.cart_widget.setObjectName("register_cart")
        self.cart_widget.hold_clicked.connect(self._on_hold)
        self.cart_widget.cancel_clicked.connect(self._on_clear_cart)
        self.cart_widget.restore_held_requested.connect(self._on_restore_held)
        # Refresh deals banner whenever the cart changes via inline controls
        self.cart_widget.qty_changed.connect(lambda _n: self._refresh_deals_banner())
        self.cart_widget.item_removed.connect(self._refresh_deals_banner)
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

        v.addWidget(self._build_tabs_bar())
        v.addWidget(self._build_dept_tiles())
        v.addWidget(self._build_separator())
        v.addWidget(self._build_search_bar())

        # NRS numpad+action grid
        self._numpad_buffer: str = ""
        # Hidden display label (kept for backward compat with _numpad_render());
        # not added to layout — amount visibility relies on cart TOTAL panel.
        self._numpad_display = QLabel("$0.00")
        self._numpad_display.setObjectName("numpad_display")
        self._numpad_display.hide()

        v.addWidget(self._build_numpad_grid())
        sec = self._build_sec_rows()
        sec.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(sec, stretch=1)   # absorbs leftover vertical space, white bg fills gap
        v.addWidget(self._build_info_bar())
        return panel

    # ─── Secondary action rows (Hold / Cancel / Clear / Override / Price Check) ──

    def _build_sec_rows(self) -> QWidget:
        container = QFrame()
        container.setObjectName("sec_rows_container")
        container.setStyleSheet("QFrame#sec_rows_container { background-color: white; }")
        v = QVBoxLayout(container)
        v.setSpacing(3)
        v.setContentsMargins(2, 4, 2, 2)

        # Row A: Cancel Item / No Sale / Override Price
        # (Hold + Retrieve dropped — Hold is inside cart panel, Retrieve via HELD pill)
        h1 = QHBoxLayout(); h1.setSpacing(3)
        for label, name, color_key, slot in [
            ("Cancel Item",    "act_cancel_item",    "btn_cancel",  self._on_cancel_item),
            ("No Sale",        "act_no_sale",        "btn_no_sale", self._on_no_sale),
            ("Override Price", "act_override_price", "btn_void",    self._on_override_price),
        ]:
            b = self._mk_action(label, name, color_key, height=40)
            b.clicked.connect(slot)
            h1.addWidget(b)
        v.addLayout(h1)

        # Row C: Price Check full width
        pc = self._mk_action("Price Check", "act_price_check", "btn_hold", height=40)
        pc.clicked.connect(self._on_price_check)
        v.addWidget(pc)

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
            "QPushButton { background-color: #B8C5D6; color: #1B3A6B;"
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
        container = QFrame()
        container.setObjectName("dept_tiles")
        # Light grey background distinguishes dept area from white numpad area
        container.setStyleSheet("QFrame#dept_tiles { background-color: #E8E8E8; }")
        grid = QGridLayout(container)
        grid.setSpacing(8)
        grid.setContentsMargins(8, 4, 8, 8)
        for c in range(6):
            grid.setColumnStretch(c, 1)

        for i, (label, color, dept_id) in enumerate(self.NRS_DEPT_TILES):
            r, c = divmod(i, 6)
            b = QPushButton(label)
            b.setObjectName(f"nrs_dept_{i}")
            b.setMinimumHeight(52)
            b.setMaximumHeight(52)
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: 1px solid #888; border-radius: 4px;"
                f" font-weight: bold; font-size: 10pt; padding: 4px; }}"
            )
            if dept_id is not None:
                b.clicked.connect(lambda _ck=False, x=dept_id: self._on_dept_selected(x))
            else:
                b.clicked.connect(lambda _ck=False, x=label: self._stub(f"{x} (NRS-only dept, not configured)"))
            grid.addWidget(b, r, c)

        # Empty cells fill row 2 to slot 11
        for slot in range(len(self.NRS_DEPT_TILES), 12):
            r, c = divmod(slot, 6)
            grid.addWidget(self._empty_tile(52, bg="#E8E8E8"), r, c)

        return container

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
        grid.setSpacing(2)
        grid.setContentsMargins(2, 2, 2, 2)
        for c in range(6):
            grid.setColumnStretch(c, 1)

        DIGIT_QSS = (
            "QPushButton { background-color: white; color: #333;"
            " border: 1px solid #DDD; font-weight: bold; font-size: 22pt; }"
            "QPushButton:pressed { background-color: #EEE; }"
        )

        def mk_digit(text: str, name: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(name)
            b.setMinimumHeight(46); b.setMaximumHeight(46)
            b.setStyleSheet(DIGIT_QSS)
            return b

        def mk_act(text: str, name: str, color: str, *, font_pt: int = 11) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(name)
            b.setMinimumHeight(46); b.setMaximumHeight(46)
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: none; font-weight: bold; font-size: {font_pt}pt; }}"
            )
            return b

        # ─ Row 0: 7 8 9 | LOTTERY PAYOUT (green) | $20 (orange) | $10 (orange) ─
        for i, d in enumerate(["7", "8", "9"]):
            b = mk_digit(d, f"npd_btn_{d}")
            b.clicked.connect(lambda _ck=False, x=d: self._numpad_input(x))
            grid.addWidget(b, 0, i)
        b = mk_act("LOTTERY\nPAYOUT", "act_lottery_minus", "#4CAF50", font_pt=10)
        b.clicked.connect(self._on_lottery_minus); grid.addWidget(b, 0, 3)
        b = mk_act("$20", "act_cash_20", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(2000)); grid.addWidget(b, 0, 4)
        b = mk_act("$10", "act_cash_10", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(1000)); grid.addWidget(b, 0, 5)

        # ─ Row 1: 4 5 6 | $5 (orange) | empty | Basket Discount (blue) ─
        for i, d in enumerate(["4", "5", "6"]):
            b = mk_digit(d, f"npd_btn_{d}")
            b.clicked.connect(lambda _ck=False, x=d: self._numpad_input(x))
            grid.addWidget(b, 1, i)
        b = mk_act("$5", "act_cash_5", "#F39C12", font_pt=14)
        b.clicked.connect(lambda: self._on_cash_shortcut(500)); grid.addWidget(b, 1, 3)
        grid.addWidget(self._empty_tile(46), 1, 4)
        b = mk_act("Basket\nDiscount", "act_basket_discount", "#2196F3", font_pt=10)
        b.clicked.connect(lambda: self._stub("Basket Discount"))
        grid.addWidget(b, 1, 5)

        # ─ Row 2: 1 2 3 | Credit Debit (red) | empty | Cash (green) ─
        for i, d in enumerate(["1", "2", "3"]):
            b = mk_digit(d, f"npd_btn_{d}")
            b.clicked.connect(lambda _ck=False, x=d: self._numpad_input(x))
            grid.addWidget(b, 2, i)
        b = mk_act("Credit\nDebit", "act_card", "#E53935", font_pt=11)
        b.clicked.connect(self._on_card); grid.addWidget(b, 2, 3)
        grid.addWidget(self._empty_tile(46), 2, 4)
        b = mk_act("Cash", "act_cash", "#4CAF50", font_pt=14)
        b.clicked.connect(self._on_cash); grid.addWidget(b, 2, 5)

        # ─ Row 3: 0 00 @ | empty | Refund (red) | empty ─
        b = mk_digit("0", "npd_btn_0")
        b.clicked.connect(lambda _ck=False: self._numpad_input("0")); grid.addWidget(b, 3, 0)
        b = mk_digit("00", "npd_btn_00")
        b.clicked.connect(lambda _ck=False: self._numpad_input("00")); grid.addWidget(b, 3, 1)
        at_btn = mk_digit("@", "npd_btn_at")
        grid.addWidget(at_btn, 3, 2)
        grid.addWidget(self._empty_tile(46), 3, 3)
        b = mk_act("Refund", "act_refund", "#E53935", font_pt=12)
        b.clicked.connect(lambda: self._stub("Refund"))
        grid.addWidget(b, 3, 4)
        grid.addWidget(self._empty_tile(46), 3, 5)

        return container

    # ─── Bottom info bar ─────────────────────────────────────────────────────

    def _build_info_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("info_bar")
        bar.setFixedHeight(28)
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
        bar.setFixedHeight(50)
        bar.setStyleSheet("QFrame#search_bar { background-color: #DCDCDC; }")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

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
        input_wrap.setStyleSheet("QFrame#search_input_wrap { background-color: white; }")
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
        self._search_input.setPlaceholderText("")
        self._search_input.setStyleSheet(
            "QLineEdit { background: transparent; border: none;"
            " font-size: 13pt; color: #333; padding: 4px; }"
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
        self._search_input.clear()
        self._search_input.setFocus()

    def _on_search_submit(self) -> None:
        text = self._search_input.text().strip()
        if not text:
            return
        # Treat anything submitted via Enter as a barcode for now.
        # Future: distinguish numeric (barcode) from text (name search).
        self._handle_barcode(text)
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
        self._numpad_display.setText(f"${cents / 100:.2f}")

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
            ("Reprint",    "ftr_reprint",  lambda: self._stub("Reprint")),
            ("EOD",        "ftr_eod",      lambda: self._stub("EOD")),
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

    def _handle_barcode(self, barcode: str) -> None:
        log.info("scan: %s", barcode)
        if hasattr(self, "_search_input") and self._search_input is not None:
            self._search_input.setText(barcode)
            QTimer.singleShot(1500, lambda: self._search_input.clear()
                              if self._search_input.text() == barcode else None)
        row = db.get_item_by_barcode(barcode)
        if row is None:
            db.log_barcode_miss(barcode)
            self._info(f"Unknown barcode: {barcode}\nAdmin → Inventory to add it.")
            return
        from core.models import Item
        ln = self.cart.add_item(Item.from_row(row))
        idx = self.cart.lines.index(ln)
        self.cart_widget.refresh(flash_index=idx)
        self._refresh_deals_banner()

    # ─── department / manual entry ───────────────────────────────────────────

    def _on_dept_selected(self, dept_id: str) -> None:
        # Numpad has price → dept click adds manual line in that dept.
        # Otherwise (numpad empty) selection is a no-op (browse hook for later).
        if dept_id == ALL_ID:
            return
        cents = self._numpad_cents()
        if cents <= 0:
            return
        d = DEPT_BY_ID.get(dept_id)
        if d is None:
            self._error(f"Unknown department: {dept_id}")
            return
        ln = self.cart.add_manual(name=d["label"], unit_price_cents=cents, department=dept_id, quantity=1)
        idx = self.cart.lines.index(ln)
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
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        rounded = self.cart.totals["rounded_total_cents"]
        tender = self._numpad_cents()
        if tender == 0:
            tender = rounded   # exact change shortcut
        if tender < rounded:
            short = rounded - tender
            self._error(f"Insufficient cash: short ${short / 100:.2f}")
            return
        change = tender - rounded
        self._finalize_cash(tender, change)

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
        try:
            tid = db.insert_transaction(txn.header_dict(), items_data)
        except Exception:
            log.exception("cash transaction insert failed")
            self._error("Failed to save transaction. See errors.log.")
            return
        # Lottery sale lines also written to lottery_ledger
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
        # Hardware stubs
        self._open_cash_drawer()
        self._print_receipt(txn, tid)
        self._play_success()
        self._show_change_dialog(ref, change_cents)
        # Reset register state
        self.cart.clear()
        self._numpad_clear()
        self.cart_widget.refresh()
        self._refresh_deals_banner()

    def _open_cash_drawer(self) -> None:
        log.info("[STUB] cash drawer kick signal")

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
        if self.cart.is_empty():
            self._info("Cart is empty.")
            return
        if self.terminal is None or not self.terminal.is_connected():
            self._error("Card terminal not connected (cash-only mode).")
            return
        if self._payment_thread is not None:
            self._info("Payment already in progress.")
            return

        amount = self.cart.totals["total_cents"]   # card = exact, no rounding
        if amount <= 0:
            self._error("Total is $0 — nothing to charge.")
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
        self._finalize_card_db(req, resp)
        if self._confirm("Print receipt?"):
            self._print_receipt_card_stub(req, resp)

    def _after_card_declined(self, resp: PaymentResponse) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None
        self._show_toast(f"Card Declined — {resp.error_message or 'try again'}", danger=True)

    def _on_sheet_cancel(self) -> None:
        # Cashier-initiated cancel BEFORE terminal responds.
        # Mock can't be aborted mid-sleep; just dismiss visually and let the
        # response (already pending) be discarded by lock_cancel state.
        log.info("card payment cancel requested by cashier")
        self._dismiss_sheet()
        self._pending_card_req = None
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
        self._info("Enter cash tender on the numpad, then press Cash.")

    def _dismiss_sheet(self) -> None:
        if self._card_sheet is not None:
            self._card_sheet.slide_out()

    def _dismiss_sheet_and_clear_req(self) -> None:
        self._dismiss_sheet()
        self._pending_card_req = None

    def _show_toast(self, message: str, *, danger: bool = False) -> None:
        toast = Toast(self, message, danger=danger)
        toast.show_for(2000)

    def _finalize_card_db(self, req: PaymentRequest, resp: PaymentResponse) -> None:
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
            rounded_total_cents=t["total_cents"],
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
        self._print_receipt(txn, tid)
        last4 = resp.card_last4 or "????"
        self._info(
            f"APPROVED\n\n{req.transaction_ref}\nAuth: {resp.auth_code}\nCard …{last4}"
        )
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
        cents = self._numpad_cents()
        if cents <= 0:
            self._info("Enter payout amount on numpad first.")
            return
        if not self._confirm(f"Lottery payout ${cents/100:.2f}?"):
            return
        try:
            db.log_lottery("payout", cents, self.cashier.name, shift_id=self.shift_id)
        except Exception:
            log.exception("lottery payout insert failed")
            self._error("Failed to log payout.")
            return
        self._open_cash_drawer()
        self._info(f"Payout of ${cents/100:.2f} dispensed.")
        self._numpad_clear()

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


# ─── Change dialog ───────────────────────────────────────────────────────────

class ChangeDialog(QDialog):
    """Large change-due display shown after a cash sale completes."""

    def __init__(self, txn_ref: str, change_cents: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("change_dialog")
        self.setWindowTitle("Change Due")
        self.setModal(True)
        self.setMinimumSize(360, 220)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(16)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ref_lbl = QLabel(f"Transaction {txn_ref}")
        ref_lbl.setObjectName("change_ref")
        ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ref_lbl.setFont(QFont(styles.FONT_FAMILY, 11))
        v.addWidget(ref_lbl)

        change_lbl = QLabel("Change Due")
        change_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        change_lbl.setFont(QFont(styles.FONT_FAMILY, 14))
        v.addWidget(change_lbl)

        amount = QLabel(f"${change_cents / 100:.2f}")
        amount.setObjectName("change_amount")
        amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        af = QFont(styles.FONT_FAMILY, 48)
        af.setBold(True)
        amount.setFont(af)
        amount.setStyleSheet(f"color: {styles.COLORS['btn_cash']};")
        v.addWidget(amount)

        ok = QPushButton("OK")
        ok.setObjectName("change_ok")
        ok.setMinimumHeight(48)
        of = QFont(styles.FONT_FAMILY, 14)
        of.setBold(True)
        ok.setFont(of)
        ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 24px; }}"
        )
        ok.clicked.connect(self.accept)
        v.addWidget(ok)
