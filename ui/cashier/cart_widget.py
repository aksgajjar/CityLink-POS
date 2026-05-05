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

CART_BG = "#EEF2FF"           # very light blue-grey, premium feel
ROW_BG_DEFAULT = "#EEF2FF"
ROW_BG_DEAL = styles.COLORS["deal_highlight"]   # yellow
ROW_BG_LOTTERY = "#F4ECF7"     # light purple
ROW_BG_BAG = "#FAFAFA"         # near-white grey
ROW_BG_FLASH = "#D5F5E3"       # bright mint, fades after 800ms
FLASH_MS = 800


# ─── Big TOTAL header ────────────────────────────────────────────────────────

class CartTotalHeader(QWidget):
    """Prominent TOTAL bar above the cart list."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cart_total_header")
        self.setFixedHeight(64)
        self.setStyleSheet(
            f"QWidget#cart_total_header {{ background-color: white;"
            f" border-bottom: 2px solid {styles.COLORS['navy']}; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 14, 6)
        h.setSpacing(10)

        lbl = QLabel("TOTAL")
        lbl.setObjectName("cart_total_label")
        lf = QFont(styles.FONT_FAMILY, 16); lf.setBold(True)
        lbl.setFont(lf)
        lbl.setStyleSheet(f"color: {styles.COLORS['navy']}; background: transparent;")
        h.addWidget(lbl)

        self.amount = QLabel("$0.00")
        self.amount.setObjectName("cart_total_amount")
        self.amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        af = QFont(styles.FONT_FAMILY, 24); af.setBold(True)
        self.amount.setFont(af)
        self.amount.setStyleSheet(f"color: {styles.COLORS['navy']}; background: transparent;")
        h.addWidget(self.amount, stretch=1)

    def update_amount(self, total_cents: int) -> None:
        self.amount.setText(f"${total_cents / 100:.2f}")


# ─── Per-row widget ──────────────────────────────────────────────────────────

class CartRow(QWidget):
    """One cart line. Selected → static qty/unit/total swapped for inline controls."""

    qty_minus = pyqtSignal(int)   # line_index
    qty_plus = pyqtSignal(int)
    remove_clicked = pyqtSignal(int)

    def __init__(self, line: CartItem, line_index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.line_index = line_index
        self.line = line
        self.setObjectName(f"cart_row_{line_index}")
        self._selected: bool = False
        self._build(line)
        self._apply_tint(line)

    def _build(self, line: CartItem) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)

        # Name (always visible, stretches)
        self._name_lbl = QLabel(line.name)
        self._name_lbl.setObjectName("cart_row_name")
        self._name_lbl.setFont(QFont(styles.FONT_FAMILY, 13))
        h.addWidget(self._name_lbl, stretch=1)

        # Static cluster: qty + unit + (discount) + total
        self._static = QWidget()
        self._static.setObjectName("cart_row_static")
        sh = QHBoxLayout(self._static)
        sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(8)

        self._qty_lbl = QLabel(f"{line.quantity}x")
        self._qty_lbl.setObjectName("cart_row_qty")
        self._qty_lbl.setMinimumWidth(36)
        self._qty_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sh.addWidget(self._qty_lbl)

        self._unit_lbl = QLabel(f"${line.unit_price_cents / 100:.2f}")
        self._unit_lbl.setObjectName("cart_row_unit")
        self._unit_lbl.setMinimumWidth(64)
        self._unit_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._unit_lbl.setStyleSheet(f"color: {styles.COLORS['text_muted']}; background: transparent;")
        sh.addWidget(self._unit_lbl)

        if line.deal_discount_cents > 0:
            disc = QLabel(f"-${line.deal_discount_cents / 100:.2f}")
            disc.setObjectName("cart_row_discount")
            disc.setMinimumWidth(72)
            disc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            disc.setStyleSheet(f"color: {styles.COLORS['btn_cash']}; font-weight: bold; background: transparent;")
            sh.addWidget(disc)

        self._total_lbl = QLabel(f"${line.line_total_cents / 100:.2f}")
        self._total_lbl.setObjectName("cart_row_total")
        self._total_lbl.setMinimumWidth(80)
        self._total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tf = QFont(styles.FONT_FAMILY, 13); tf.setBold(True)
        self._total_lbl.setFont(tf)
        sh.addWidget(self._total_lbl)

        h.addWidget(self._static)

        # Inline controls (hidden until row selected)
        self._controls = QWidget()
        self._controls.setObjectName("cart_row_controls")
        ch = QHBoxLayout(self._controls)
        ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(4)

        self._btn_minus = self._mk_ctrl("−", f"row{self.line_index}_btn_minus", color=styles.COLORS["blue_mid"])
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

        self._btn_plus = self._mk_ctrl("+", f"row{self.line_index}_btn_plus", color=styles.COLORS["blue_mid"])
        self._btn_plus.clicked.connect(lambda _ck=False: self.qty_plus.emit(self.line_index))
        ch.addWidget(self._btn_plus)

        self._btn_remove = self._mk_ctrl("🗑", f"row{self.line_index}_btn_remove", color=styles.COLORS["btn_cancel"])
        self._btn_remove.clicked.connect(lambda _ck=False: self.remove_clicked.emit(self.line_index))
        ch.addWidget(self._btn_remove)

        self._controls.hide()
        h.addWidget(self._controls)

    def _mk_ctrl(self, text: str, name: str, *, color: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setMinimumSize(36, 36)
        b.setMaximumHeight(36)
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        b.setFont(f)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white;"
            f" border: none; border-radius: 4px; padding: 2px 10px; }}"
            f"QPushButton:pressed {{ padding: 4px 10px 0px 12px; }}"
        )
        return b

    def _apply_tint(self, line: CartItem) -> None:
        if self._selected:
            return
        if line.kind == "lottery":
            bg = ROW_BG_LOTTERY
            extra = ""
        elif line.kind == "bag":
            bg = ROW_BG_BAG
            extra = f"color: {styles.COLORS['text_muted']}; font-style: italic;"
        elif line.deal_id is not None:
            bg = ROW_BG_DEAL
            extra = ""
        else:
            bg = ROW_BG_DEFAULT
            extra = ""
        self.setStyleSheet(f"CartRow {{ background-color: {bg}; }} {extra}")

    # ─── Public API for CartWidget ───────────────────────────────────────────

    def set_selected(self, selected: bool) -> None:
        """Toggle inline controls. Static cluster hides while selected."""
        self._selected = selected
        if selected:
            self._static.hide()
            self._qty_in_ctrl.setText(str(self.line.quantity))
            self._controls.show()
            # Slightly tinted bg for selected
            self.setStyleSheet(
                f"CartRow {{ background-color: {styles.COLORS['blue_light']}; }}"
                f" QLabel {{ color: white; background: transparent; }}"
            )
        else:
            self._controls.hide()
            self._static.show()
            self._apply_tint(self.line)

    def flash_added(self) -> None:
        """Flash green bg for FLASH_MS then revert to natural tint."""
        self.setStyleSheet(f"CartRow {{ background-color: {ROW_BG_FLASH}; }}")
        QTimer.singleShot(FLASH_MS, lambda: self._apply_tint(self.line) if not self._selected else None)


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
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(2)

        font_row = QFont(styles.FONT_FAMILY, 11)

        def add_row(row: int, label_text: str, value_name: str) -> QLabel:
            lab = QLabel(label_text); lab.setFont(font_row)
            val = QLabel("$0.00"); val.setObjectName(value_name); val.setFont(font_row)
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

        # Cash rounded preview (hidden when equal to total)
        self._lbl_cash_preview = QLabel("")
        self._lbl_cash_preview.setObjectName("totals_cash_preview")
        self._lbl_cash_preview.setFont(QFont(styles.FONT_FAMILY, 10))
        self._lbl_cash_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_cash_preview.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        grid.addWidget(self._lbl_cash_preview, 6, 0, 1, 2)

    def update_totals(self, totals: dict) -> None:
        def fmt(c: int) -> str: return f"${c / 100:.2f}"
        self._lbl_subtotal.setText(fmt(totals["subtotal_cents"]))
        d = totals["discount_cents"]
        self._lbl_discount.setText(f"-{fmt(d)}" if d else "$0.00")
        self._lbl_gst.setText(fmt(totals["gst_cents"]))
        self._lbl_pst.setText(fmt(totals["pst_cents"]))
        self._lbl_deposit.setText(fmt(totals["deposit_cents"]))
        self._lbl_bag.setText(fmt(totals["bag_charge_cents"]))
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
    """Live cart view with inline row controls + big TOTAL header."""

    item_selected = pyqtSignal(object)   # CartItem | None
    qty_changed = pyqtSignal(int)        # new qty
    item_removed = pyqtSignal()

    def __init__(self, cart: Cart, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cart_widget")
        self.cart = cart
        self._selected_index: Optional[int] = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Big TOTAL header
        self.total_header = CartTotalHeader()
        root.addWidget(self.total_header)

        # Cart list
        self._list = QListWidget()
        self._list.setObjectName("cart")
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.setStyleSheet(
            f"QListWidget#cart {{ background-color: {CART_BG};"
            f" border: 1px solid #C8D0E0; }}"
            f"QListWidget#cart::item {{ border: none; padding: 0; }}"
            f"QListWidget#cart::item:selected {{ background: transparent; }}"
        )
        root.addWidget(self._list, stretch=1)

        # Totals breakdown panel
        self.totals_panel = TotalsPanel()
        self.totals_panel.setStyleSheet(
            "QWidget#totals_panel { background-color: white; border-top: 1px solid #C8D0E0; }"
        )
        root.addWidget(self.totals_panel)

    # ─── Refresh / sync ──────────────────────────────────────────────────────

    def refresh(self, *, flash_index: Optional[int] = None) -> None:
        """Rebuild list + totals. If flash_index is set, that row flashes green."""
        prev_selected = self._selected_index
        self._list.blockSignals(True)
        self._list.clear()

        for idx, line in enumerate(self.cart.lines):
            item = QListWidgetItem(self._list)
            row = CartRow(line, idx)
            row.qty_minus.connect(self._on_row_minus)
            row.qty_plus.connect(self._on_row_plus)
            row.remove_clicked.connect(self._on_row_remove)
            row.adjustSize()
            hint = row.sizeHint()
            item.setSizeHint(QSize(hint.width(), max(hint.height(), 50)))
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

    def _row_widget(self, idx: int) -> Optional[CartRow]:
        item = self._list.item(idx)
        if item is None:
            return None
        return self._list.itemWidget(item)   # type: ignore[return-value]

    # ─── Selection ───────────────────────────────────────────────────────────

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
