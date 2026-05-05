"""Cart display + qty controls + totals panel.

Wraps a live `Cart` object — does not copy. Mutations call back into the cart
(which calls `recompute()` internally) and then `refresh()` rebuilds the view.

Row visuals:
  - regular item:  default white background
  - deal applied:  deal_highlight (yellow)
  - lottery line:  light purple tint
  - bag charge:    muted italic, very light grey

Signals:
  - item_selected(object)  : CartItem or None
  - qty_changed(int)        : new quantity after +/- press
  - item_removed()          : after Remove press
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
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

from core.cart import Cart
from core.logger import get_logger
from core.models import CartItem
from ui import styles

log = get_logger("ui.cart")

LOTTERY_TINT = "#F4ECF7"   # light purple
BAG_TINT = "#FAFAFA"       # near-white grey


# ─── Row widget ──────────────────────────────────────────────────────────────

class CartRow(QWidget):
    """Visual row for one CartItem. Used via QListWidget.setItemWidget."""

    def __init__(self, line: CartItem, line_index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.line_index = line_index
        self.line = line
        self.setObjectName(f"cart_row_{line_index}")
        self._build(line)

    def _build(self, line: CartItem) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

        # Name (stretches)
        name = QLabel(line.name)
        name.setObjectName("cart_row_name")
        name.setFont(QFont(styles.FONT_FAMILY, 13))
        h.addWidget(name, stretch=1)

        # Qty
        qty = QLabel(f"{line.quantity}x")
        qty.setObjectName("cart_row_qty")
        qty.setMinimumWidth(40)
        qty.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(qty)

        # Unit price
        unit = QLabel(f"${line.unit_price_cents / 100:.2f}")
        unit.setObjectName("cart_row_unit")
        unit.setMinimumWidth(70)
        unit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        unit.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        h.addWidget(unit)

        # Discount (only when present)
        if line.deal_discount_cents > 0:
            disc = QLabel(f"-${line.deal_discount_cents / 100:.2f}")
            disc.setObjectName("cart_row_discount")
            disc.setMinimumWidth(80)
            disc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            disc.setStyleSheet(f"color: {styles.COLORS['btn_cash']}; font-weight: bold;")
            h.addWidget(disc)

        # Line total (bold)
        total = QLabel(f"${line.line_total_cents / 100:.2f}")
        total.setObjectName("cart_row_total")
        total.setMinimumWidth(80)
        total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        f = QFont(styles.FONT_FAMILY, 13)
        f.setBold(True)
        total.setFont(f)
        h.addWidget(total)

        self._apply_tint(line)

    def _apply_tint(self, line: CartItem) -> None:
        if line.kind == "lottery":
            self.setStyleSheet(f"background-color: {LOTTERY_TINT};")
        elif line.kind == "bag":
            self.setStyleSheet(
                f"background-color: {BAG_TINT}; "
                f"color: {styles.COLORS['text_muted']}; "
                f"font-style: italic;"
            )
        elif line.deal_id is not None:
            self.setStyleSheet(f"background-color: {styles.COLORS['deal_highlight']};")
        else:
            self.setStyleSheet(f"background-color: {styles.COLORS['white']};")


# ─── Totals panel ────────────────────────────────────────────────────────────

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
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)

        font_row = QFont(styles.FONT_FAMILY, 12)
        font_total_label = QFont(styles.FONT_FAMILY, 18)
        font_total_label.setBold(True)
        font_total_value = QFont(styles.FONT_FAMILY, 28)
        font_total_value.setBold(True)

        def add_row(row: int, label_text: str, value_name: str) -> QLabel:
            lab = QLabel(label_text)
            lab.setFont(font_row)
            val = QLabel("$0.00")
            val.setObjectName(value_name)
            val.setFont(font_row)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lab, row, 0)
            grid.addWidget(val, row, 1)
            return val

        self._lbl_subtotal = add_row(0, "Subtotal:", "totals_subtotal")
        self._lbl_discount = add_row(1, "Discount:", "totals_discount")
        self._lbl_gst      = add_row(2, "GST (5%):", "totals_gst")
        self._lbl_pst      = add_row(3, "PST (7%):", "totals_pst")
        self._lbl_deposit  = add_row(4, "Deposit:",  "totals_deposit")
        self._lbl_bag      = add_row(5, "Bag:",      "totals_bag")

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {styles.COLORS['blue_mid']};")
        grid.addWidget(sep, 6, 0, 1, 2)

        # TOTAL line
        total_label = QLabel("TOTAL:")
        total_label.setObjectName("total_label")
        total_label.setFont(font_total_label)
        self._lbl_total = QLabel("$0.00")
        self._lbl_total.setObjectName("total_amount")
        self._lbl_total.setFont(font_total_value)
        self._lbl_total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_total.setStyleSheet(f"color: {styles.COLORS['navy']};")
        grid.addWidget(total_label, 7, 0)
        grid.addWidget(self._lbl_total, 7, 1)

        # Cash rounded preview (hidden when equal to total)
        self._lbl_cash_preview = QLabel("")
        self._lbl_cash_preview.setObjectName("totals_cash_preview")
        self._lbl_cash_preview.setFont(QFont(styles.FONT_FAMILY, 11))
        self._lbl_cash_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_cash_preview.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        grid.addWidget(self._lbl_cash_preview, 8, 0, 1, 2)

    def update_totals(self, totals: dict) -> None:
        def fmt(c: int) -> str: return f"${c / 100:.2f}"

        self._lbl_subtotal.setText(fmt(totals["subtotal_cents"]))
        # Discount shown as negative when present
        d = totals["discount_cents"]
        self._lbl_discount.setText(f"-{fmt(d)}" if d else "$0.00")
        self._lbl_gst.setText(fmt(totals["gst_cents"]))
        self._lbl_pst.setText(fmt(totals["pst_cents"]))
        self._lbl_deposit.setText(fmt(totals["deposit_cents"]))
        self._lbl_bag.setText(fmt(totals["bag_charge_cents"]))
        self._lbl_total.setText(fmt(totals["total_cents"]))
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
    """Live cart view + qty controls + totals."""

    item_selected = pyqtSignal(object)   # CartItem | None
    qty_changed   = pyqtSignal(int)      # new quantity
    item_removed  = pyqtSignal()

    def __init__(self, cart: Cart, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cart_widget")
        self.cart = cart
        self._selected_index: Optional[int] = None
        self._build_ui()
        self.refresh()

    # ─── construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Cart list
        self._list = QListWidget()
        self._list.setObjectName("cart")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._list, stretch=1)

        # Qty + Remove control row (enabled only when row selected)
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._btn_minus = self._mk_ctrl("−", "cart_btn_minus")
        self._btn_minus.clicked.connect(lambda: self._adjust_qty(-1))
        ctrl.addWidget(self._btn_minus)

        self._qty_label = QLabel("—")
        self._qty_label.setObjectName("cart_btn_qty_label")
        self._qty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(styles.FONT_FAMILY, 16)
        f.setBold(True)
        self._qty_label.setFont(f)
        self._qty_label.setMinimumWidth(60)
        ctrl.addWidget(self._qty_label)

        self._btn_plus = self._mk_ctrl("+", "cart_btn_plus")
        self._btn_plus.clicked.connect(lambda: self._adjust_qty(+1))
        ctrl.addWidget(self._btn_plus)

        ctrl.addStretch(1)

        self._btn_remove = self._mk_ctrl("Remove", "cart_btn_remove")
        self._btn_remove.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cancel']}; "
            f"color: white; border: none; border-radius: 6px; padding: 6px 16px; }}"
        )
        self._btn_remove.clicked.connect(self._remove_selected)
        ctrl.addWidget(self._btn_remove)

        root.addLayout(ctrl)

        # Totals panel
        self.totals_panel = TotalsPanel()
        root.addWidget(self.totals_panel)

        self._set_controls_enabled(False)

    def _mk_ctrl(self, text: str, name: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumSize(50, 36)
        f = QFont(styles.FONT_FAMILY, 16)
        f.setBold(True)
        b.setFont(f)
        return b

    # ─── refresh / sync from cart ───────────────────────────────────────────

    def refresh(self) -> None:
        """Rebuild the list view and totals from the current Cart state."""
        prev_selected = self._selected_index
        self._list.blockSignals(True)
        self._list.clear()

        for idx, line in enumerate(self.cart.lines):
            item = QListWidgetItem(self._list)
            row = CartRow(line, idx)
            row.adjustSize()
            hint = row.sizeHint()
            from PyQt6.QtCore import QSize
            item.setSizeHint(QSize(hint.width(), max(hint.height(), 44)))
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            item.setData(Qt.ItemDataRole.UserRole, idx)

        # Restore selection if still valid
        if prev_selected is not None and prev_selected < len(self.cart.lines):
            self._list.setCurrentRow(prev_selected)
        else:
            self._selected_index = None

        self._list.blockSignals(False)
        self.totals_panel.update_totals(self.cart.totals)
        self._sync_controls()

    # ─── selection / qty / remove handlers ──────────────────────────────────

    def _on_selection_changed(self) -> None:
        items = self._list.selectedItems()
        if not items:
            self._selected_index = None
            self._sync_controls()
            self.item_selected.emit(None)
            return
        idx = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_index = idx
        line = self.cart.lines[idx]
        self._sync_controls()
        self.item_selected.emit(line)

    def _adjust_qty(self, delta: int) -> None:
        if self._selected_index is None:
            return
        idx = self._selected_index
        line = self.cart.lines[idx]
        new_qty = line.quantity + delta
        if new_qty <= 0:
            # Treat as remove
            self._remove_selected()
            return
        self.cart.set_quantity(idx, new_qty)
        self.refresh()
        self.qty_changed.emit(new_qty)

    def _remove_selected(self) -> None:
        if self._selected_index is None:
            return
        idx = self._selected_index
        self.cart.remove_line(idx)
        self._selected_index = None
        self.refresh()
        self.item_removed.emit()

    # ─── helpers ────────────────────────────────────────────────────────────

    def _sync_controls(self) -> None:
        if self._selected_index is None or self._selected_index >= len(self.cart.lines):
            self._set_controls_enabled(False)
            self._qty_label.setText("—")
            return
        line = self.cart.lines[self._selected_index]
        self._set_controls_enabled(True)
        self._qty_label.setText(str(line.quantity))

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._btn_minus.setEnabled(enabled)
        self._btn_plus.setEnabled(enabled)
        self._btn_remove.setEnabled(enabled)

    # ─── public ─────────────────────────────────────────────────────────────

    def selected_line(self) -> Optional[CartItem]:
        if self._selected_index is None or self._selected_index >= len(self.cart.lines):
            return None
        return self.cart.lines[self._selected_index]

    def clear_selection(self) -> None:
        self._list.clearSelection()
        self._selected_index = None
        self._sync_controls()
