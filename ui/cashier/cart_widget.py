"""Cart display + per-row inline controls + totals panel.

Wraps a live `Cart` object — does not copy. Mutations call back into the cart
(which calls `recompute()` internally) and then `refresh()` rebuilds the view.

Layout:
  ┌── Big TOTAL header (white bg, 24pt navy amount) ──┐
  ├── Cart list (#EEF2FF bg, per-row tint by kind) ───┤
  │   selected row shows inline [−][N][+][🗑] controls│
  ├── Totals breakdown panel (Subtotal, GST, PST, …) ─┤
  └────────────────────────────────────────────────────┘

Signals:
  item_selected(object)  : CartItem or None
  qty_changed(int)        : new qty after row +/- press
  item_removed()          : after row 🗑 press
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import db

from core.cart import Cart
from core.logger import get_logger
from core.models import CartItem
from ui import styles

log = get_logger("ui.cart")

CART_BG = "#DCE6F4"            # light steel-blue container, dark text rides on top
ROW_BG_DEFAULT = "transparent"
ROW_BG_DEAL = styles.COLORS["deal_highlight"]   # yellow
ROW_BG_LOTTERY = "#F4ECF7"     # light purple
ROW_BG_BAG = "#F5F5F5"         # near-white grey
ROW_BG_FLASH = "transparent"   # animation removed per UX request
FLASH_MS = 0


# ─── Big TOTAL header ────────────────────────────────────────────────────────

class CartTotalHeader(QWidget):
    """Prominent TOTAL bar above the cart list."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cart_total_header")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(72)
        self.setStyleSheet(
            f"QWidget#cart_total_header {{ background-color: {styles.COLORS['navy']};"
            f" border-bottom: 2px solid {styles.COLORS['cart_dark']}; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 14, 6)
        h.setSpacing(10)

        lbl = QLabel("TOTAL")
        lbl.setObjectName("cart_total_label")
        lf = QFont(styles.FONT_FAMILY, 16); lf.setBold(True)
        lbl.setFont(lf)
        lbl.setStyleSheet(f"color: {styles.COLORS['white']}; background: transparent; letter-spacing: 2px;")
        h.addWidget(lbl)

        self.amount = QLabel("$0.00")
        self.amount.setObjectName("cart_total_amount")
        self.amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        af = QFont(styles.FONT_FAMILY, 30); af.setBold(True)
        self.amount.setFont(af)
        self.amount.setStyleSheet(f"color: {styles.COLORS['white']}; background: transparent;")
        h.addWidget(self.amount, stretch=1)

    def update_amount(self, total_cents: int) -> None:
        self.amount.setText(f"${total_cents / 100:.2f}")


# ─── Per-row widget ──────────────────────────────────────────────────────────

class CartRow(QWidget):
    """One cart line. Selected → static qty/unit/total swapped for inline controls."""

    qty_minus = pyqtSignal(int)   # line_index
    qty_plus = pyqtSignal(int)
    remove_clicked = pyqtSignal(int)
    clicked = pyqtSignal(int)     # line_index — for select/deselect toggling

    def __init__(self, line: CartItem, line_index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.line_index = line_index
        self.line = line
        self.setObjectName(f"cart_row_{line_index}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._selected: bool = False
        self._build(line)
        self._apply_tint(line)

    def mousePressEvent(self, ev) -> None:
        # Forward row-body presses up as a clicked signal. Child buttons
        # (+/- /trash) intercept their own clicks before this fires.
        try:
            self.clicked.emit(self.line_index)
        except Exception:
            pass
        super().mousePressEvent(ev)

    def _build(self, line: CartItem) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 2, 10, 2)
        h.setSpacing(8)
        self.setFixedHeight(32)

        # Name (always visible, stretches) — color set by _apply_tint via parent QSS
        self._name_lbl = QLabel(line.name)
        self._name_lbl.setObjectName("cart_row_name")
        nf = QFont(styles.FONT_FAMILY, 13); nf.setBold(True)
        self._name_lbl.setFont(nf)
        h.addWidget(self._name_lbl, stretch=1)

        # Static cluster: qty + unit + (discount) + total
        self._static = QWidget()
        self._static.setObjectName("cart_row_static")
        sh = QHBoxLayout(self._static)
        sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(8)

        # qty — color set by _apply_tint via parent QSS
        self._qty_lbl = QLabel(f"{line.quantity}x")
        self._qty_lbl.setObjectName("cart_row_qty")
        self._qty_lbl.setMinimumWidth(36)
        self._qty_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        qf = QFont(styles.FONT_FAMILY, 12); qf.setBold(True)
        self._qty_lbl.setFont(qf)
        sh.addWidget(self._qty_lbl)

        # unit price — color set by _apply_tint via parent QSS
        self._unit_lbl = QLabel(f"${line.unit_price_cents / 100:.2f}")
        self._unit_lbl.setObjectName("cart_row_unit")
        self._unit_lbl.setMinimumWidth(64)
        self._unit_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._unit_lbl.setFont(QFont(styles.FONT_FAMILY, 12))
        sh.addWidget(self._unit_lbl)

        if line.deal_discount_cents > 0:
            disc = QLabel(f"PROMO  −${line.deal_discount_cents / 100:.2f}")
            disc.setObjectName("cart_row_discount")
            disc.setMinimumWidth(120)
            disc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            disc.setStyleSheet(
                f"color: white; font-weight: bold; font-size: 10pt;"
                f" background-color: {styles.COLORS['btn_cash']};"
                f" border-radius: 9px; padding: 2px 10px;"
            )
            sh.addWidget(disc)

        # line total — color set by _apply_tint via parent QSS
        self._total_lbl = QLabel(f"${line.line_total_cents / 100:.2f}")
        self._total_lbl.setObjectName("cart_row_total")
        self._total_lbl.setMinimumWidth(80)
        self._total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tf = QFont(styles.FONT_FAMILY, 12); tf.setBold(True)
        self._total_lbl.setFont(tf)
        sh.addWidget(self._total_lbl)

        h.addWidget(self._static)

        # Inline controls (hidden until row selected)
        self._controls = QWidget()
        self._controls.setObjectName("cart_row_controls")
        ch = QHBoxLayout(self._controls)
        ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(4)

        self._btn_minus = self._mk_ctrl("−", f"row{self.line_index}_btn_minus", color="#E74C3C")
        self._btn_minus.clicked.connect(lambda _ck=False: self.qty_minus.emit(self.line_index))
        ch.addWidget(self._btn_minus)

        self._qty_in_ctrl = QLabel(str(line.quantity))
        self._qty_in_ctrl.setObjectName(f"row{self.line_index}_qty_inline")
        self._qty_in_ctrl.setMinimumWidth(40)
        self._qty_in_ctrl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cf = QFont(styles.FONT_FAMILY, 14); cf.setBold(True)
        self._qty_in_ctrl.setFont(cf)
        self._qty_in_ctrl.setStyleSheet("background: transparent;")
        ch.addWidget(self._qty_in_ctrl)

        self._btn_plus = self._mk_ctrl("+", f"row{self.line_index}_btn_plus", color="#27AE60")
        self._btn_plus.clicked.connect(lambda _ck=False: self.qty_plus.emit(self.line_index))
        ch.addWidget(self._btn_plus)

        # Inline line-total readout so cashier sees total update on +/−.
        self._inline_total = QLabel(f"${line.line_total_cents/100:.2f}")
        self._inline_total.setObjectName(f"row{self.line_index}_inline_total")
        self._inline_total.setMinimumWidth(72)
        self._inline_total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        itf = QFont(styles.FONT_FAMILY, 13); itf.setBold(True)
        self._inline_total.setFont(itf)
        ch.addWidget(self._inline_total)

        # Trash button — dark slate bg so emoji icon is clearly visible.
        self._btn_remove = QPushButton("🗑")
        self._btn_remove.setObjectName(f"row{self.line_index}_btn_remove")
        self._btn_remove.setMinimumSize(36, 28)
        self._btn_remove.setMaximumHeight(28)
        rf = QFont(styles.FONT_FAMILY, 14); rf.setBold(True)
        self._btn_remove.setFont(rf)
        self._btn_remove.setStyleSheet(
            "QPushButton { background-color: #2F3E52; color: #FFFFFF;"
            " border: none; border-radius: 4px; padding: 2px 10px; }"
            "QPushButton:hover { background-color: #1B3A6B; }"
            "QPushButton:pressed { padding: 4px 10px 0px 12px; }"
        )
        self._btn_remove.clicked.connect(lambda _ck=False: self.remove_clicked.emit(self.line_index))
        ch.addWidget(self._btn_remove)

        self._controls.hide()
        h.addWidget(self._controls)

    def _mk_ctrl(self, text: str, name: str, *, color: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumSize(36, 28)
        b.setMaximumHeight(28)
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        b.setFont(f)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: none; border-radius: 4px; padding: 2px 10px; }}"
            f"QPushButton:pressed {{ padding: 4px 10px 0px 12px; }}"
        )
        return b

    # Cache of generated QSS strings keyed by (bg, name, qty, unit, total, italic).
    # Avoids re-doing f-string + Qt's QSS parser on every refresh under rapid input.
    _QSS_CACHE: dict = {}

    @classmethod
    def _row_qss(cls, bg: str, name_col: str, qty_col: str, unit_col: str,
                 total_col: str, *, italic: bool = False) -> str:
        """Build (or fetch cached) CartRow stylesheet — paints bg + child label colors.

        Per-label QSS via objectName selectors so we don't need direct
        stylesheets on each QLabel (which would override parent rules).
        """
        key = (bg, name_col, qty_col, unit_col, total_col, italic)
        cached = cls._QSS_CACHE.get(key)
        if cached is not None:
            return cached
        italic_css = "font-style: italic;" if italic else ""
        qss = (
            f"CartRow {{ background-color: {bg}; {italic_css} }}"
            f"CartRow QLabel {{ background: transparent; }}"
            f"CartRow QLabel#cart_row_name  {{ color: {name_col}; }}"
            f"CartRow QLabel#cart_row_qty   {{ color: {qty_col}; }}"
            f"CartRow QLabel#cart_row_unit  {{ color: {unit_col}; }}"
            f"CartRow QLabel#cart_row_total {{ color: {total_col}; }}"
        )
        cls._QSS_CACHE[key] = qss
        return qss

    @staticmethod
    def _lighten(hex_color: str, ratio: float = 0.82) -> str:
        """Mix hex toward white. ratio=0 → white, 1 → original."""
        try:
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            r = int(r + (255 - r) * (1 - ratio))
            g = int(g + (255 - g) * (1 - ratio))
            b = int(b + (255 - b) * (1 - ratio))
            return f"#{r:02X}{g:02X}{b:02X}"
        except Exception:
            return "#F0F4FA"

    def _apply_tint(self, line: CartItem) -> None:
        if self._selected:
            return
        navy = styles.COLORS["navy"]
        blue_mid = styles.COLORS["blue_mid"]
        # Only 3 row states: normal (light blue), payout/refund (soft red),
        # bag (faded grey-italic for clarity). Dept-tinting removed.
        if line.kind == "lottery" and line.unit_price_cents < 0:
            qss = self._row_qss("#FCE8E6", "#C0392B", "#C0392B", "#C0392B", "#C0392B")
        elif line.kind == "bag":
            muted = styles.COLORS["text_muted"]
            qss = self._row_qss("#F5F5F5", muted, muted, muted, muted, italic=True)
        else:
            qss = self._row_qss("transparent", navy, blue_mid, "#5C6B7F", navy)
        self.setStyleSheet(qss)

    # ─── Public API for CartWidget ───────────────────────────────────────────

    def set_selected(self, selected: bool) -> None:
        """Toggle inline controls. Static cluster hides while selected."""
        self._selected = selected
        if selected:
            self._static.hide()
            self._qty_in_ctrl.setText(str(self.line.quantity))
            self._controls.show()
            # Soft green highlight for selection (touch-friendly).
            self.setStyleSheet(self._row_qss(
                "#D1F2D5", "#1B3A6B", "#1B3A6B", "#5C6B7F", "#1B3A6B"
            ))
        else:
            self._controls.hide()
            self._static.show()
            self._apply_tint(self.line)

    def flash_added(self) -> None:
        """No-op (animation removed per UX request). Kept for API compat."""
        return

    def _restore_tint_safe(self) -> None:
        try:
            if not self._selected:
                self._apply_tint(self.line)
        except RuntimeError:
            pass   # underlying C++ row already deleted; ignore


