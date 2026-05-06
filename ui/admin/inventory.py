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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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
    QTabWidget,
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
        self.setObjectName("admin_inventory")
        self.setStyleSheet(styles.admin_screen_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Pricebook Management")
        title.setObjectName("screen_title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        back = QPushButton("Back")
        back.setObjectName("inventory_back")
        back.setStyleSheet(styles.pill_button_qss("ghost"))
        back.setMinimumHeight(40)
        back.clicked.connect(self.back_requested.emit)
        title_row.addWidget(back)
        root.addLayout(title_row)

        # Toolbar — search + dept filter + actions
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search = QLineEdit()
        self._search.setObjectName("inventory_search")
        self._search.setProperty("touchKeyboard", "text")
        self._search.setPlaceholderText("Search items by name or UPC…")
        # Debounce to avoid running search_items on every keystroke (slow on
        # large catalogs).
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self.refresh)
        self._search.textChanged.connect(lambda _t: self._search_debounce.start())
        self._search.returnPressed.connect(self.refresh)
        bar.addWidget(self._search, stretch=1)

        self._dept_filter = QComboBox()
        self._dept_filter.setObjectName("inventory_dept_filter")
        self._dept_filter.setStyleSheet(styles.premium_combo_qss())
        self._dept_filter.setMinimumHeight(42)
        self._dept_filter.setMinimumWidth(180)
        self._dept_filter.addItem("All Departments", "")
        for d in DEPARTMENTS:
            self._dept_filter.addItem(d["label"], d["id"])
        self._dept_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        bar.addWidget(self._dept_filter)

        for label, name, slot, variant in [
            ("+ Add Item",      "inv_btn_add",     self._on_add,           "success"),
            ("Import",          "inv_btn_import",  self._on_import_csv,    "ghost"),
            ("Export Labels",   "inv_btn_export",  self._on_export_labels, "ghost"),
            ("Barcode Misses",  "inv_btn_misses",  self._on_misses,        "ghost"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(42)
            b.setStyleSheet(styles.pill_button_qss(variant))
            b.clicked.connect(slot)
            bar.addWidget(b)

        # Multi-select placeholder (disabled until bulk-edit is built)
        ms = QPushButton("Multi-select  ▾")
        ms.setObjectName("inv_btn_multiselect")
        ms.setMinimumHeight(42)
        ms.setStyleSheet(styles.pill_button_qss("ghost"))
        ms.setEnabled(False)
        bar.addWidget(ms)

        root.addLayout(bar)

        # Item table
        self._table = QTableWidget()
        self._table.setObjectName("inventory_table")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["UPC / PLU", "Description", "Department", "Price", "GST", "PST", "Active"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setStyleSheet(styles.premium_table_qss())
        self._table.itemDoubleClicked.connect(lambda _it: self._on_edit_selected())
        root.addWidget(self._table, stretch=1)

        # Bottom action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for label, name, slot, variant in [
            ("Edit Selected",       "inv_btn_edit",     self._on_edit_selected,        "primary"),
            ("Price History",       "inv_btn_history",  self._on_price_history,        "ghost"),
            ("Deactivate Selected", "inv_btn_deact",    self._on_deactivate_selected,  "ghost"),
        ]:
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumHeight(40)
            b.setStyleSheet(styles.pill_button_qss(variant))
            b.clicked.connect(slot)
            action_row.addWidget(b)
        action_row.addStretch(1)
        root.addLayout(action_row)

    # ─── Refresh / search ────────────────────────────────────────────────────

    def refresh(self) -> None:
        q = self._search.text().strip() if hasattr(self, "_search") else ""
        if q:
            rows = db.search_items(q, active_only=False)
        else:
            rows = db.list_all_items(active_only=False)
        # Apply department filter (no DB-level filter helper; client-side is fine
        # for store-scale catalogs, ~thousands of items).
        if hasattr(self, "_dept_filter"):
            dept_id = self._dept_filter.currentData()
            if dept_id:
                rows = [r for r in rows if r["department"] == dept_id]
        if not rows:
            self._table.setRowCount(1)
            it = QTableWidgetItem("No results")
            it.setForeground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(styles.COLORS["text_muted"]))
            self._table.setItem(0, 1, it)
            for c in (0, 2, 3, 4, 5, 6):
                self._table.setItem(0, c, QTableWidgetItem(""))
            return
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
        self.setMinimumSize(620, 640)
        self.resize(640, 700)
        self._item_id = item_id
        self._admin_name = admin_name
        self._build()
        if item_id is not None:
            self._load_existing()
        elif prefill_barcode:
            self._barcode.setText(prefill_barcode)
            # Scanner-flow: barcode came from register; jump cursor straight
            # to description so cashier types item name without re-tapping.
            self._name.setFocus()

    def _build(self) -> None:
        title_text = "Add Pricebook Item" if self._item_id is None else "Edit Pricebook Item"

        # Premium dialog QSS + title bar styling
        self.setStyleSheet(
            styles.premium_dialog_qss()
            + styles.dialog_titlebar_qss()
            + "QLineEdit#item_barcode { font-size: 14pt; padding: 10px 12px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Title bar (navy band) ──
        title_bar = QFrame()
        title_bar.setObjectName("dialogTitle")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel(title_text)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb_layout.addWidget(title_lbl)
        outer.addWidget(title_bar)

        # ── Tabs ──
        tabs = QTabWidget()
        tabs.setObjectName("item_tabs")
        outer.addWidget(tabs, stretch=1)

        basic = self._build_basic_tab()
        tabs.addTab(basic, "Basic Info")

        # Placeholder tab — keeps door open for combo/multi-pack pricing.
        placeholder = QWidget()
        pv = QVBoxLayout(placeholder)
        pv.addStretch(1)
        ph = QLabel("Quantity / Combo pricing — coming soon.")
        ph.setStyleSheet("color: #8A8F95; font-size: 12pt;")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv.addWidget(ph)
        pv.addStretch(1)
        tabs.addTab(placeholder, "Quantity Choices")
        tabs.setTabEnabled(1, False)

        # ── Status banner ──
        self._status_banner = QLabel("")
        self._status_banner.setObjectName("item_status_banner")
        self._status_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_banner.setMinimumHeight(28)
        self._status_banner.setStyleSheet("background: transparent; padding: 0 18px;")
        self._status_banner.hide()
        outer.addWidget(self._status_banner)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(18, 10, 18, 14)
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("item_cancel")
        cancel.setMinimumSize(140, 44)
        cancel.setStyleSheet(styles.pill_button_qss("ghost"))
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("item_save")
        save.setMinimumSize(160, 44)
        save.setDefault(True)
        save.setStyleSheet(styles.pill_button_qss("success"))
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        outer.addLayout(btn_row)

        # Worker thread state for UPC online lookup.
        self._upc_thread = None
        self._upc_worker = None

        self._on_dept_change()
        # Auto-focus: barcode for fresh entry, name when prefilled by scanner
        # (handled in __init__).
        self._barcode.setFocus()

    def _build_basic_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(18, 14, 18, 12)
        page_layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 18, 22, 18)
        cv.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)

        # UPC
        self._barcode = QLineEdit()
        self._barcode.setObjectName("item_barcode")
        self._barcode.setProperty("touchKeyboard", "num")
        self._barcode.setPlaceholderText("Scan or type UPC, then press Enter")
        self._barcode.returnPressed.connect(self._on_barcode_enter)
        form.addRow(self._fl("UPC:"), self._barcode)

        # Description + Size on one row
        desc_row = QHBoxLayout()
        desc_row.setSpacing(12)
        self._name = QLineEdit()
        self._name.setObjectName("item_name")
        self._name.setProperty("touchKeyboard", "text")
        self._name.setPlaceholderText("Item description")
        desc_row.addWidget(self._name, stretch=3)
        size_lbl = QLabel("Size:")
        size_lbl.setObjectName("formLabel")
        size_lbl.setProperty("class", "formLabel")
        desc_row.addWidget(size_lbl)
        self._size = QLineEdit()
        self._size.setObjectName("item_size")
        self._size.setProperty("touchKeyboard", "text")
        self._size.setPlaceholderText("e.g. 355ml")
        self._size.setMaximumWidth(160)
        desc_row.addWidget(self._size, stretch=1)
        form.addRow(self._fl("Item Desc:"), desc_row)

        # Department
        self._dept = QComboBox()
        self._dept.setObjectName("item_dept")
        self._dept.setStyleSheet(styles.premium_combo_qss())
        self._dept.setMinimumHeight(40)
        for d in DEPARTMENTS:
            self._dept.addItem(d["label"], d["id"])
        self._dept.currentIndexChanged.connect(self._on_dept_change)
        form.addRow(self._fl("Department:"), self._dept)

        # Price
        self._price = QLineEdit()
        self._price.setObjectName("item_price")
        self._price.setProperty("touchKeyboard", "num")
        self._price.setPlaceholderText("Dollars (e.g. 2.49)")
        self._price.returnPressed.connect(self._save)
        form.addRow(self._fl("Price:"), self._price)

        # Tax row (GST + PST inline)
        tax_row = QHBoxLayout()
        tax_row.setSpacing(24)
        self._gst = QCheckBox("GST taxable")
        self._gst.setObjectName("item_gst")
        tax_row.addWidget(self._gst)
        self._pst = QCheckBox("PST taxable")
        self._pst.setObjectName("item_pst")
        tax_row.addWidget(self._pst)
        tax_row.addStretch(1)
        form.addRow(self._fl("Tax:"), tax_row)

        # Bottle deposit
        self._deposit = QComboBox()
        self._deposit.setObjectName("item_deposit")
        self._deposit.setStyleSheet(styles.premium_combo_qss())
        self._deposit.setMinimumHeight(40)
        for d in ["none", "355ml", "1L"]:
            self._deposit.addItem(d, d)
        form.addRow(self._fl("Bottle Deposit:"), self._deposit)

        # Toggle row (Age + Active)
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(24)
        self._age = QCheckBox("Age restricted (18+)")
        self._age.setObjectName("item_age")
        toggle_row.addWidget(self._age)
        self._active = QCheckBox("Active")
        self._active.setObjectName("item_active")
        self._active.setChecked(True)
        toggle_row.addWidget(self._active)
        toggle_row.addStretch(1)
        form.addRow(self._fl(""), toggle_row)

        cv.addLayout(form)
        page_layout.addWidget(card)
        page_layout.addStretch(1)
        return page

    @staticmethod
    def _fl(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("formLabel")
        lbl.setProperty("class", "formLabel")
        lbl.setMinimumWidth(120)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    # ─── Status banner helpers ───────────────────────────────────────────────

    def _set_banner(self, text: str, kind: str) -> None:
        """kind: 'info' | 'ok' | 'warn' | 'err'."""
        palette = {
            "info": ("#E8F0FE", "#1B3A6B"),
            "ok":   ("#E6F4EA", "#137333"),
            "warn": ("#FEF7E0", "#B26A00"),
            "err":  ("#FCE8E6", "#C5221F"),
        }.get(kind, ("#EEEEEE", "#333"))
        bg, fg = palette
        self._status_banner.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
            f" border-radius: 6px; padding: 4px 10px;"
        )
        self._status_banner.setText(text)
        self._status_banner.show()

    def _hide_banner(self) -> None:
        self._status_banner.hide()
        self._status_banner.setText("")

    # ─── UPC autofill flow ───────────────────────────────────────────────────

    def _on_barcode_enter(self) -> None:
        bc = self._barcode.text().strip()
        if not bc:
            return
        # Editing existing item — Enter on barcode is a no-op (no auto-lookup).
        if self._item_id is not None:
            self._price.setFocus()
            return
        # Step 1: local resolution (DB item OR cached UPC).
        try:
            from core.upc import lookup_local, auto_map_department, tax_defaults_for_dept
            existing, cached = lookup_local(bc)
        except Exception:
            log.exception("lookup_local failed")
            existing, cached = (None, None)
        if existing is not None:
            self._handle_existing_item(existing)
            return
        if cached is not None:
            self._apply_lookup_result(cached, source_label="cache")
            return
        # Step 2: online lookup on a worker thread (UI stays responsive).
        self._set_banner("⏳ Looking up UPC online…", "info")
        self._spawn_upc_worker(bc)

    def _spawn_upc_worker(self, barcode: str) -> None:
        # Cancel previous worker if any.
        try:
            if self._upc_thread is not None and self._upc_thread.isRunning():
                self._upc_thread.quit()
                self._upc_thread.wait(500)
        except Exception:
            pass
        from core.upc import UpcLookupWorker
        from PyQt6.QtCore import QThread
        self._upc_thread = QThread(self)
        self._upc_worker = UpcLookupWorker(barcode)
        self._upc_worker.moveToThread(self._upc_thread)
        self._upc_thread.started.connect(self._upc_worker.run)
        self._upc_worker.finished.connect(self._on_upc_finished)
        self._upc_worker.finished.connect(self._upc_thread.quit)
        self._upc_thread.finished.connect(self._upc_thread.deleteLater)
        self._upc_thread.start()

    def _on_upc_finished(self, barcode: str, result) -> None:
        if result is None:
            self._set_banner(
                "⚠ UPC not found — enter manually",
                "warn",
            )
            self._name.setFocus()
            return
        self._apply_lookup_result(result, source_label=getattr(result, "source", "online"))

    def _apply_lookup_result(self, result, *, source_label: str = "online") -> None:
        # Fill name only if empty (don't clobber manual input mid-typing).
        if not self._name.text().strip():
            label = result.name
            if getattr(result, "brand", "") and result.brand.lower() not in label.lower():
                label = f"{result.brand} {result.name}".strip()
            if getattr(result, "quantity", ""):
                label = f"{label} {result.quantity}".strip()
            self._name.setText(label)
        # Auto-map department from name + category hint.
        from core.upc import auto_map_department, tax_defaults_for_dept
        dept = auto_map_department(result.name, getattr(result, "category", ""))
        if dept:
            idx = self._dept.findData(dept)
            if idx >= 0:
                self._dept.setCurrentIndex(idx)
                gst, pst = tax_defaults_for_dept(dept)
                self._gst.setChecked(gst); self._pst.setChecked(pst)
        self._set_banner(f"✓ Match found ({source_label}) — set price + Save", "ok")
        # Hand focus to price for fast entry.
        self._price.setFocus()

    def _handle_existing_item(self, item: dict) -> None:
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Existing Item")
        msg.setText(
            f"Barcode already exists:\n\n"
            f"{item['name']}  ·  ${item['price_cents']/100:.2f}\n"
            f"Department: {item['department']}"
        )
        load = msg.addButton("Load Existing", QMessageBox.ButtonRole.AcceptRole)
        new = msg.addButton("Create New (different barcode)",
                            QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(load)
        msg.exec()
        if msg.clickedButton() is load:
            self._item_id = int(item["id"])
            self.setWindowTitle("Edit Item")
            self._load_existing()
            self._set_banner("✓ Loaded existing item — edit and Save", "ok")
        else:
            # User wants different barcode — clear it for re-scan.
            self._barcode.clear(); self._barcode.setFocus()
            self._hide_banner()

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
            QMessageBox.warning(self, "Item", "Description is required.")
            return
        # Size is cosmetic — appended to name on create only (DB has no
        # `size` column; we avoid schema churn per project rules).
        size = self._size.text().strip() if hasattr(self, "_size") else ""
        if size and self._item_id is None and size.lower() not in name.lower():
            name = f"{name} {size}".strip()
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

    def closeEvent(self, ev) -> None:
        # Cancel any in-flight UPC worker thread + close on-screen keyboard.
        try:
            if self._upc_thread is not None and self._upc_thread.isRunning():
                self._upc_thread.quit()
                self._upc_thread.wait(500)
        except Exception:
            pass
        try:
            from ui.cashier.touch_keyboard import close_active_keyboard
            close_active_keyboard()
        except Exception:
            pass
        super().closeEvent(ev)


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
