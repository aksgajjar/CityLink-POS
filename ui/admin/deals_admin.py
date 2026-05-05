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
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
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


class DealsAdminScreen(QWidget):
    """Deals catalog management screen."""

    back_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("deals_admin_screen")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("DEALS")
        title.setObjectName("deals_admin_title")
        f = QFont(styles.FONT_FAMILY, 22); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back")
        back.setObjectName("deals_admin_back")
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addStretch(1)
        for label, name, slot, color_key in [
            ("+ New Deal",   "deals_btn_add",    self._on_add,    "btn_cash"),
            ("Edit",         "deals_btn_edit",   self._on_edit,   "btn_hold"),
            ("Test Deal",    "deals_btn_test",   self._on_test,   "btn_lottery_s"),
            ("Expire Now",   "deals_btn_expire", self._on_expire, "btn_lottery_p"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(40)
            bf = QFont(styles.FONT_FAMILY, 11); bf.setBold(True)
            b.setFont(bf)
            color = styles.COLORS[color_key]
            b.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white;"
                f" border: none; border-radius: 6px; padding: 8px 14px; }}"
            )
            b.clicked.connect(slot)
            bar.addWidget(b)
        root.addLayout(bar)

        # Deals table
        self._table = QTableWidget()
        self._table.setObjectName("deals_admin_table")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Type", "Start", "Expiry", "Status"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.itemDoubleClicked.connect(lambda _it: self._on_edit())
        root.addWidget(self._table, stretch=1)

    # ─── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        rows = db.conn().execute(
            "SELECT * FROM deals ORDER BY id DESC"
        ).fetchall()
        today = date.today().isoformat()
        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            d = dict(r)
            self._table.setItem(ri, 0, QTableWidgetItem(d["name"]))
            self._table.setItem(ri, 1, QTableWidgetItem(d["deal_type"]))
            self._table.setItem(ri, 2, QTableWidgetItem(d["start_date"]))
            self._table.setItem(ri, 3, QTableWidgetItem(d["expiry_date"]))
            if not d["is_active"]:
                status, color = "Inactive", styles.COLORS["text_muted"]
            elif d["expiry_date"] < today:
                status, color = "Expired", styles.COLORS["danger"]
            elif d["start_date"] > today:
                status, color = "Scheduled", styles.COLORS["warning"]
            else:
                status, color = "Active", styles.COLORS["btn_cash"]
            cell = QTableWidgetItem(status)
            from PyQt6.QtGui import QColor
            cell.setForeground(QColor(color))
            self._table.setItem(ri, 4, cell)
            self._table.item(ri, 0).setData(Qt.ItemDataRole.UserRole, d["id"])

    def _selected_deal_id(self) -> Optional[int]:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ─── Toolbar handlers ────────────────────────────────────────────────────

    def _on_add(self) -> None:
        dlg = DealEditDialog(deal_id=None, parent=self)
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
        self.setWindowTitle("Add Deal" if deal_id is None else "Edit Deal")
        self.setModal(True)
        self.setMinimumSize(560, 540)
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
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        # Common fields
        common_form = QFormLayout()
        common_form.setSpacing(8)

        self._name = QLineEdit()
        self._name.setObjectName("deal_name")
        common_form.addRow("Deal name:", self._name)

        self._type_combo = QComboBox()
        self._type_combo.setObjectName("deal_type_combo")
        for t in DEAL_TYPES:
            self._type_combo.addItem(DEAL_TYPE_LABELS[t], t)
        self._type_combo.currentIndexChanged.connect(self._rebuild_type_form)
        common_form.addRow("Type:", self._type_combo)

        self._start = QDateEdit(QDate.currentDate())
        self._start.setObjectName("deal_start_date")
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        common_form.addRow("Start date:", self._start)

        self._expiry = QDateEdit(QDate.currentDate().addDays(30))
        self._expiry.setObjectName("deal_expiry_date")
        self._expiry.setCalendarPopup(True)
        self._expiry.setDisplayFormat("yyyy-MM-dd")
        common_form.addRow("Expiry date:", self._expiry)

        self._active = QCheckBox("Active")
        self._active.setObjectName("deal_active")
        self._active.setChecked(True)
        common_form.addRow("", self._active)

        v.addLayout(common_form)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(sep)

        type_lbl = QLabel("Deal-specific settings:")
        tlf = QFont(styles.FONT_FAMILY, 11); tlf.setBold(True)
        type_lbl.setFont(tlf)
        v.addWidget(type_lbl)

        # Type-specific form holder
        self._type_holder = QWidget()
        self._type_form = QVBoxLayout(self._type_holder)
        self._type_form.setSpacing(8)
        v.addWidget(self._type_holder)

        v.addStretch(1)

        # Buttons
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setObjectName("deal_save")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("deal_cancel")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _rebuild_type_form(self) -> None:
        # Clear all widgets in holder
        while self._type_form.count():
            it = self._type_form.takeAt(0)
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

    # ─── Per-type forms ──────────────────────────────────────────────────────

    def _item_combo(self) -> QComboBox:
        cb = QComboBox()
        for r in self._items_cache:
            cb.addItem(f"{r['name']}  (${r['price_cents']/100:.2f})", r["id"])
        return cb

    def _build_qty_form(self) -> None:
        f = QFormLayout()
        self._qty_target = self._item_combo()
        self._qty_target.setObjectName("qty_target_item")
        f.addRow("Item:", self._qty_target)
        self._qty_qty = QSpinBox()
        self._qty_qty.setObjectName("qty_need_qty")
        self._qty_qty.setMinimum(1); self._qty_qty.setMaximum(99); self._qty_qty.setValue(3)
        f.addRow("Qty needed:", self._qty_qty)
        self._qty_total_price = QLineEdit()
        self._qty_total_price.setObjectName("qty_total_price")
        self._qty_total_price.setPlaceholderText("Total price for that qty (e.g. 5.00)")
        f.addRow("Bundle price:", self._qty_total_price)
        self._type_form.addLayout(f)

    def _build_bundle_form(self) -> None:
        v = QVBoxLayout()
        v.addWidget(QLabel("Items in bundle (one of each):"))
        self._bundle_list = QListWidget()
        self._bundle_list.setObjectName("bundle_items_list")
        self._bundle_list.setMinimumHeight(120)
        v.addWidget(self._bundle_list)

        h = QHBoxLayout()
        self._bundle_picker = self._item_combo()
        self._bundle_picker.setObjectName("bundle_picker")
        h.addWidget(self._bundle_picker, stretch=1)
        add_btn = QPushButton("+ Add"); add_btn.setObjectName("bundle_add_btn")
        add_btn.clicked.connect(self._on_bundle_add)
        h.addWidget(add_btn)
        rm_btn = QPushButton("Remove"); rm_btn.setObjectName("bundle_remove_btn")
        rm_btn.clicked.connect(self._on_bundle_remove)
        h.addWidget(rm_btn)
        v.addLayout(h)

        f = QFormLayout()
        self._bundle_fixed = QLineEdit()
        self._bundle_fixed.setObjectName("bundle_fixed_price")
        self._bundle_fixed.setPlaceholderText("e.g. 3.99")
        f.addRow("Fixed bundle price:", self._bundle_fixed)
        v.addLayout(f)
        self._type_form.addLayout(v)

    def _on_bundle_add(self) -> None:
        if self._bundle_picker is None or self._bundle_list is None:
            return
        item_id = self._bundle_picker.currentData()
        item_text = self._bundle_picker.currentText()
        if item_id is None:
            return
        # Skip if already added
        for i in range(self._bundle_list.count()):
            if self._bundle_list.item(i).data(Qt.ItemDataRole.UserRole) == item_id:
                return
        li = QListWidgetItem(item_text)
        li.setData(Qt.ItemDataRole.UserRole, item_id)
        self._bundle_list.addItem(li)

    def _on_bundle_remove(self) -> None:
        if self._bundle_list is None:
            return
        for it in self._bundle_list.selectedItems():
            self._bundle_list.takeItem(self._bundle_list.row(it))

    def _build_spend_form(self) -> None:
        f = QFormLayout()
        self._spend_target = self._item_combo()
        self._spend_target.setObjectName("spend_target_item")
        f.addRow("Item:", self._spend_target)
        self._spend_qty = QSpinBox()
        self._spend_qty.setObjectName("spend_need_qty")
        self._spend_qty.setMinimum(1); self._spend_qty.setMaximum(99); self._spend_qty.setValue(2)
        f.addRow("Qty needed:", self._spend_qty)
        self._spend_disc = QLineEdit()
        self._spend_disc.setObjectName("spend_discount")
        self._spend_disc.setPlaceholderText("Dollars off (e.g. 1.00)")
        f.addRow("Discount:", self._spend_disc)
        self._type_form.addLayout(f)

    def _build_cross_form(self) -> None:
        f = QFormLayout()
        self._cross_trig_dept = QComboBox()
        self._cross_trig_dept.setObjectName("cross_trig_dept")
        for d in DEPARTMENTS:
            self._cross_trig_dept.addItem(d["label"], d["id"])
        f.addRow("Trigger dept:", self._cross_trig_dept)

        self._cross_tgt_dept = QComboBox()
        self._cross_tgt_dept.setObjectName("cross_tgt_dept")
        for d in DEPARTMENTS:
            self._cross_tgt_dept.addItem(d["label"], d["id"])
        f.addRow("Target dept:", self._cross_tgt_dept)

        self._cross_pct = QSpinBox()
        self._cross_pct.setObjectName("cross_discount_pct")
        self._cross_pct.setMinimum(1); self._cross_pct.setMaximum(100); self._cross_pct.setValue(10)
        self._cross_pct.setSuffix(" %")
        f.addRow("Discount:", self._cross_pct)
        self._type_form.addLayout(f)

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
            if t == "qty_discount" and self._qty_target is not None:
                idx = self._qty_target.findData(trig.get("item_id"))
                if idx >= 0: self._qty_target.setCurrentIndex(idx)
                self._qty_qty.setValue(int(trig.get("qty", 1)))
                self._qty_total_price.setText(f"{rew.get('total_price_cents', 0) / 100:.2f}")
            elif t == "bundle" and self._bundle_list is not None:
                self._bundle_list.clear()
                for iid in trig.get("items", []):
                    item = db.get_item(iid)
                    if item is None:
                        continue
                    li = QListWidgetItem(f"{item['name']}  (${item['price_cents']/100:.2f})")
                    li.setData(Qt.ItemDataRole.UserRole, iid)
                    self._bundle_list.addItem(li)
                self._bundle_fixed.setText(f"{rew.get('fixed_price_cents', 0) / 100:.2f}")
            elif t == "spend_discount" and self._spend_target is not None:
                idx = self._spend_target.findData(trig.get("item_id"))
                if idx >= 0: self._spend_target.setCurrentIndex(idx)
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
            target = self._qty_target.currentData()
            if target is None:
                raise ValueError("Pick an item.")
            qty = self._qty_qty.value()
            total = self._dollars_to_cents(self._qty_total_price.text(), "Bundle price")
            return {"item_id": target, "qty": qty}, {"total_price_cents": total}

        if t == "bundle":
            ids = []
            for i in range(self._bundle_list.count()):
                ids.append(self._bundle_list.item(i).data(Qt.ItemDataRole.UserRole))
            if len(ids) < 2:
                raise ValueError("Bundle needs at least 2 items.")
            fixed = self._dollars_to_cents(self._bundle_fixed.text(), "Bundle price")
            return {"items": ids}, {"fixed_price_cents": fixed}

        if t == "spend_discount":
            target = self._spend_target.currentData()
            if target is None:
                raise ValueError("Pick an item.")
            qty = self._spend_qty.value()
            disc = self._dollars_to_cents(self._spend_disc.text(), "Discount")
            return {"item_id": target, "qty": qty}, {"discount_cents": disc}

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
