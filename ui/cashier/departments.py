"""Scrollable department button grid.

15 colored department buttons + a leading "ALL" button (default selection,
indicates scan/search mode for the register). Two rows visible; rest scrolls.

Selection is mutually exclusive — clicking one deselects all others. The
selected button gets a 2px white border. Emits `dept_selected(str)` with the
department id (or "all" for the catch-all option).

Wired by the register screen: when a dept is selected, the manual-price
entry routes the next price-keyed item into that department (using
`core/cart.py:add_manual()`'s DEPT_TAX_DEFAULTS).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QGridLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.departments import DEPARTMENTS
from core.logger import get_logger
from ui import styles

log = get_logger("ui.dept_grid")

ALL_ID = "all"
ALL_LABEL = "ALL"
COLUMNS = 6
VISIBLE_ROWS = 2


class DepartmentGrid(QWidget):
    """Scrollable grid of dept buttons + leading ALL button.

    Optional `dept_ids` filters/orders which depts appear (None = all 15).
    `columns` overrides COLUMNS. `button_size` / `font_pt` override per-button sizing.
    """

    dept_selected = pyqtSignal(str)   # dept id, or ALL_ID

    def __init__(
        self,
        *,
        dept_ids: Optional[list[str]] = None,
        columns: int = COLUMNS,
        button_size: Optional[tuple[int, int]] = None,
        font_pt: int = 11,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("dept_grid")
        self._buttons: dict[str, QPushButton] = {}
        self._selected_id: Optional[str] = None
        self._btn_size: tuple[int, int] = button_size or styles.SIZES["dept_btn"]
        self._font_pt: int = font_pt
        self._columns: int = columns
        self._dept_ids: Optional[list[str]] = dept_ids
        self._build_ui()
        self._select(ALL_ID, emit=False)   # default; no signal at construction

    # ─── construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("dept_grid_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        container.setObjectName("dept_grid_container")

        grid = QGridLayout(container)
        grid.setSpacing(8)
        grid.setContentsMargins(4, 4, 4, 4)

        cols = self._columns

        # Slot 0 = ALL
        all_btn = self._make_button(ALL_ID, ALL_LABEL, color=styles.COLORS["navy"])
        grid.addWidget(all_btn, 0, 0)
        self._buttons[ALL_ID] = all_btn

        # Filter dept set by id list if provided (preserves DEPARTMENTS order)
        if self._dept_ids is not None:
            wanted = set(self._dept_ids)
            depts = [d for d in DEPARTMENTS if d["id"] in wanted]
        else:
            depts = list(DEPARTMENTS)

        for i, d in enumerate(depts):
            btn = self._make_button(d["id"], d["label"], color=d["color"])
            slot = i + 1
            r, c = divmod(slot, cols)
            grid.addWidget(btn, r, c)
            self._buttons[d["id"]] = btn

        scroll.setWidget(container)

        # Auto-size viewport to fit exactly all rows of buttons we built (no scroll needed
        # for the compact 3×3 register layout). For full-catalog usage falls back to
        # VISIBLE_ROWS visible height.
        btn_w, btn_h = self._btn_size
        total = 1 + len(depts)
        rows_needed = (total + cols - 1) // cols
        if self._dept_ids is None:
            visible = VISIBLE_ROWS
        else:
            visible = rows_needed
        viewport_h = visible * btn_h + max(0, visible - 1) * 8 + 4 * 2
        scroll.setMinimumHeight(viewport_h)
        scroll.setMaximumHeight(viewport_h)

        # Hide scrollbar arrows; keep the slider only.
        scroll.setStyleSheet(
            "QScrollBar:vertical { background: transparent; width: 10px; }"
            "QScrollBar::handle:vertical { background: #B0BEC5; border-radius: 4px; min-height: 24px; }"
            "QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {"
            "  height: 0px; background: none; border: none;"
            "}"
            "QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {"
            "  height: 0px; width: 0px; background: none; border: none;"
            "}"
        )

        outer.addWidget(scroll)

    def _make_button(self, dept_id: str, label: str, *, color: str) -> QPushButton:
        b = QPushButton(label)
        b.setObjectName(f"dept_btn_{dept_id}")
        w, h = self._btn_size
        b.setMinimumSize(w, h)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(styles.FONT_FAMILY, self._font_pt)
        f.setBold(True)
        b.setFont(f)
        b.setProperty("dept_color", color)
        b.setProperty("selected", False)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style(b)
        b.clicked.connect(lambda _checked=False, x=dept_id: self._select(x))
        return b

    @staticmethod
    def _apply_style(b: QPushButton) -> None:
        color = b.property("dept_color")
        selected = bool(b.property("selected"))
        border = "2px solid white" if selected else "2px solid transparent"
        b.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {color};"
            f"  color: white;"
            f"  border: {border};"
            f"  border-radius: 6px;"
            f"  padding: 4px;"
            f"  font-weight: bold;"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {color};"
            f"  padding: 6px 4px 2px 6px;"
            f"}}"
        )

    # ─── selection ──────────────────────────────────────────────────────────

    def _select(self, dept_id: str, *, emit: bool = True) -> None:
        if dept_id not in self._buttons:
            log.warning("ignoring select for unknown dept: %s", dept_id)
            return
        if self._selected_id is not None and self._selected_id != dept_id:
            prev = self._buttons.get(self._selected_id)
            if prev is not None:
                prev.setProperty("selected", False)
                self._apply_style(prev)
        new_btn = self._buttons[dept_id]
        new_btn.setProperty("selected", True)
        self._apply_style(new_btn)
        self._selected_id = dept_id
        if emit:
            self.dept_selected.emit(dept_id)

    # ─── public API ─────────────────────────────────────────────────────────

    @property
    def selected_id(self) -> Optional[str]:
        return self._selected_id

    def select(self, dept_id: str) -> None:
        """Programmatic selection. Raises ValueError if unknown."""
        if dept_id not in self._buttons:
            raise ValueError(f"unknown department: {dept_id!r}")
        self._select(dept_id)

    def reset_to_all(self) -> None:
        """Convenience: select the ALL catch-all option."""
        self._select(ALL_ID)
