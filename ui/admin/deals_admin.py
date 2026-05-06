"""Admin deals screen.

Create / edit / expire / test all 4 deal types per `.claude/features.md`:
  - bundle         : items A+B for fixed_price_cents
  - qty_discount   : N of item X for total_price_cents
  - cross_dept     : presence in dept A → discount_pct off all dept B lines
  - spend_discount : buy N of X → discount_cents off

DealEditDialog form morphs based on selected type. "Test Deal" simulates a
cart with the trigger items at minimum trigger qty and reports whether the
deal fires + the savings.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import db, deals as deals_engine
from core.cart import Cart
from core.departments import DEPARTMENTS
from core.logger import get_logger
from core.models import Deal, Item
from ui import styles

log = get_logger("ui.admin.deals")

DEAL_TYPES = ["qty_discount", "bundle", "spend_discount", "cross_dept"]
DEAL_TYPE_LABELS = {
    "qty_discount":   "Qty Discount  (N for $X)",
    "bundle":         "Bundle  (items together for $X)",
    "spend_discount": "Spend Discount  (buy N → $Y off)",
    "cross_dept":     "Cross Dept  (dept A → % off dept B)",
}

# Type-tile palette (matches reference: green/blue/purple/teal/orange).
PROMO_TYPE_TILES = [
    ("qty_discount",   "New Regular Promo",     "🏷",  "#27AE60"),
    ("spend_discount", "New BOGO / Spend Off",  "🎁",  "#7D3C98"),
    ("cross_dept",     "New Cross-Dept Promo",  "🛍",  "#2E86C1"),
    ("bundle",         "New Bundle Promo",      "📦",  "#E67E22"),
]


def _summarize_deal(d: dict) -> str:
    """Headline string for a promo card (e.g. '2 for $6.99'). Falls back to
    the deal type if shape is unknown.
    """
    try:
        from json import loads
        trig = loads(d.get("trigger_json") or "{}")
        rew = loads(d.get("reward_json") or "{}")
        t = d.get("deal_type")
        if t == "qty_discount":
            qty = int(trig.get("qty", 0))
            tot = int(rew.get("total_price_cents", 0))
            return f"{qty} for ${tot/100:.2f}" if qty and tot else "Qty Discount"
        if t == "bundle":
            price = int(rew.get("fixed_price_cents", 0))
            return f"Bundle for ${price/100:.2f}" if price else "Bundle"
        if t == "spend_discount":
            qty = int(trig.get("qty", 0))
            disc = int(rew.get("discount_cents", 0))
            return f"Buy {qty}, ${disc/100:.2f} off" if qty and disc else "Spend Discount"
        if t == "cross_dept":
            pct = int(rew.get("discount_pct", 0))
            return f"{pct}% off cross-dept" if pct else "Cross Dept"
    except Exception:
        pass
    return DEAL_TYPE_LABELS.get(d.get("deal_type", ""), "Promotion")


def _included_item_count(d: dict) -> int:
    """Count of items included in a promotion (read trigger payload)."""
    try:
        from json import loads
        trig = loads(d.get("trigger_json") or "{}")
        if "items" in trig and isinstance(trig["items"], list):
            return len(trig["items"])
        if trig.get("item_id"):
            return 1
    except Exception:
        pass
    return 0


def _status_for(d: dict, today: str) -> str:
    if not d.get("is_active"):
        return "inactive"
    if d.get("expiry_date") and d["expiry_date"] < today:
        return "expired"
    if d.get("start_date") and d["start_date"] > today:
        return "upcoming"
    return "active"


class DealsAdminScreen(QWidget):
    """Deals catalog management screen."""

    back_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("admin_inventory")  # reuse admin_screen_qss bg
        self._selected_id: Optional[int] = None
        self._cards: dict[int, QFrame] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.setStyleSheet(styles.admin_screen_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Promotions")
        title.setObjectName("screen_title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back to Home")
        back.setObjectName("deals_admin_back")
        back.setStyleSheet(styles.pill_button_qss("ghost"))
        back.setMinimumHeight(40)
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # ── Promo type tiles ──
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        for type_key, label, glyph, color in PROMO_TYPE_TILES:
            btn = QPushButton(f"{glyph}\n{label}")
            btn.setObjectName(f"promo_tile_{type_key}")
            btn.setStyleSheet(styles.promo_type_tile_qss(color))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _ck=False, t=type_key: self._on_add(default_type=t))
            tiles_row.addWidget(btn, stretch=1)
        root.addLayout(tiles_row)

        # ── Filter row ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setObjectName("deals_search")
        self._search.setProperty("touchKeyboard", "text")
        self._search.setPlaceholderText("Search promotions by name…")
        # Debounce filter to avoid rebuilding the card grid on every keystroke.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self.refresh)
        self._search.textChanged.connect(lambda _t: self._search_debounce.start())
        filter_row.addWidget(self._search, stretch=1)

        self._status_filter = QComboBox()
        self._status_filter.setObjectName("deals_status_filter")
        self._status_filter.setStyleSheet(styles.premium_combo_qss())
        self._status_filter.setMinimumHeight(42)
        self._status_filter.setMinimumWidth(150)
        self._status_filter.addItem("All", "")
        self._status_filter.addItem("Active", "active")
        self._status_filter.addItem("Upcoming", "upcoming")
        self._status_filter.addItem("Expired", "expired")
        self._status_filter.addItem("Inactive", "inactive")
        self._status_filter.setCurrentIndex(1)
        self._status_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        filter_row.addWidget(self._status_filter)

        for label, name, slot, variant in [
            ("+ Add Promotion", "deals_btn_add",    lambda: self._on_add(),  "success"),
            ("Edit",            "deals_btn_edit",   self._on_edit,           "primary"),
            ("Test",            "deals_btn_test",   self._on_test,           "ghost"),
            ("Expire Now",      "deals_btn_expire", self._on_expire,         "ghost"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(42)
            b.setStyleSheet(styles.pill_button_qss(variant))
            b.clicked.connect(slot)
            filter_row.addWidget(b)
        root.addLayout(filter_row)

        # ── Card grid (scrollable) ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(14)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._grid_host)
        root.addWidget(self._scroll, stretch=1)

    # ─── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        # Clear grid — detach + immediately hide so stale cards never paint.
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()

        rows = db.conn().execute(
            "SELECT * FROM deals ORDER BY is_active DESC, id DESC"
        ).fetchall()
        today = date.today().isoformat()

        q = (self._search.text() or "").strip().lower() if hasattr(self, "_search") else ""
        status_filter = self._status_filter.currentData() if hasattr(self, "_status_filter") else ""

        col_count = 3
        ri = ci = 0
        shown = 0
        seen_ids: set = set()
        for r in rows:
            d = dict(r)
            # Defensive dedupe: never render the same DB id twice in the grid.
            if d["id"] in seen_ids:
                continue
            seen_ids.add(d["id"])
            if q and q not in (d.get("name") or "").lower():
                continue
            status = _status_for(d, today)
            if status_filter and status != status_filter:
                continue
            card = self._build_card(d, status)
            self._cards[d["id"]] = card
            self._grid.addWidget(card, ri, ci)
            ci += 1
            if ci >= col_count:
                ci = 0
                ri += 1
            shown += 1

        if shown == 0:
            empty = QLabel("No promotions match the current filter.")
            empty.setStyleSheet("color: #8A8F95; font-size: 12pt; padding: 32px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(empty, 0, 0, 1, col_count)

    def _build_card(self, d: dict, status: str) -> QFrame:
        active = status in ("active", "upcoming")
        card = QFrame()
        card.setObjectName(f"deal_card_{d['id']}")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setMinimumHeight(150)
        card.setStyleSheet(styles.promo_card_qss(active=active, selected=False))

        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)

        # Top row: status badge + type label
        top = QHBoxLayout()
        type_lbl = QLabel(d.get("deal_type", "").replace("_", " ").title())
        type_lbl.setStyleSheet(f"color: {styles.COLORS['blue_mid']}; font-size: 9pt; font-weight: bold;")
        top.addWidget(type_lbl)
        top.addStretch(1)
        badge = QLabel(status.upper())
        badge.setStyleSheet(styles.status_badge_qss(status))
        top.addWidget(badge)
        v.addLayout(top)

        # Headline summary
        headline = QLabel(_summarize_deal(d))
        hf = QFont(styles.FONT_FAMILY, 16); hf.setBold(True)
        headline.setFont(hf)
        headline.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(headline)

        # Name
        name_lbl = QLabel(d.get("name", ""))
        name_lbl.setStyleSheet("color: #1A1A1A; font-size: 11pt;")
        name_lbl.setWordWrap(True)
        v.addWidget(name_lbl)

        v.addStretch(1)

        # Footer: dates + item count
        item_count = _included_item_count(d)
        meta_lbl = QLabel(
            f"From: {d.get('start_date', '—')}    To: {d.get('expiry_date', '—')}"
            f"    •    {item_count} item{'' if item_count == 1 else 's'}"
        )
        meta_lbl.setStyleSheet("color: #7F8C8D; font-size: 9pt;")
        v.addWidget(meta_lbl)

        # Click handlers (single = select, double = edit)
        deal_id = int(d["id"])
        card.mousePressEvent = lambda _ev, did=deal_id: self._select_card(did)
        card.mouseDoubleClickEvent = lambda _ev, did=deal_id: (
            self._select_card(did), self._on_edit()
        )
        return card

    def _select_card(self, deal_id: int) -> None:
        # Restyle previously selected, then select new.
        if self._selected_id is not None and self._selected_id in self._cards:
            today = date.today().isoformat()
            row = db.conn().execute(
                "SELECT * FROM deals WHERE id = ?", (self._selected_id,)
            ).fetchone()
            if row is not None:
                d_old = dict(row)
                status_old = _status_for(d_old, today)
                self._cards[self._selected_id].setStyleSheet(
                    styles.promo_card_qss(
                        active=status_old in ("active", "upcoming"),
                        selected=False,
                    )
                )
        self._selected_id = deal_id
        if deal_id in self._cards:
            today = date.today().isoformat()
            row = db.conn().execute(
                "SELECT * FROM deals WHERE id = ?", (deal_id,)
            ).fetchone()
            if row is not None:
                d_new = dict(row)
                status_new = _status_for(d_new, today)
                self._cards[deal_id].setStyleSheet(
                    styles.promo_card_qss(
                        active=status_new in ("active", "upcoming"),
                        selected=True,
                    )
                )

    def _selected_deal_id(self) -> Optional[int]:
        return self._selected_id

    # ─── Toolbar handlers ────────────────────────────────────────────────────

    def _on_add(self, default_type: Optional[str] = None) -> None:
        dlg = DealEditDialog(deal_id=None, parent=self)
        if default_type:
            idx = dlg._type_combo.findData(default_type)
            if idx >= 0:
                dlg._type_combo.setCurrentIndex(idx)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit(self) -> None:
        did = self._selected_deal_id()
        if did is None:
            self._info("Select a deal to edit.")
            return
        dlg = DealEditDialog(deal_id=did, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_expire(self) -> None:
        did = self._selected_deal_id()
        if did is None:
            self._info("Select a deal to expire.")
            return
        if not self._confirm("Set this deal's expiry to today (immediate end)?"):
            return
        try:
            today = date.today().isoformat()
            db.conn().execute(
                "UPDATE deals SET expiry_date = ?, is_active = 0 WHERE id = ?",
                (today, did),
            )
            db.conn().commit()
            self.refresh()
        except Exception:
            log.exception("expire failed")
            self._error("Could not expire deal.")

    def _on_test(self) -> None:
        did = self._selected_deal_id()
        if did is None:
            self._info("Select a deal to test.")
            return
        # Load deal
        row = db.conn().execute("SELECT * FROM deals WHERE id = ?", (did,)).fetchone()
        if row is None:
            self._error("Deal not found.")
            return
        from json import loads
        d = Deal.from_row({
            **dict(row),
            "trigger": loads(row["trigger_json"]),
            "reward":  loads(row["reward_json"]),
        })
        msg = self._simulate_deal(d)
        QMessageBox.information(self, "Deal Test", msg)

    def _simulate_deal(self, d: Deal) -> str:
        """Build a synthetic cart that satisfies the trigger; report fire + savings."""
        cart = Cart()
        try:
            if d.deal_type == "qty_discount" or d.deal_type == "spend_discount":
                target_id = d.trigger.get("item_id")
                qty = int(d.trigger.get("qty", 1))
                if not target_id:
                    return "Trigger missing item_id."
                item_row = db.get_item(target_id)
                if not item_row:
                    return f"Trigger item id={target_id} no longer exists."
                cart.add_item(Item.from_row(item_row), quantity=qty)

            elif d.deal_type == "bundle":
                items = d.trigger.get("items", [])
                for iid in items:
                    item_row = db.get_item(iid)
                    if not item_row:
                        return f"Trigger item id={iid} no longer exists."
                    cart.add_item(Item.from_row(item_row), quantity=1)

            elif d.deal_type == "cross_dept":
                trigger_dept = d.trigger.get("dept")
                target_dept = d.reward.get("target_dept")
                # Find one item in each dept
                trig_items = [r for r in db.list_all_items() if r["department"] == trigger_dept]
                tgt_items  = [r for r in db.list_all_items() if r["department"] == target_dept]
                if not trig_items:
                    return f"No active items in trigger dept '{trigger_dept}'."
                if not tgt_items:
                    return f"No active items in target dept '{target_dept}'."
                cart.add_item(Item.from_row(trig_items[0]))
                cart.add_item(Item.from_row(tgt_items[0]))

            else:
                return f"Unknown deal type: {d.deal_type}"

            # Apply ONLY this deal (isolate from other active deals)
            deals_engine.apply_deals(cart.lines, [d])
            cart._recalc_line_tax(); cart._sum_totals()
            disc = cart.totals["discount_cents"]
            if disc > 0:
                lines_summary = "\n".join(
                    f"  {ln.name} ×{ln.quantity}  ${ln.line_total_cents/100:.2f}"
                    + (f" (–${ln.deal_discount_cents/100:.2f})" if ln.deal_discount_cents else "")
                    for ln in cart.lines
                )
                return (
                    f"✓ DEAL FIRED\n\n"
                    f"Synthetic cart:\n{lines_summary}\n\n"
                    f"Total discount: ${disc/100:.2f}"
                )
            return "✗ Deal did NOT fire on synthetic cart."
        except Exception as exc:
            log.exception("deal simulation failed")
            return f"Simulation error: {exc}"

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "Deals", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Deals", msg)

    def _confirm(self, msg: str) -> bool:
        return QMessageBox.question(
            self, "Deals", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes


# ─── Deal edit dialog ────────────────────────────────────────────────────────

class DealEditDialog(QDialog):
    """Type-aware deal editor. Form morphs based on selected deal_type."""

    def __init__(self, *, deal_id: Optional[int], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("deal_edit_dialog")
        self.setWindowTitle("Add Promotion" if deal_id is None else "Edit Promotion")
        self.setModal(True)
        self.setMinimumSize(640, 620)
        self._deal_id = deal_id
        self._items_cache = db.list_all_items(active_only=True)

        # Per-type form widgets, lazily built
        self._qty_target: Optional[QComboBox] = None
        self._qty_qty: Optional[QSpinBox] = None
        self._qty_total_price: Optional[QLineEdit] = None
        self._bundle_list: Optional[QListWidget] = None
        self._bundle_picker: Optional[QComboBox] = None
        self._bundle_fixed: Optional[QLineEdit] = None
        self._spend_target: Optional[QComboBox] = None
        self._spend_qty: Optional[QSpinBox] = None
        self._spend_disc: Optional[QLineEdit] = None
        self._cross_trig_dept: Optional[QComboBox] = None
        self._cross_tgt_dept: Optional[QComboBox] = None
        self._cross_pct: Optional[QSpinBox] = None

        self._build()
        # Initial type form so widgets exist before load_existing populates them.
        self._rebuild_type_form()
        if deal_id is not None:
            self._load_existing()

    def _build(self) -> None:
        title_text = "Add Promotion" if self._deal_id is None else "Edit Promotion"
        self.setStyleSheet(
            styles.premium_dialog_qss() + styles.dialog_titlebar_qss()
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(0, 0, 0, 0)
        tlbl = QLabel(title_text)
        tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(tlbl)
        outer.addWidget(title_bar)

        # Wizard progress strip ("Add Detail" — "Add Items")
        prog = QFrame()
        ph = QHBoxLayout(prog)
        ph.setContentsMargins(40, 14, 40, 6)
        ph.setSpacing(0)
        self._prog_s1 = QLabel("● Add Detail")
        self._prog_bar = QFrame()
        self._prog_bar.setFixedHeight(2)
        self._prog_s2 = QLabel("○ Add Items")
        ph.addWidget(self._prog_s1)
        ph.addWidget(self._prog_bar, stretch=1)
        ph.addWidget(self._prog_s2)
        outer.addWidget(prog)

        # Body card
        wrap = QVBoxLayout()
        wrap.setContentsMargins(18, 8, 18, 8)
        wrap.setSpacing(0)
        body = QFrame()
        body.setObjectName("card")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(10)
        wrap.addWidget(body)
        outer.addLayout(wrap, stretch=1)

        # Stacked pages
        self._stack = QStackedWidget()
        bv.addWidget(self._stack, stretch=1)

        # ── Page 1: Promotion Details (scrollable) ──
        page1_scroll = QScrollArea()
        page1_scroll.setWidgetResizable(True)
        page1_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page1_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.setSpacing(10)

        common_form = QFormLayout()
        common_form.setSpacing(10)
        common_form.setHorizontalSpacing(16)

        self._name = QLineEdit()
        self._name.setObjectName("deal_name")
        self._name.setProperty("touchKeyboard", "text")
        common_form.addRow("Promo name:", self._name)

        self._type_combo = QComboBox()
        self._type_combo.setObjectName("deal_type_combo")
        self._type_combo.setStyleSheet(styles.premium_combo_qss())
        self._type_combo.setMinimumHeight(40)
        for t in DEAL_TYPES:
            self._type_combo.addItem(DEAL_TYPE_LABELS[t], t)
        self._type_combo.currentIndexChanged.connect(self._rebuild_type_form)
        common_form.addRow("Type:", self._type_combo)

        self._start = QDateEdit(QDate.currentDate())
        self._start.setObjectName("deal_start_date")
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        common_form.addRow("Valid from:", self._start)

        self._expiry = QDateEdit(QDate.currentDate().addDays(30))
        self._expiry.setObjectName("deal_expiry_date")
        self._expiry.setCalendarPopup(True)
        self._expiry.setDisplayFormat("yyyy-MM-dd")
        common_form.addRow("Valid to:", self._expiry)

        self._active = QCheckBox("Active")
        self._active.setObjectName("deal_active")
        self._active.setChecked(True)
        common_form.addRow("", self._active)

        p1.addLayout(common_form)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #E1E4EA; background: #E1E4EA;")
        sep.setFixedHeight(1)
        p1.addWidget(sep)

        type_lbl = QLabel("Promo-specific settings")
        tlf = QFont(styles.FONT_FAMILY, 11); tlf.setBold(True)
        type_lbl.setFont(tlf)
        type_lbl.setStyleSheet(f"color: {styles.COLORS['navy']}; padding-top: 4px;")
        p1.addWidget(type_lbl)

        # Step-1 type-specific holder (numeric params)
        self._step1_holder = QWidget()
        self._step1_form = QVBoxLayout(self._step1_holder)
        self._step1_form.setContentsMargins(0, 0, 0, 0)
        self._step1_form.setSpacing(8)
        p1.addWidget(self._step1_holder)
        p1.addStretch(1)

        page1_scroll.setWidget(page1)
        self._stack.addWidget(page1_scroll)

        # ── Page 2: Add Items ──
        page2 = QWidget()
        p2 = QVBoxLayout(page2)
        p2.setContentsMargins(0, 0, 0, 0)
        p2.setSpacing(8)

        self._step2_holder = QWidget()
        self._step2_form = QVBoxLayout(self._step2_holder)
        self._step2_form.setContentsMargins(0, 0, 0, 0)
        self._step2_form.setSpacing(8)
        p2.addWidget(self._step2_holder, stretch=1)

        self._stack.addWidget(page2)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("deal_cancel")
        cancel.setMinimumSize(120, 44)
        cancel.setStyleSheet(styles.pill_button_qss("ghost"))
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)

        self._btn_back = QPushButton("◄ Back — Add Detail")
        self._btn_back.setObjectName("deal_back")
        self._btn_back.setMinimumSize(180, 44)
        self._btn_back.setStyleSheet(styles.pill_button_qss("ghost"))
        self._btn_back.clicked.connect(lambda: self._goto_step(1))
        btn_row.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next — Add Items ▶")
        self._btn_next.setObjectName("deal_next")
        self._btn_next.setMinimumSize(200, 44)
        self._btn_next.setStyleSheet(styles.pill_button_qss("primary"))
        self._btn_next.clicked.connect(lambda: self._goto_step(2))
        btn_row.addWidget(self._btn_next)

        self._btn_finish = QPushButton("Finish")
        self._btn_finish.setObjectName("deal_save")
        self._btn_finish.setMinimumSize(160, 44)
        self._btn_finish.setDefault(True)
        self._btn_finish.setStyleSheet(styles.pill_button_qss("success"))
        self._btn_finish.clicked.connect(self._save)
        btn_row.addWidget(self._btn_finish)
        bv.addLayout(btn_row)

        # Initial: page 1, Back hidden, Next visible, Finish visible.
        self._goto_step(1)

    # ─── Wizard navigation ───────────────────────────────────────────────────

    def _goto_step(self, step: int) -> None:
        step = 1 if step < 1 else (2 if step > 2 else step)
        self._stack.setCurrentIndex(step - 1)
        # Update progress strip
        active = styles.COLORS["blue_mid"]
        muted = "#8A8F95"
        self._prog_s1.setStyleSheet(
            f"color: {active if step >= 1 else muted}; font-weight: bold; font-size: 11pt;"
        )
        self._prog_s1.setText("● Add Detail" if step == 1 else "✓ Add Detail")
        self._prog_bar.setStyleSheet(
            f"background: {active if step >= 2 else muted};"
        )
        self._prog_s2.setStyleSheet(
            f"color: {active if step >= 2 else muted}; font-weight: bold; font-size: 11pt;"
        )
        self._prog_s2.setText("● Add Items" if step == 2 else "○ Add Items")
        # Buttons
        self._btn_back.setVisible(step == 2)
        self._btn_next.setVisible(step == 1)
        # Finish stays visible on both pages — lets user save without items
        # (cross_dept has no items; existing edits may need a quick save)

    def _rebuild_type_form(self) -> None:
        # Clear step1 + step2 holders
        for layout in (self._step1_form, self._step2_form):
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()

        t = self._type_combo.currentData()
        if t == "qty_discount":
            self._build_qty_form()
        elif t == "bundle":
            self._build_bundle_form()
        elif t == "spend_discount":
            self._build_spend_form()
        elif t == "cross_dept":
            self._build_cross_form()

        # Cross-dept has no item picker; allow Finish from page 1 only.
        if t == "cross_dept":
            self._btn_next.setVisible(False)
        else:
            self._btn_next.setVisible(self._stack.currentIndex() == 0)

    # ─── Per-type forms ──────────────────────────────────────────────────────

    def _build_qty_form(self) -> None:
        # Step 1: numeric params
        f = QFormLayout(); f.setSpacing(10); f.setHorizontalSpacing(16)
        self._qty_qty = QSpinBox()
        self._qty_qty.setObjectName("qty_need_qty")
        self._qty_qty.setMinimum(1); self._qty_qty.setMaximum(99); self._qty_qty.setValue(2)
        f.addRow("Qty needed:", self._qty_qty)
        self._qty_total_price = QLineEdit()
        self._qty_total_price.setObjectName("qty_total_price")
        self._qty_total_price.setProperty("touchKeyboard", "num")
        self._qty_total_price.setPlaceholderText("Total price for that qty (e.g. 5.00)")
        f.addRow("Bundle price:", self._qty_total_price)
        self._step1_form.addLayout(f)
        hint = QLabel(
            "Pick every item that qualifies for this offer. The promo fires "
            "when the total quantity (any combination) reaches the threshold."
        )
        hint.setStyleSheet("color: #6B7787; font-size: 10pt;")
        hint.setWordWrap(True)
        self._step1_form.addWidget(hint)

        # Step 2: dual-panel item selector (multi-pick mode)
        self._qty_target = self._build_dual_panel(single_pick=False)

    def _build_spend_form(self) -> None:
        # Step 1: numeric params
        f = QFormLayout(); f.setSpacing(10); f.setHorizontalSpacing(16)
        self._spend_qty = QSpinBox()
        self._spend_qty.setObjectName("spend_need_qty")
        self._spend_qty.setMinimum(1); self._spend_qty.setMaximum(99); self._spend_qty.setValue(2)
        f.addRow("Qty needed:", self._spend_qty)
        self._spend_disc = QLineEdit()
        self._spend_disc.setObjectName("spend_discount")
        self._spend_disc.setProperty("touchKeyboard", "num")
        self._spend_disc.setPlaceholderText("Dollars off (e.g. 1.00)")
        f.addRow("Discount:", self._spend_disc)
        self._step1_form.addLayout(f)
        hint = QLabel(
            "Pick every qualifying item. Buying the threshold quantity in any "
            "combination triggers the discount."
        )
        hint.setStyleSheet("color: #6B7787; font-size: 10pt;")
        hint.setWordWrap(True)
        self._step1_form.addWidget(hint)

        # Step 2: dual-panel item selector (multi-pick mode)
        self._spend_target = self._build_dual_panel(single_pick=False)

    def _build_bundle_form(self) -> None:
        # Step 1: numeric params (fixed price)
        f = QFormLayout(); f.setSpacing(10); f.setHorizontalSpacing(16)
        self._bundle_fixed = QLineEdit()
        self._bundle_fixed.setObjectName("bundle_fixed_price")
        self._bundle_fixed.setProperty("touchKeyboard", "num")
        self._bundle_fixed.setPlaceholderText("e.g. 3.99")
        f.addRow("Fixed bundle price:", self._bundle_fixed)
        self._step1_form.addLayout(f)
        hint = QLabel("Bundle promo: cart must contain ONE of every item picked on next step, then bundle is sold for the fixed price above.")
        hint.setStyleSheet("color: #6B7787; font-size: 10pt;")
        hint.setWordWrap(True)
        self._step1_form.addWidget(hint)

        # Step 2: dual-panel item selector (multi-pick mode)
        self._bundle_panel = self._build_dual_panel(single_pick=False)
        # Back-compat alias used by save/load logic.
        self._bundle_list = self._bundle_panel["right_list"]

    def _build_cross_form(self) -> None:
        # Step 1: trigger dept + target dept + pct (no items)
        f = QFormLayout(); f.setSpacing(10); f.setHorizontalSpacing(16)
        self._cross_trig_dept = QComboBox()
        self._cross_trig_dept.setObjectName("cross_trig_dept")
        self._cross_trig_dept.setStyleSheet(styles.premium_combo_qss())
        self._cross_trig_dept.setMinimumHeight(40)
        for d in DEPARTMENTS:
            self._cross_trig_dept.addItem(d["label"], d["id"])
        f.addRow("Trigger dept:", self._cross_trig_dept)

        self._cross_tgt_dept = QComboBox()
        self._cross_tgt_dept.setObjectName("cross_tgt_dept")
        self._cross_tgt_dept.setStyleSheet(styles.premium_combo_qss())
        self._cross_tgt_dept.setMinimumHeight(40)
        for d in DEPARTMENTS:
            self._cross_tgt_dept.addItem(d["label"], d["id"])
        f.addRow("Target dept:", self._cross_tgt_dept)

        self._cross_pct = QSpinBox()
        self._cross_pct.setObjectName("cross_discount_pct")
        self._cross_pct.setMinimum(1); self._cross_pct.setMaximum(100); self._cross_pct.setValue(10)
        self._cross_pct.setSuffix(" %")
        f.addRow("Discount:", self._cross_pct)
        self._step1_form.addLayout(f)

        # Step 2: explanatory placeholder (no items needed)
        empty = QLabel("No items needed — Cross-Dept promos discount lines based on department membership.")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet("color: #8A8F95; font-size: 11pt; padding: 32px;")
        self._step2_form.addWidget(empty)

    # ─── Dual-panel item selector ────────────────────────────────────────────

    def _build_dual_panel(self, *, single_pick: bool) -> dict:
        """Build a left/right item picker into self._step2_form. Returns a
        dict of {"left_list", "right_list", "search", "dept_filter"}.

        single_pick=True  → only one item allowed on the right (qty/spend types).
        single_pick=False → any number of items on the right (bundle).
        """
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(8)

        # Helpful banner so cashier knows the promo only fires on the
        # exact SKUs added to the right panel — not the department label.
        banner = QLabel(
            "⚠  Add every SKU this promo should apply to. The promo will "
            "only fire on items in the right panel — picking just a department "
            "name is not enough."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background: #FEF7E0; color: #8A5A00; border: 1px solid #F1C40F;"
            " border-radius: 6px; padding: 8px 12px; font-size: 10pt;"
        )
        wv.addWidget(banner)

        # Filter row
        flt = QHBoxLayout()
        flt.setSpacing(8)
        search = QLineEdit()
        search.setObjectName("dp_search")
        search.setProperty("touchKeyboard", "text")
        search.setPlaceholderText("Search by name or UPC…")
        flt.addWidget(search, stretch=2)

        dept = QComboBox()
        dept.setObjectName("dp_dept_filter")
        dept.setStyleSheet(styles.premium_combo_qss())
        dept.setMinimumHeight(38)
        dept.addItem("All Departments", "")
        for d in DEPARTMENTS:
            dept.addItem(d["label"], d["id"])
        flt.addWidget(dept, stretch=1)
        wv.addLayout(flt)

        # Headers + lists row
        body = QHBoxLayout()
        body.setSpacing(10)

        # Left column
        left_col = QVBoxLayout(); left_col.setSpacing(4)
        left_lbl = QLabel("Available Items")
        left_lbl.setStyleSheet(f"color: {styles.COLORS['navy']}; font-weight: bold;")
        left_col.addWidget(left_lbl)
        left = QListWidget()
        left.setObjectName("dp_left_list")
        left.setAlternatingRowColors(True)
        left.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 8px; alternate-background-color: #F7F9FC; }"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #F0F2F5; }"
            f"QListWidget::item:selected {{ background: {styles.COLORS['blue_light']}; color: white; }}"
        )
        left.setUniformItemSizes(True)
        left.itemDoubleClicked.connect(lambda it: self._dp_add(it, single_pick))
        left_col.addWidget(left, stretch=1)
        body.addLayout(left_col, stretch=1)

        # Center buttons
        ctr = QVBoxLayout(); ctr.addStretch(1)
        add_btn = QPushButton("▶")
        add_btn.setObjectName("dp_add_btn")
        add_btn.setMinimumSize(48, 44)
        add_btn.setStyleSheet(styles.pill_button_qss("primary"))
        add_btn.clicked.connect(lambda: [self._dp_add(it, single_pick) for it in left.selectedItems()])
        ctr.addWidget(add_btn)
        rm_btn = QPushButton("◀")
        rm_btn.setObjectName("dp_remove_btn")
        rm_btn.setMinimumSize(48, 44)
        rm_btn.setStyleSheet(styles.pill_button_qss("ghost"))
        rm_btn.clicked.connect(self._dp_remove)
        ctr.addWidget(rm_btn)
        ctr.addStretch(1)
        body.addLayout(ctr)

        # Right column
        right_col = QVBoxLayout(); right_col.setSpacing(4)
        right_lbl = QLabel("Included in Promotion")
        right_lbl.setStyleSheet(f"color: {styles.COLORS['navy']}; font-weight: bold;")
        right_col.addWidget(right_lbl)
        right = QListWidget()
        right.setObjectName("dp_right_list")
        right.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 8px; }"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #F0F2F5; }"
            f"QListWidget::item:selected {{ background: {styles.COLORS['blue_light']}; color: white; }}"
        )
        right.setUniformItemSizes(True)
        right.itemDoubleClicked.connect(lambda _it: self._dp_remove())
        right_col.addWidget(right, stretch=1)
        body.addLayout(right_col, stretch=1)

        wv.addLayout(body, stretch=1)
        self._step2_form.addWidget(wrap)

        # Stash refs + initial population
        ref = {
            "left_list": left, "right_list": right,
            "search": search, "dept_filter": dept,
        }
        self._dp = ref

        # Debounced search (200ms)
        self._dp_debounce = QTimer(self)
        self._dp_debounce.setSingleShot(True)
        self._dp_debounce.setInterval(200)
        self._dp_debounce.timeout.connect(self._dp_filter_left)
        search.textChanged.connect(lambda _t: self._dp_debounce.start())
        dept.currentIndexChanged.connect(lambda _i: self._dp_filter_left())

        self._dp_filter_left()
        return ref

    def _dp_filter_left(self) -> None:
        """Repopulate left list from cache, applying search + dept filters."""
        if not hasattr(self, "_dp"):
            return
        q = (self._dp["search"].text() or "").strip().lower()
        dept_id = self._dp["dept_filter"].currentData() or ""
        # Items already on right shouldn't appear on left (avoid dupes).
        on_right = {self._dp["right_list"].item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self._dp["right_list"].count())}
        left = self._dp["left_list"]
        left.clear()
        for r in self._items_cache:
            if r["id"] in on_right:
                continue
            if dept_id and r.get("department") != dept_id:
                continue
            if q:
                hay = f"{r.get('name','')} {r.get('barcode','')}".lower()
                if q not in hay:
                    continue
            li = QListWidgetItem(self._dp_row_text(r))
            li.setData(Qt.ItemDataRole.UserRole, r["id"])
            left.addItem(li)

    @staticmethod
    def _dp_row_text(r: dict) -> str:
        upc = (r.get("barcode") or "").ljust(14)[:14]
        name = (r.get("name") or "").ljust(28)[:28]
        dept = (r.get("department") or "").ljust(10)[:10]
        price = f"${r.get('price_cents', 0)/100:.2f}"
        return f"{upc}  {name}  {dept}  {price}"

    def _dp_add(self, it: QListWidgetItem, single_pick: bool) -> None:
        if not hasattr(self, "_dp") or it is None:
            return
        item_id = it.data(Qt.ItemDataRole.UserRole)
        right = self._dp["right_list"]
        if single_pick:
            right.clear()
        else:
            for i in range(right.count()):
                if right.item(i).data(Qt.ItemDataRole.UserRole) == item_id:
                    return
        new = QListWidgetItem(it.text())
        new.setData(Qt.ItemDataRole.UserRole, item_id)
        right.addItem(new)
        self._dp_filter_left()

    def _dp_remove(self) -> None:
        if not hasattr(self, "_dp"):
            return
        right = self._dp["right_list"]
        for it in right.selectedItems():
            right.takeItem(right.row(it))
        self._dp_filter_left()

    def _dp_get_right_ids(self) -> list[int]:
        if not hasattr(self, "_dp"):
            return []
        right = self._dp["right_list"]
        return [right.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(right.count())]

    def _dp_set_right_ids(self, ids: list[int]) -> None:
        if not hasattr(self, "_dp"):
            return
        right = self._dp["right_list"]
        right.clear()
        items_by_id = {r["id"]: r for r in self._items_cache}
        for iid in ids:
            r = items_by_id.get(iid) or db.get_item(iid)
            if r is None:
                continue
            li = QListWidgetItem(self._dp_row_text(r))
            li.setData(Qt.ItemDataRole.UserRole, iid)
            right.addItem(li)
        self._dp_filter_left()

    # Back-compat shims so existing _save/_load logic keeps working.
    def _on_bundle_add(self) -> None:
        if hasattr(self, "_dp") and self._dp["left_list"].selectedItems():
            self._dp_add(self._dp["left_list"].currentItem(), single_pick=False)

    def _on_bundle_remove(self) -> None:
        self._dp_remove()

    # ─── Load existing for edit ──────────────────────────────────────────────

    def _load_existing(self) -> None:
        from json import loads
        row = db.conn().execute("SELECT * FROM deals WHERE id = ?", (self._deal_id,)).fetchone()
        if row is None:
            self.reject(); return
        d = dict(row)
        self._name.setText(d["name"])
        idx = self._type_combo.findData(d["deal_type"])
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._start.setDate(QDate.fromString(d["start_date"], "yyyy-MM-dd"))
        self._expiry.setDate(QDate.fromString(d["expiry_date"], "yyyy-MM-dd"))
        self._active.setChecked(bool(d["is_active"]))
        # Type form is rebuilt by index change above
        # Stash decoded payload for _post_load_apply (called after rebuild)
        self._loaded_trigger = loads(d["trigger_json"])
        self._loaded_reward = loads(d["reward_json"])
        # Apply now (rebuild was already triggered by setCurrentIndex)
        self._apply_loaded_payload()

    def _apply_loaded_payload(self) -> None:
        t = self._type_combo.currentData()
        trig, rew = self._loaded_trigger, self._loaded_reward
        try:
            if t == "qty_discount":
                ids = list(trig.get("items") or [])
                if not ids and trig.get("item_id") is not None:
                    ids = [trig["item_id"]]
                if ids:
                    self._dp_set_right_ids(ids)
                self._qty_qty.setValue(int(trig.get("qty", 1)))
                self._qty_total_price.setText(f"{rew.get('total_price_cents', 0) / 100:.2f}")
            elif t == "bundle":
                self._dp_set_right_ids(list(trig.get("items", [])))
                self._bundle_fixed.setText(f"{rew.get('fixed_price_cents', 0) / 100:.2f}")
            elif t == "spend_discount":
                ids = list(trig.get("items") or [])
                if not ids and trig.get("item_id") is not None:
                    ids = [trig["item_id"]]
                if ids:
                    self._dp_set_right_ids(ids)
                self._spend_qty.setValue(int(trig.get("qty", 1)))
                self._spend_disc.setText(f"{rew.get('discount_cents', 0) / 100:.2f}")
            elif t == "cross_dept" and self._cross_trig_dept is not None:
                idx = self._cross_trig_dept.findData(trig.get("dept"))
                if idx >= 0: self._cross_trig_dept.setCurrentIndex(idx)
                idx = self._cross_tgt_dept.findData(rew.get("target_dept"))
                if idx >= 0: self._cross_tgt_dept.setCurrentIndex(idx)
                self._cross_pct.setValue(int(rew.get("discount_pct", 10)))
        except Exception:
            log.exception("apply_loaded_payload failed")

    # ─── Save ────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Deal", "Name is required."); return

        t = self._type_combo.currentData()
        start = self._start.date().toString("yyyy-MM-dd")
        expiry = self._expiry.date().toString("yyyy-MM-dd")
        if expiry < start:
            QMessageBox.warning(self, "Deal", "Expiry date must be on/after start date."); return

        try:
            trigger, reward = self._collect_trigger_reward(t)
        except ValueError as e:
            QMessageBox.warning(self, "Deal", str(e)); return

        try:
            if self._deal_id is None:
                new_id = db.create_deal(name, t, trigger, reward, start, expiry)
                if not self._active.isChecked():
                    db.expire_deal(new_id)
            else:
                # Direct UPDATE since core/db.py doesn't expose update_deal
                from json import dumps
                db.conn().execute(
                    """UPDATE deals SET name=?, deal_type=?, trigger_json=?, reward_json=?,
                              start_date=?, expiry_date=?, is_active=? WHERE id=?""",
                    (name, t, dumps(trigger), dumps(reward),
                     start, expiry, 1 if self._active.isChecked() else 0,
                     self._deal_id),
                )
                db.conn().commit()
            self.accept()
        except Exception as exc:
            log.exception("save deal failed")
            QMessageBox.warning(self, "Deal", f"Save failed: {exc}")

    def _collect_trigger_reward(self, t: str) -> tuple[dict, dict]:
        if t == "qty_discount":
            ids = self._dp_get_right_ids()
            if not ids:
                raise ValueError("Pick at least one item on the 'Add Items' step.")
            qty = self._qty_qty.value()
            total = self._dollars_to_cents(self._qty_total_price.text(), "Bundle price")
            return {"items": ids, "qty": qty}, {"total_price_cents": total}

        if t == "bundle":
            ids = self._dp_get_right_ids()
            if len(ids) < 2:
                raise ValueError("Bundle needs at least 2 items.")
            fixed = self._dollars_to_cents(self._bundle_fixed.text(), "Bundle price")
            return {"items": ids}, {"fixed_price_cents": fixed}

        if t == "spend_discount":
            ids = self._dp_get_right_ids()
            if not ids:
                raise ValueError("Pick at least one item on the 'Add Items' step.")
            qty = self._spend_qty.value()
            disc = self._dollars_to_cents(self._spend_disc.text(), "Discount")
            return {"items": ids, "qty": qty}, {"discount_cents": disc}

        if t == "cross_dept":
            tdept = self._cross_trig_dept.currentData()
            tgtdept = self._cross_tgt_dept.currentData()
            if tdept == tgtdept:
                raise ValueError("Trigger and target dept must differ.")
            pct = self._cross_pct.value()
            return {"dept": tdept}, {"target_dept": tgtdept, "discount_pct": pct}

        raise ValueError(f"Unknown deal type: {t}")

    @staticmethod
    def _dollars_to_cents(text: str, label: str) -> int:
        try:
            v = float(text.strip())
            if v < 0:
                raise ValueError
            return int(round(v * 100))
        except (ValueError, TypeError):
            raise ValueError(f"{label} must be a positive number (e.g. 5.00).")

    def closeEvent(self, ev) -> None:
        # Stop debounce timer + close on-screen keyboard so neither outlives
        # the dialog. Defensive — Qt also tears down child timers automatically.
        try:
            if hasattr(self, "_dp_debounce"):
                self._dp_debounce.stop()
        except Exception:
            pass
        try:
            from ui.cashier.touch_keyboard import close_active_keyboard
            close_active_keyboard()
        except Exception:
            pass
        super().closeEvent(ev)