# ─── Totals breakdown panel ──────────────────────────────────────────────────

class TotalsPanel(QWidget):
    """Subtotal/Discount/GST/PST/Deposit/Bag/TOTAL/(Cash rounded)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("totals_panel")
        self._build()
        self.update_totals({
            "subtotal_cents": 0, "discount_cents": 0,
            "gst_cents": 0, "pst_cents": 0, "deposit_cents": 0,
            "bag_charge_cents": 0,
            "total_cents": 0, "rounded_total_cents": 0,
        })

    def _build(self) -> None:
        # Outer V box: detail rows → divider → SUBTOTAL band → TOTAL band.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── detail rows ──
        details = QFrame()
        details.setObjectName("totals_details")
        details.setStyleSheet("QFrame#totals_details { background: white; }")
        grid = QGridLayout(details)
        grid.setContentsMargins(14, 4, 14, 4)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(2)

        row_label_qss = "color: #2F3E52; background: transparent;"
        row_value_qss = "color: #1B1B1B; background: transparent;"

        def add_row(row: int, label_text: str, value_name: str) -> QLabel:
            lab = QLabel(label_text)
            lf = QFont(styles.FONT_FAMILY, 11); lab.setFont(lf)
            lab.setStyleSheet(row_label_qss)
            val = QLabel("$0.00"); val.setObjectName(value_name)
            vf = QFont(styles.FONT_FAMILY, 12); val.setFont(vf)
            val.setStyleSheet(row_value_qss)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lab, row, 0)
            grid.addWidget(val, row, 1)
            return val

        self._lbl_discount = add_row(0, "Discount:", "totals_discount")
        self._lbl_gst      = add_row(1, "GST (5%):", "totals_gst")
        self._lbl_pst      = add_row(2, "PST (7%):", "totals_pst")
        self._lbl_deposit  = add_row(3, "Deposit:",  "totals_deposit")
        self._lbl_bag      = add_row(4, "Bag:",      "totals_bag")
        outer.addWidget(details)

        # divider above SUBTOTAL
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background-color: #C8D0E0;")
        outer.addWidget(rule)

        # ── SUBTOTAL band (highlighted) ──
        sub_band = QFrame()
        sub_band.setObjectName("totals_subtotal_band")
        sub_band.setStyleSheet(
            "QFrame#totals_subtotal_band { background-color: #E8EEF7; }"
        )
        sb = QHBoxLayout(sub_band)
        sb.setContentsMargins(14, 4, 14, 4); sb.setSpacing(20)
        sub_lbl = QLabel("SUBTOTAL")
        slf = QFont(styles.FONT_FAMILY, 14); slf.setBold(True)
        sub_lbl.setFont(slf)
        sub_lbl.setStyleSheet("color: #1B3A6B; background: transparent;")
        self._lbl_subtotal = QLabel("$0.00")
        self._lbl_subtotal.setObjectName("totals_subtotal")
        sav = QFont(styles.FONT_FAMILY, 17); sav.setBold(True)
        self._lbl_subtotal.setFont(sav)
        self._lbl_subtotal.setStyleSheet("color: #1B3A6B; background: transparent;")
        self._lbl_subtotal.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sb.addWidget(sub_lbl); sb.addStretch(1); sb.addWidget(self._lbl_subtotal)
        outer.addWidget(sub_band)

        # ── TOTAL band (navy bg, RED amount for max glance visibility) ──
        tot_band = QFrame()
        tot_band.setObjectName("totals_total_band")
        tot_band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tot_band.setStyleSheet(
            "QFrame#totals_total_band { background-color: #1B3A6B; }"
            "QFrame#totals_total_band QLabel { color: #FFFFFF;"
            " background: transparent; }"
            "QFrame#totals_total_band QLabel#totals_total {"
            " color: #FF6B6B; }"
        )
        tb = QHBoxLayout(tot_band)
        tb.setContentsMargins(14, 6, 14, 6); tb.setSpacing(20)
        tot_lbl = QLabel("TOTAL")
        tlf = QFont(styles.FONT_FAMILY, 15); tlf.setBold(True)
        tot_lbl.setFont(tlf)
        self._lbl_total = QLabel("$0.00")
        self._lbl_total.setObjectName("totals_total")
        tav = QFont(styles.FONT_FAMILY, 26); tav.setBold(True)
        self._lbl_total.setFont(tav)
        self._lbl_total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tb.addWidget(tot_lbl); tb.addStretch(1); tb.addWidget(self._lbl_total)
        outer.addWidget(tot_band)

        # ── Cash-rounded preview (hidden when equal) ──
        self._lbl_cash_preview = QLabel("")
        self._lbl_cash_preview.setObjectName("totals_cash_preview")
        self._lbl_cash_preview.setFont(QFont(styles.FONT_FAMILY, 10))
        self._lbl_cash_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_cash_preview.setStyleSheet(
            f"color: {styles.COLORS['text_muted']};"
            f" background: white; padding: 2px 14px;"
        )
        outer.addWidget(self._lbl_cash_preview)

    def set_partial_paid(self, paid_cents: int) -> None:
        """Show 'Paid: $X / Remaining: $Y' below TOTAL when split cash in
        progress. paid_cents=0 → revert to plain TOTAL display.
        """
        self._partial_paid_cents = max(0, int(paid_cents))
        self._render_total_text()

    def _render_total_text(self) -> None:
        # Internal helper — refresh TOTAL/remaining label.
        total = getattr(self, "_last_total_cents", 0)
        partial = getattr(self, "_partial_paid_cents", 0)
        if partial > 0 and total > 0:
            remaining = max(0, total - partial)
            self._lbl_total.setText(
                f"${remaining / 100:.2f}  ◂ remaining"
            )
        else:
            self._lbl_total.setText(f"${total / 100:.2f}")

    def update_totals(self, totals: dict) -> None:
        def fmt(c: int) -> str: return f"${c / 100:.2f}"
        self._last_total_cents = int(totals.get("total_cents", 0))
        self._lbl_subtotal.setText(fmt(totals["subtotal_cents"]))
        d = totals["discount_cents"]
        self._lbl_discount.setText(f"-{fmt(d)}" if d else "$0.00")
        self._lbl_gst.setText(fmt(totals["gst_cents"]))
        self._lbl_pst.setText(fmt(totals["pst_cents"]))
        self._lbl_deposit.setText(fmt(totals["deposit_cents"]))
        self._lbl_bag.setText(fmt(totals["bag_charge_cents"]))
        self._render_total_text()  # honors partial-paid remaining display
        if totals["rounded_total_cents"] != totals["total_cents"]:
            self._lbl_cash_preview.setText(
                f"(Cash → {fmt(totals['rounded_total_cents'])})"
            )
            self._lbl_cash_preview.show()
        else:
            self._lbl_cash_preview.setText("")
            self._lbl_cash_preview.hide()


# ─── Main widget ─────────────────────────────────────────────────────────────

class CartWidget(QWidget):
    """Live cart view: one bordered box containing TOTAL header → list →
    breakdown → Hold|Cancel."""

    item_selected = pyqtSignal(object)   # CartItem | None
    qty_changed = pyqtSignal(int)        # new qty
    item_removed = pyqtSignal()
    hold_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    print_receipt_clicked = pyqtSignal()
    restore_held_requested = pyqtSignal(int)   # held_id

    def __init__(self, cart: Cart, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cart_widget")
        self.cart = cart
        self._selected_index: Optional[int] = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # ── HELD pill (above the box, top-right; hidden when count == 0) ──
        held_row = QFrame()
        held_row.setObjectName("held_row")
        held_row.setStyleSheet("QFrame#held_row { background: transparent; }")
        held_row.setFixedHeight(28)
        hr = QHBoxLayout(held_row)
        hr.setContentsMargins(0, 0, 0, 0); hr.setSpacing(0)
        hr.addStretch(1)
        self._held_pill = QPushButton("🔖 HOLD (0)")
        self._held_pill.setObjectName("held_pill")
        self._held_pill.setMinimumHeight(28)
        self._held_pill.setMaximumHeight(28)
        pf = QFont(styles.FONT_FAMILY, 12); pf.setBold(True)
        self._held_pill.setFont(pf)
        self._held_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._held_pill.setStyleSheet(
            f"QPushButton#held_pill {{ background-color: {styles.COLORS['navy']};"
            f" color: white; border: none; border-radius: 12px;"
            f" padding: 2px 14px; }}"
            f"QPushButton#held_pill:hover {{ background-color: {styles.COLORS['blue_mid']}; }}"
        )
        self._held_pill.clicked.connect(self._on_held_pill_clicked)
        self._held_pill.hide()
        hr.addWidget(self._held_pill)
        outer.addWidget(held_row)

        # Single bordered container — all cart pieces live inside
        box = QFrame()
        box.setObjectName("cart_box")
        box.setStyleSheet(
            "QFrame#cart_box { background-color: white;"
            " border: 1px solid #B0BEC5; border-radius: 6px; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # TOTAL is shown only at the bottom of TotalsPanel — header removed
        # to prevent duplicate display. `total_header` retained as a no-op
        # stub so any external `update_amount` calls keep working.
        class _NullTotalHeader:
            def update_amount(self, _cents): pass
        self.total_header = _NullTotalHeader()

        # Cart list (stretch)
        self._list = QListWidget()
        self._list.setObjectName("cart")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemPressed.connect(self._on_item_pressed)
        self._list.setStyleSheet(
            f"QListWidget#cart {{ background-color: {CART_BG};"
            f" border: none; }}"
            f"QListWidget#cart::item {{ border: none;"
            f" border-bottom: 2px solid #C3CCD8; padding: 0; margin: 0; }}"
            f"QListWidget#cart::item:selected {{ background: transparent; }}"
        )
        v.addWidget(self._list, stretch=1)

        # Tax breakdown
        self.totals_panel = TotalsPanel()
        self.totals_panel.setStyleSheet(
            "QWidget#totals_panel { background-color: white;"
            " border-top: 1px solid #C8D0E0; }"
        )
        v.addWidget(self.totals_panel)

        # Hold | Cancel (very bottom inside the box)
        bottom = QFrame()
        bottom.setObjectName("cart_bottom_actions")
        bottom.setStyleSheet(
            "QFrame#cart_bottom_actions { background-color: white;"
            " border-top: 1px solid #C8D0E0; }"
        )
        bh = QHBoxLayout(bottom)
        bh.setContentsMargins(8, 6, 8, 8)
        bh.setSpacing(8)

        b_hold = self._mk_bottom_btn(
            "Hold", "act_hold_left", styles.COLORS["btn_hold"]
        )
        b_hold.clicked.connect(self.hold_clicked.emit)
        bh.addWidget(b_hold)

        b_cancel = self._mk_bottom_btn(
            "Cancel", "act_cancel_left", styles.COLORS["btn_cancel"]
        )
        b_cancel.clicked.connect(self.cancel_clicked.emit)
        bh.addWidget(b_cancel)

        b_print = self._mk_bottom_btn(
            "Print", "act_print_receipt", styles.COLORS["btn_split"]
        )
        b_print.setToolTip(
            "Print receipt for current cart, or reprint last sale if cart is empty."
        )
        b_print.clicked.connect(self.print_receipt_clicked.emit)
        bh.addWidget(b_print)

        v.addWidget(bottom)

        outer.addWidget(box)

    @staticmethod
    def _mk_bottom_btn(text: str, name: str, color: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumHeight(42)
        f = QFont(styles.FONT_FAMILY, 13); f.setBold(True)
        b.setFont(f)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 16px; }}"
        )
        return b

    # ─── Refresh / sync ──────────────────────────────────────────────────────

    def refresh(self, *, flash_index: Optional[int] = None) -> None:
        """Rebuild list + totals. If flash_index is set, that row flashes green."""
        prev_selected = self._selected_index
        # Batch all paints into a single redraw — avoids per-row flicker under
        # rapid input (10+ items/sec barcode bursts).
        self._list.setUpdatesEnabled(False)
        self._list.blockSignals(True)
        self._list.clear()

        for idx, line in enumerate(self.cart.lines):
            item = QListWidgetItem(self._list)
            row = CartRow(line, idx)
            row.qty_minus.connect(self._on_row_minus)
            row.qty_plus.connect(self._on_row_plus)
            row.remove_clicked.connect(self._on_row_remove)
            row.clicked.connect(self._on_row_clicked)
            row.adjustSize()
            hint = row.sizeHint()
            item.setSizeHint(QSize(hint.width(), 32))
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            item.setData(Qt.ItemDataRole.UserRole, idx)

        if prev_selected is not None and prev_selected < len(self.cart.lines):
            self._list.setCurrentRow(prev_selected)
            row_w = self._row_widget(prev_selected)
            if row_w is not None:
                row_w.set_selected(True)
        else:
            self._selected_index = None

        self._list.blockSignals(False)

        # Totals
        self.totals_panel.update_totals(self.cart.totals)
        self.total_header.update_amount(self.cart.totals["total_cents"])

        # Flash newly added row
        if flash_index is not None and 0 <= flash_index < len(self.cart.lines):
            row_w = self._row_widget(flash_index)
            if row_w is not None:
                row_w.flash_added()
        self._list.setUpdatesEnabled(True)

    def _row_widget(self, idx: int) -> Optional[CartRow]:
        item = self._list.item(idx)
        if item is None:
            return None
        return self._list.itemWidget(item)   # type: ignore[return-value]

    # ─── Selection ───────────────────────────────────────────────────────────

    def _on_item_pressed(self, item) -> None:
        """Toggle selection via QListWidget click (fallback path)."""
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx == self._selected_index:
            QTimer.singleShot(0, self._list.clearSelection)

    def _on_row_clicked(self, idx: int) -> None:
        """Tap on a CartRow body — toggle select/deselect.

        CartRow consumes the press before QListWidget sees it, so the
        QListWidget itemPressed signal often does NOT fire. This explicit
        signal handles the toggle reliably.
        """
        if 0 <= idx < self._list.count():
            if idx == self._selected_index:
                # Re-tap on selected row → deselect.
                self._list.clearSelection()
            else:
                self._list.setCurrentRow(idx)

    def _on_selection_changed(self) -> None:
        # Deselect previous row's inline controls
        if self._selected_index is not None:
            prev = self._row_widget(self._selected_index)
            if prev is not None:
                prev.set_selected(False)

        items = self._list.selectedItems()
        if not items:
            self._selected_index = None
            self.item_selected.emit(None)
            return
        idx = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_index = idx
        new_row = self._row_widget(idx)
        if new_row is not None:
            new_row.set_selected(True)
        self.item_selected.emit(self.cart.lines[idx])

    # ─── Per-row actions ─────────────────────────────────────────────────────

    def _on_row_minus(self, idx: int) -> None:
        if idx >= len(self.cart.lines):
            return
        line = self.cart.lines[idx]
        if line.quantity <= 1:
            self._on_row_remove(idx)
            return
        self.cart.set_quantity(idx, line.quantity - 1)
        self.refresh()
        self.qty_changed.emit(line.quantity)

    def _on_row_plus(self, idx: int) -> None:
        if idx >= len(self.cart.lines):
            return
        line = self.cart.lines[idx]
        new_q = line.quantity + 1
        self.cart.set_quantity(idx, new_q)
        self.refresh()
        self.qty_changed.emit(new_q)

    def _on_row_remove(self, idx: int) -> None:
        if idx >= len(self.cart.lines):
            return
        self.cart.remove_line(idx)
        self._selected_index = None
        self.refresh()
        self.item_removed.emit()

    # ─── Public helpers ──────────────────────────────────────────────────────

    def selected_line(self) -> Optional[CartItem]:
        if self._selected_index is None or self._selected_index >= len(self.cart.lines):
            return None
        return self.cart.lines[self._selected_index]

    def clear_selection(self) -> None:
        self._list.clearSelection()
        self._selected_index = None

    # ─── HELD pill ───────────────────────────────────────────────────────────

    def update_held_count(self, count: int) -> None:
        """Show/update the held-cart pill above the cart panel."""
        if count > 0:
            self._held_pill.setText(f"🔖 HOLD ({count})")
            self._held_pill.show()
        else:
            self._held_pill.hide()

    def _on_held_pill_clicked(self) -> None:
        held = db.list_held()
        if not held:
            self.update_held_count(0)
            return
        dlg = HeldPickerDialog(held, self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        # Always re-sync count after dialog closes — dialog may have cleared rows.
        try:
            self.update_held_count(len(db.list_held()))
        except Exception:
            log.exception("held count refresh failed")
        if accepted and dlg.selected_id is not None:
            self.restore_held_requested.emit(dlg.selected_id)


# ─── Held cart picker dialog ─────────────────────────────────────────────────

class HeldPickerDialog(QDialog):
    """Modal list of held carts. Per-row Clear button + Clear All bottom.

    On accept: selected_id holds the chosen held cart's id (caller restores).
    On reject after deletes: selected_id is None but held DB rows may have
    been removed in-place; caller should refresh the held count.
    """

    def __init__(self, held_rows: list[dict], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("held_picker_dialog")
        self.setWindowTitle("Held Carts")
        self.setMinimumSize(520, 420)
        self.selected_id: Optional[int] = None

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16); v.setSpacing(10)

        self._title = QLabel("")
        self._title.setObjectName("held_picker_title")
        tf = QFont(styles.FONT_FAMILY, 13); tf.setBold(True)
        self._title.setFont(tf)
        v.addWidget(self._title)

        self._list = QListWidget()
        self._list.setObjectName("held_list")
        self._list.itemDoubleClicked.connect(lambda _: self._accept())
        v.addWidget(self._list, stretch=1)
        self._populate(held_rows)

        h = QHBoxLayout(); h.setSpacing(8)
        clear_all = QPushButton("Clear All")
        clear_all.setObjectName("held_picker_clear_all")
        clear_all.setMinimumHeight(40)
        clear_all.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cancel']};"
            f" color: white; border: none; border-radius: 6px;"
            f" padding: 6px 16px; font-weight: bold; }}"
        )
        clear_all.clicked.connect(self._on_clear_all)
        h.addWidget(clear_all)
        h.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setObjectName("held_picker_cancel")
        cancel.setMinimumHeight(40)
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        ok = QPushButton("Retrieve"); ok.setObjectName("held_picker_ok")
        ok.setMinimumHeight(40)
        ok.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']}; color: white;"
            f" border: none; border-radius: 6px; padding: 8px 24px; font-weight: bold; }}"
        )
        ok.clicked.connect(self._accept)
        ok.setDefault(True)
        h.addWidget(ok)
        v.addLayout(h)

    def _populate(self, held_rows: list[dict]) -> None:
        self._list.clear()
        self._title.setText(
            f"{len(held_rows)} held cart(s) — select one to retrieve"
        )
        for row in held_rows:
            label = row.get("hold_label") or "(unlabelled)"
            cashier = row.get("cashier_name") or "?"
            ts = row.get("created_at") or ""
            text = f"#{row['id']}  ·  {label}  ·  {cashier}  ·  {ts}"
            item = QListWidgetItem(self._list)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])

            row_w = QWidget()
            rh = QHBoxLayout(row_w); rh.setContentsMargins(8, 4, 8, 4); rh.setSpacing(8)
            lbl = QLabel(text)
            lbl.setStyleSheet("background: transparent;")
            rh.addWidget(lbl, stretch=1)
            clr = QPushButton("Clear")
            clr.setObjectName(f"held_row_clear_{row['id']}")
            clr.setFixedSize(70, 28)
            clr.setStyleSheet(
                f"QPushButton {{ background-color: {styles.COLORS['btn_cancel']};"
                f" color: white; border: none; border-radius: 4px;"
                f" font-weight: bold; }}"
            )
            clr.clicked.connect(lambda _ck=False, hid=row["id"]: self._on_clear_one(hid))
            rh.addWidget(clr)
            item.setSizeHint(row_w.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row_w)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_clear_one(self, hid: int) -> None:
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Clear Held Cart",
            f"Discard held cart #{hid}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            db.delete_held(hid)
        except Exception:
            log.exception("delete_held failed")
            return
        try:
            self._populate(db.list_held())
        except Exception:
            log.exception("list_held failed")
        if self._list.count() == 0:
            self.reject()

    def _on_clear_all(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Clear All Held Carts",
            "Discard ALL held carts? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            db.clear_all_held()
        except Exception:
            log.exception("clear_all_held failed")
            return
        self.reject()

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self.selected_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.accept()
