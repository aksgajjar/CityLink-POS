"""Admin inventory screen.

Features:
  - Searchable item list (table)
  - Add / Edit / Deactivate items
  - CSV bulk import
  - Barcode-miss log (review unknown scans, add inline)
  - Price-change history per item
  - Label CSV export (name, price, barcode)
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from core import db
from core.departments import DEPARTMENTS, DEPT_BY_ID, DEPT_TAX_DEFAULTS
from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.inventory")

EXPORTS_DIR = Path("exports")


# ─── Inventory screen ────────────────────────────────────────────────────────

class InventoryScreen(QWidget):
    """Item catalog management."""

    back_requested = pyqtSignal()

    def __init__(self, *, admin_name: str = "admin", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("inventory_screen")
        self._admin_name = admin_name
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("INVENTORY")
        title.setObjectName("inventory_title")
        f = QFont(styles.FONT_FAMILY, 22); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back")
        back.setObjectName("inventory_back")
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._search = QLineEdit()
        self._search.setObjectName("inventory_search")
        self._search.setPlaceholderText("Search by name or barcode…")
        self._search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self._search, stretch=1)

        for label, name, slot, color_key in [
            ("+ Add Item",        "inv_btn_add",      self._on_add,            "btn_cash"),
            ("Import CSV",        "inv_btn_import",   self._on_import_csv,     "btn_hold"),
            ("Export Labels",     "inv_btn_export",   self._on_export_labels,  "btn_hold"),
            ("Barcode Misses",    "inv_btn_misses",   self._on_misses,         "btn_lottery_p"),
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

        # Item table
        self._table = QTableWidget()
        self._table.setObjectName("inventory_table")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["Barcode", "Name", "Dept", "Price", "GST", "PST", "Active"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.itemDoubleClicked.connect(lambda _it: self._on_edit_selected())
        root.addWidget(self._table, stretch=1)

        # Bottom action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for label, name, slot in [
            ("Edit Selected",       "inv_btn_edit",     self._on_edit_selected),
            ("Price History",       "inv_btn_history",  self._on_price_history),
            ("Deactivate Selected", "inv_btn_deact",    self._on_deactivate_selected),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(40)
            b.clicked.connect(slot)
            action_row.addWidget(b)
        action_row.addStretch(1)
        root.addLayout(action_row)

    # ─── Refresh / search ────────────────────────────────────────────────────

    def refresh(self) -> None:
        rows = db.list_all_items(active_only=False)
        q = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        if q:
            rows = [
                r for r in rows
                if q in (r["name"] or "").lower()
                or q in (r["barcode"] or "").lower()
            ]
        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            self._table.setItem(ri, 0, QTableWidgetItem(r["barcode"] or ""))
            self._table.setItem(ri, 1, QTableWidgetItem(r["name"]))
            self._table.setItem(ri, 2, QTableWidgetItem(r["department"]))
            price = QTableWidgetItem(f"${r['price_cents'] / 100:.2f}")
            price.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(ri, 3, price)
            self._table.setItem(ri, 4, QTableWidgetItem("Y" if r["tax_gst"] else "—"))
            self._table.setItem(ri, 5, QTableWidgetItem("Y" if r["tax_pst"] else "—"))
            active = QTableWidgetItem("Active" if r["is_active"] else "Inactive")
            if not r["is_active"]:
                from PyQt6.QtGui import QColor
                active.setForeground(QColor(styles.COLORS["text_muted"]))
            self._table.setItem(ri, 6, active)
            # Stash item id on row
            self._table.item(ri, 0).setData(Qt.ItemDataRole.UserRole, r["id"])

    def _on_search_changed(self, _text: str) -> None:
        self.refresh()

    def _selected_item_id(self) -> Optional[int]:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        ri = rows[0].row()
        item = self._table.item(ri, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ─── Add / Edit / Deactivate ─────────────────────────────────────────────

    def _on_add(self) -> None:
        dlg = ItemEditDialog(item_id=None, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit_selected(self) -> None:
        iid = self._selected_item_id()
        if iid is None:
            self._info("Select an item first.")
            return
        dlg = ItemEditDialog(item_id=iid, admin_name=self._admin_name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_deactivate_selected(self) -> None:
        iid = self._selected_item_id()
        if iid is None:
            self._info("Select an item first.")
            return
        item = db.get_item(iid)
        if not item:
            return
        if not self._confirm(f"Deactivate '{item['name']}'?\n(Soft delete — can be reactivated.)"):
            return
        try:
            db.deactivate_item(iid)
            self.refresh()
        except Exception:
            log.exception("deactivate failed")
            self._error("Deactivate failed.")

    def _on_price_history(self) -> None:
        iid = self._selected_item_id()
        if iid is None:
            self._info("Select an item first.")
            return
        rows = db.get_price_history(iid)
        item = db.get_item(iid)
        if not rows:
            self._info(f"No price history for {item['name']}.")
            return
        dlg = PriceHistoryDialog(item, rows, parent=self)
        dlg.exec()

    # ─── CSV import / label export ───────────────────────────────────────────

    def _on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import items from CSV", "", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                ok_count = 0
                err_count = 0
                errors: list[str] = []
                for i, row in enumerate(reader, start=2):
                    try:
                        barcode = row.get("barcode", "").strip() or None
                        name = row["name"].strip()
                        price_cents = int(round(float(row["price"]) * 100))
                        dept = row["department"].strip()
                        if dept not in DEPT_BY_ID:
                            raise ValueError(f"unknown department {dept!r}")
                        gst = int(row.get("gst") or 1)
                        pst = int(row.get("pst") or 0)
                        deposit = (row.get("deposit") or "none").strip()
                        # Update if barcode exists, else create
                        existing = db.get_item_by_barcode(barcode) if barcode else None
                        if existing:
                            db.update_item(
                                existing["id"], changed_by=self._admin_name,
                                name=name, price_cents=price_cents,
                                department=dept, tax_gst=gst, tax_pst=pst,
                                bottle_deposit=deposit,
                            )
                        else:
                            db.create_item(barcode, name, price_cents, dept,
                                           gst=gst, pst=pst, deposit=deposit)
                        ok_count += 1
                    except Exception as exc:
                        err_count += 1
                        errors.append(f"row {i}: {exc}")
            self.refresh()
            msg = f"Imported {ok_count} item(s)."
            if err_count:
                msg += f"\n{err_count} error(s):\n" + "\n".join(errors[:10])
                if err_count > 10:
                    msg += f"\n... and {err_count - 10} more"
            self._info(msg)
        except Exception:
            log.exception("CSV import failed")
            self._error("CSV import failed. See errors.log.")

    def _on_export_labels(self) -> None:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = EXPORTS_DIR / f"labels_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        try:
            rows = db.list_all_items(active_only=True)
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["name", "price", "barcode"])
                for r in rows:
                    w.writerow([
                        r["name"],
                        f"{r['price_cents'] / 100:.2f}",
                        r["barcode"] or "",
                    ])
            self._info(f"Labels exported.\n{out}")
        except Exception:
            log.exception("label export failed")
            self._error("Label export failed.")

    # ─── Barcode miss log ────────────────────────────────────────────────────

    def _on_misses(self) -> None:
        dlg = BarcodeMissDialog(parent=self)
        dlg.add_requested.connect(self._add_from_miss)
        dlg.exec()
        self.refresh()

    def _add_from_miss(self, barcode: str) -> None:
        dlg = ItemEditDialog(item_id=None, prefill_barcode=barcode,
                             admin_name=self._admin_name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Item now exists — purge its barcode from the miss log so it doesn't
            # show as "unknown" anymore in the dialog.
            try:
                db.clear_barcode_miss(barcode)
            except Exception:
                log.exception("clear_barcode_miss failed for %s", barcode)

    # ─── Dialog helpers ──────────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        QMessageBox.information(self, "Inventory", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Inventory", msg)

    def _confirm(self, msg: str) -> bool:
        return QMessageBox.question(
            self, "Inventory", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes


# ─── Item edit dialog ────────────────────────────────────────────────────────

class ItemEditDialog(QDialog):
    """Add or edit a single item. `item_id=None` → create. Else → edit."""

    def __init__(
        self,
        *,
        item_id: Optional[int],
        prefill_barcode: str = "",
        admin_name: str = "admin",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("item_edit_dialog")
        self.setWindowTitle("Add Item" if item_id is None else "Edit Item")
        self.setModal(True)
        self.setMinimumSize(440, 480)
        self._item_id = item_id
        self._admin_name = admin_name
        self._build()
        if item_id is not None:
            self._load_existing()
        elif prefill_barcode:
            self._barcode.setText(prefill_barcode)

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        title = QLabel("Item Details")
        f = QFont(styles.FONT_FAMILY, 14); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)

        self._barcode = QLineEdit()
        self._barcode.setObjectName("item_barcode")
        form.addRow("Barcode:", self._barcode)

        self._name = QLineEdit()
        self._name.setObjectName("item_name")
        form.addRow("Name:", self._name)

        self._price = QLineEdit()
        self._price.setObjectName("item_price")
        self._price.setPlaceholderText("Dollars (e.g. 2.49)")
        form.addRow("Price:", self._price)

        self._dept = QComboBox()
        self._dept.setObjectName("item_dept")
        for d in DEPARTMENTS:
            self._dept.addItem(d["label"], d["id"])
        self._dept.currentIndexChanged.connect(self._on_dept_change)
        form.addRow("Department:", self._dept)

        self._gst = QCheckBox("GST taxable")
        self._gst.setObjectName("item_gst")
        form.addRow("", self._gst)

        self._pst = QCheckBox("PST taxable")
        self._pst.setObjectName("item_pst")
        form.addRow("", self._pst)

        self._deposit = QComboBox()
        self._deposit.setObjectName("item_deposit")
        for d in ["none", "355ml", "1L"]:
            self._deposit.addItem(d, d)
        form.addRow("Bottle deposit:", self._deposit)

        self._age = QCheckBox("Age restricted (18+)")
        self._age.setObjectName("item_age")
        form.addRow("", self._age)

        self._active = QCheckBox("Active")
        self._active.setObjectName("item_active")
        self._active.setChecked(True)
        form.addRow("", self._active)

        v.addLayout(form)

        # Buttons
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setObjectName("item_save")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("item_cancel")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        # Apply default tax for the initially-selected dept
        self._on_dept_change()

    def _on_dept_change(self) -> None:
        # Pre-fill GST/PST/deposit defaults whenever dept changes (only if creating)
        if self._item_id is not None:
            return
        dept_id = self._dept.currentData()
        d = DEPT_TAX_DEFAULTS.get(dept_id, {})
        self._gst.setChecked(bool(d.get("gst", 1)))
        self._pst.setChecked(bool(d.get("pst", 0)))
        idx = self._deposit.findData(d.get("deposit", "none"))
        if idx >= 0:
            self._deposit.setCurrentIndex(idx)

    def _load_existing(self) -> None:
        item = db.get_item(self._item_id)
        if not item:
            QMessageBox.warning(self, "Item", "Item not found.")
            self.reject()
            return
        self._barcode.setText(item["barcode"] or "")
        self._name.setText(item["name"])
        self._price.setText(f"{item['price_cents'] / 100:.2f}")
        idx = self._dept.findData(item["department"])
        if idx >= 0:
            self._dept.setCurrentIndex(idx)
        self._gst.setChecked(bool(item["tax_gst"]))
        self._pst.setChecked(bool(item["tax_pst"]))
        di = self._deposit.findData(item["bottle_deposit"])
        if di >= 0:
            self._deposit.setCurrentIndex(di)
        self._age.setChecked(bool(item["age_restricted"]))
        self._active.setChecked(bool(item["is_active"]))

    def _save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Item", "Name is required.")
            return
        try:
            price_dollars = float(self._price.text().strip())
            if price_dollars < 0:
                raise ValueError
            price_cents = int(round(price_dollars * 100))
        except (ValueError, TypeError):
            QMessageBox.warning(self, "Item", "Price must be a positive number (e.g. 2.49).")
            return

        barcode = self._barcode.text().strip() or None
        dept_id = self._dept.currentData()
        gst = 1 if self._gst.isChecked() else 0
        pst = 1 if self._pst.isChecked() else 0
        deposit = self._deposit.currentData()
        age = 1 if self._age.isChecked() else 0
        active = 1 if self._active.isChecked() else 0

        try:
            if self._item_id is None:
                # Reject duplicate barcode
                if barcode and db.get_item_by_barcode(barcode):
                    QMessageBox.warning(self, "Item", f"Barcode {barcode} already exists.")
                    return
                db.create_item(
                    barcode, name, price_cents, dept_id,
                    gst=gst, pst=pst, deposit=deposit, age_restricted=age,
                )
            else:
                # Update path includes is_active so admin can reactivate by checking the box
                db.update_item(
                    self._item_id, changed_by=self._admin_name,
                    barcode=barcode, name=name, price_cents=price_cents,
                    department=dept_id, tax_gst=gst, tax_pst=pst,
                    bottle_deposit=deposit, age_restricted=age,
                    is_active=active,
                )
            self.accept()
        except Exception as exc:
            log.exception("save item failed")
            QMessageBox.warning(self, "Item", f"Save failed: {exc}")


# ─── Price history dialog ────────────────────────────────────────────────────

class PriceHistoryDialog(QDialog):
    def __init__(self, item: dict, history: list[dict], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("price_history_dialog")
        self.setWindowTitle(f"Price history — {item['name']}")
        self.setMinimumSize(440, 360)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)
        title = QLabel(item["name"])
        tf = QFont(styles.FONT_FAMILY, 14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)
        sub = QLabel(f"Current price: ${item['price_cents'] / 100:.2f}")
        v.addWidget(sub)

        table = QTableWidget()
        table.setObjectName("price_history_table")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Old Price", "New Price", "Changed By", "When"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setRowCount(len(history))
        for ri, h in enumerate(history):
            table.setItem(ri, 0, QTableWidgetItem(f"${h['old_price_cents'] / 100:.2f}"))
            table.setItem(ri, 1, QTableWidgetItem(f"${h['new_price_cents'] / 100:.2f}"))
            table.setItem(ri, 2, QTableWidgetItem(h.get("changed_by") or ""))
            table.setItem(ri, 3, QTableWidgetItem(h.get("changed_at") or ""))
        v.addWidget(table, stretch=1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        v.addWidget(bb)


# ─── Barcode miss dialog ─────────────────────────────────────────────────────

class BarcodeMissDialog(QDialog):
    """Show unknown barcodes seen at scan time. 'Add' opens ItemEditDialog."""

    add_requested = pyqtSignal(str)   # barcode

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("barcode_miss_dialog")
        self.setWindowTitle("Barcode Miss Log")
        self.setMinimumSize(520, 400)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)
        title = QLabel("Unknown Barcodes")
        tf = QFont(styles.FONT_FAMILY, 14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)
        sub = QLabel("Sorted by scan count. Click 'Add' to create the missing item.")
        sub.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        v.addWidget(sub)

        self._table = QTableWidget()
        self._table.setObjectName("barcode_miss_table")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Barcode", "Scans", "Last Seen", "Action"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._table, stretch=1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        v.addWidget(bb)

        self.refresh()

    def refresh(self) -> None:
        rows = db.list_barcode_misses(limit=200)
        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            self._table.setItem(ri, 0, QTableWidgetItem(r["barcode"]))
            self._table.setItem(ri, 1, QTableWidgetItem(str(r["scan_count"])))
            self._table.setItem(ri, 2, QTableWidgetItem(r["last_scanned"] or ""))
            btn = QPushButton("Add")
            btn.setObjectName(f"miss_add_{r['id']}")
            btn.clicked.connect(lambda _ck=False, b=r["barcode"]: self._on_add(b))
            self._table.setCellWidget(ri, 3, btn)

    def _on_add(self, barcode: str) -> None:
        self.add_requested.emit(barcode)
        # Caller will create the item; refresh the miss table visually
        self.refresh()
