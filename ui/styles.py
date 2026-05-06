"""CityLink theme: colors, sizes, full Qt stylesheet (QSS).

Department colors are imported from `core.departments` — single source of truth.
This module deliberately does NOT import PyQt6 so it can be used from tests
or non-UI code paths. UI files build QFont / QApplication themselves and pass
SIZES / get_stylesheet() into Qt.
"""

from __future__ import annotations

from typing import Optional

from core.departments import DEPT_COLORS

FONT_FAMILY: str = "Arial"   # cross-platform sans-serif; QSS adds Segoe UI / Helvetica fallbacks

# ─── Colors ──────────────────────────────────────────────────────────────────

COLORS: dict[str, str] = {
    # Brand (CityLink navy logo)
    "navy":           "#1B3A6B",
    "blue_mid":       "#2E5BA8",
    "blue_light":     "#5B9BD5",
    "bg":             "#F4F6F9",
    "white":          "#FFFFFF",

    # Departments — sourced from core/departments.py (no duplication)
    **DEPT_COLORS,

    # Action buttons
    "btn_cash":       "#27AE60",
    "btn_card":       "#E74C3C",
    "btn_hold":       "#2E5BA8",
    "btn_void":       "#7F8C8D",
    "btn_cancel":     "#E74C3C",
    "btn_lottery_s":  "#6C3483",
    "btn_lottery_p":  "#E67E22",
    "btn_no_sale":    "#546E7A",
    "btn_split":      "#2E5BA8",

    # Status
    "success":        "#27AE60",
    "warning":        "#F39C12",
    "danger":         "#E74C3C",
    "text_dark":      "#1A1A1A",
    "text_muted":     "#7F8C8D",
    "deal_highlight": "#FFF3CD",

    # Cart panel theme (dark navy container with light-card rows)
    "cart_dark":      "#0B1E3F",
    "cart_dark_alt":  "#0F2A56",
    "cart_flash":     "#A9F0BF",
    "category_accent": "#F1C40F",
}

# ─── Sizes (touch-friendly minimums — non-negotiable) ────────────────────────

SIZES: dict[str, tuple] = {
    # button (w, h) in px
    "dept_btn":    (90, 65),
    "numpad_btn":  (70, 60),
    "action_btn":  (110, 65),

    # font (family, size_pt, weight). weight: 'Bold' | 'Normal'
    "font_header": (FONT_FAMILY, 11, "Bold"),
    "font_dept":   (FONT_FAMILY, 11, "Bold"),
    "font_cart":   (FONT_FAMILY, 15, "Normal"),
    "font_total":  (FONT_FAMILY, 22, "Bold"),
    "font_numpad": (FONT_FAMILY, 20, "Bold"),
    "font_action": (FONT_FAMILY, 12, "Bold"),
    "font_amount": (FONT_FAMILY, 34, "Bold"),
}


# ─── Stylesheet (QSS) ────────────────────────────────────────────────────────

def get_stylesheet() -> str:
    """Return full app QSS string. Apply via `app.setStyleSheet(get_stylesheet())`."""
    c = COLORS
    return f"""
    /* Base */
    QWidget {{
        background-color: {c['bg']};
        color: {c['text_dark']};
        font-family: '{FONT_FAMILY}', 'Segoe UI', 'Helvetica Neue', 'Helvetica', sans-serif;
    }}

    /* Default buttons */
    QPushButton {{
        background-color: {c['white']};
        color: {c['text_dark']};
        border: 1px solid {c['blue_mid']};
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {c['blue_light']};
        color: {c['white']};
    }}
    QPushButton:pressed {{
        background-color: {c['blue_mid']};
        color: {c['white']};
    }}
    QPushButton:disabled {{
        background-color: #E0E0E0;
        color: {c['text_muted']};
        border-color: #BDBDBD;
    }}

    /* Header bar */
    QFrame#header, QWidget#header {{
        background-color: {c['navy']};
        color: {c['white']};
        min-height: 48px;
    }}
    QFrame#header QLabel, QWidget#header QLabel {{
        color: {c['white']};
        font-weight: bold;
    }}

    /* Deals banner */
    QFrame#deals_banner, QWidget#deals_banner {{
        background-color: {c['warning']};
        color: {c['text_dark']};
        min-height: 32px;
        padding: 4px 12px;
        font-weight: bold;
    }}
    QFrame#deals_banner QLabel, QWidget#deals_banner QLabel {{
        color: {c['text_dark']};
        background: transparent;
    }}

    /* Cart list */
    QListWidget#cart {{
        background-color: {c['cart_dark']};
        border: 1px solid {c['blue_mid']};
        border-radius: 4px;
        font-size: 15pt;
    }}
    QListWidget#cart::item {{
        padding: 6px 8px;
        border-bottom: 1px solid #E0E0E0;
    }}
    QListWidget#cart::item:selected {{
        background-color: {c['blue_light']};
        color: {c['white']};
    }}

    /* Totals box */
    QLabel#total_label {{
        font-size: 22pt;
        font-weight: bold;
        color: {c['text_dark']};
    }}
    QLabel#total_amount {{
        font-size: 34pt;
        font-weight: bold;
        color: {c['navy']};
    }}

    /* Action button colour variants — set objectName on the button */
    QPushButton#btn_cash       {{ background-color: {c['btn_cash']};      color: {c['white']}; border: none; }}
    QPushButton#btn_card       {{ background-color: {c['btn_card']};      color: {c['white']}; border: none; }}
    QPushButton#btn_hold       {{ background-color: {c['btn_hold']};      color: {c['white']}; border: none; }}
    QPushButton#btn_void       {{ background-color: {c['btn_void']};      color: {c['white']}; border: none; }}
    QPushButton#btn_cancel     {{ background-color: {c['btn_cancel']};    color: {c['white']}; border: none; }}
    QPushButton#btn_lottery_s  {{ background-color: {c['btn_lottery_s']}; color: {c['white']}; border: none; }}
    QPushButton#btn_lottery_p  {{ background-color: {c['btn_lottery_p']}; color: {c['white']}; border: none; }}
    QPushButton#btn_no_sale    {{ background-color: {c['btn_no_sale']};   color: {c['white']}; border: none; }}
    QPushButton#btn_split      {{ background-color: {c['btn_split']};     color: {c['white']}; border: none; }}

    /* Numpad button */
    QPushButton#numpad_btn {{
        font-size: 20pt;
        font-weight: bold;
        min-width: 70px;
        min-height: 60px;
    }}

    /* Status pill (terminal connection dot) */
    QLabel#status_ok    {{ color: {c['success']}; font-weight: bold; }}
    QLabel#status_warn  {{ color: {c['warning']}; font-weight: bold; }}
    QLabel#status_err   {{ color: {c['danger']};  font-weight: bold; }}

    /* Inline messages */
    QLabel.success {{ color: {c['success']}; }}
    QLabel.warning {{ color: {c['warning']}; }}
    QLabel.danger  {{ color: {c['danger']};  }}
    """


def dept_button_qss(dept_id: str) -> str:
    """Return per-department QPushButton QSS. Apply inline: `btn.setStyleSheet(dept_button_qss('candy'))`."""
    color = DEPT_COLORS.get(dept_id)
    if color is None:
        # Fallback for unknown dept (shouldn't happen in production — UI grid is built from DEPARTMENTS).
        color = COLORS["btn_void"]
    return (
        f"QPushButton {{"
        f"  background-color: {color};"
        f"  color: white;"
        f"  border: none;"
        f"  border-radius: 6px;"
        f"  font-weight: bold;"
        f"  font-size: 11pt;"
        f"  min-width: 90px;"
        f"  min-height: 65px;"
        f"}}"
        f"QPushButton:pressed {{"
        f"  background-color: {color};"
        f"  padding: 2px 0 0 2px;"   # subtle press feedback
        f"}}"
    )


def deal_row_qss() -> str:
    """QSS to mark a cart row that has a deal applied. Apply via setStyleSheet on the QListWidgetItem widget."""
    return f"background-color: {COLORS['deal_highlight']};"


# ─── Premium admin / dialog helpers ──────────────────────────────────────────

_ADMIN_BG       = "#F4F6F8"
_ADMIN_CARD_BG  = "#FFFFFF"
_ADMIN_BORDER   = "#E1E4EA"
_ADMIN_HDR_BG   = "#F7F9FC"
_ADMIN_ALT_ROW  = "#FAFBFC"
_ADMIN_HOVER    = "#EEF3FA"
_ADMIN_LBL      = "#5A6573"


def admin_screen_qss() -> str:
    """Light-gray premium admin background + toolbar pill row."""
    c = COLORS
    return (
        f"QWidget#admin_inventory {{ background-color: {_ADMIN_BG}; }}"
        f"QWidget#admin_inventory QLabel#screen_title {{"
        f"  color: {c['navy']}; font-size: 18pt; font-weight: bold;"
        f"  padding: 4px 4px 8px 4px;"
        f"}}"
        f"QWidget#admin_inventory QLineEdit#inventory_search {{"
        f"  background: white; border: 1px solid {_ADMIN_BORDER};"
        f"  border-radius: 8px; padding: 10px 14px; font-size: 12pt;"
        f"  min-height: 22px;"
        f"}}"
        f"QWidget#admin_inventory QLineEdit#inventory_search:focus {{"
        f"  border: 2px solid {c['blue_mid']};"
        f"}}"
    )


def premium_dialog_qss() -> str:
    """Premium dialog body: light-gray bg, white cards, soft borders."""
    c = COLORS
    return (
        f"QDialog {{ background-color: {_ADMIN_BG}; }}"
        f"QFrame#card {{"
        f"  background-color: {_ADMIN_CARD_BG};"
        f"  border: 1px solid {_ADMIN_BORDER};"
        f"  border-radius: 10px;"
        f"}}"
        f"QLabel.formLabel {{"
        f"  color: {_ADMIN_LBL}; font-size: 11pt; font-weight: bold;"
        f"  padding-right: 8px;"
        f"}}"
        f"QLineEdit, QComboBox {{"
        f"  background: white; border: 1px solid #B0BEC5;"
        f"  border-radius: 6px; padding: 8px 12px;"
        f"  min-height: 22px; font-size: 12pt;"
        f"}}"
        f"QLineEdit:focus, QComboBox:focus {{"
        f"  border: 2px solid {c['blue_mid']};"
        f"}}"
        f"QCheckBox {{ font-size: 11pt; spacing: 8px; }}"
        f"QCheckBox::indicator {{ width: 20px; height: 20px; }}"
        f"QTabWidget::pane {{"
        f"  background: {_ADMIN_BG}; border: none; top: -1px;"
        f"}}"
        f"QTabBar::tab {{"
        f"  background: transparent; color: {_ADMIN_LBL};"
        f"  padding: 10px 22px; font-size: 11pt; font-weight: bold;"
        f"  border: none; border-bottom: 2px solid transparent;"
        f"}}"
        f"QTabBar::tab:selected {{"
        f"  color: {c['navy']};"
        f"  border-bottom: 2px solid {c['blue_mid']};"
        f"}}"
        f"QTabBar::tab:disabled {{ color: #B0B7BF; }}"
    )


def premium_table_qss() -> str:
    """Premium table: soft headers, alt rows, padded cells."""
    c = COLORS
    return (
        f"QTableWidget {{"
        f"  background: white; gridline-color: {_ADMIN_BORDER};"
        f"  border: 1px solid {_ADMIN_BORDER}; border-radius: 8px;"
        f"  font-size: 11pt;"
        f"}}"
        f"QTableWidget::item {{ padding: 8px 10px; }}"
        f"QTableWidget::item:selected {{"
        f"  background-color: {c['blue_light']}; color: white;"
        f"}}"
        f"QHeaderView::section {{"
        f"  background-color: {_ADMIN_HDR_BG};"
        f"  color: {_ADMIN_LBL};"
        f"  font-weight: bold; font-size: 11pt;"
        f"  padding: 10px 10px;"
        f"  border: none;"
        f"  border-bottom: 1px solid {_ADMIN_BORDER};"
        f"  border-right: 1px solid {_ADMIN_BORDER};"
        f"}}"
        f"QTableWidget {{ alternate-background-color: {_ADMIN_ALT_ROW}; }}"
    )


def premium_combo_qss() -> str:
    """Premium QComboBox: white field, blue focus, white popup, blue selection."""
    c = COLORS
    return (
        f"QComboBox {{"
        f"  background: white; border: 1px solid #B0BEC5;"
        f"  border-radius: 6px; padding: 8px 30px 8px 12px;"
        f"  min-height: 24px; font-size: 12pt; color: {c['text_dark']};"
        f"}}"
        f"QComboBox:focus {{ border: 2px solid {c['blue_mid']}; }}"
        f"QComboBox::drop-down {{"
        f"  subcontrol-origin: padding; subcontrol-position: top right;"
        f"  width: 28px; border-left: 1px solid {_ADMIN_BORDER};"
        f"}}"
        f"QComboBox QAbstractItemView {{"
        f"  background: white;"
        f"  border: 1px solid {_ADMIN_BORDER};"
        f"  outline: 0;"
        f"  selection-background-color: {c['blue_mid']};"
        f"  selection-color: white;"
        f"  padding: 4px;"
        f"}}"
        f"QComboBox QAbstractItemView::item {{"
        f"  min-height: 36px; padding: 6px 12px; font-size: 12pt;"
        f"  color: {c['text_dark']};"
        f"}}"
        f"QComboBox QAbstractItemView::item:hover {{"
        f"  background: {_ADMIN_HOVER};"
        f"}}"
    )


def pill_button_qss(variant: str = "ghost") -> str:
    """Rounded pill button. variant: primary | success | ghost | danger."""
    c = COLORS
    if variant == "success":
        bg, fg, br = c["btn_cash"], "white", c["btn_cash"]
        hover = "#1F8B4D"
    elif variant == "primary":
        bg, fg, br = c["blue_mid"], "white", c["blue_mid"]
        hover = c["navy"]
    elif variant == "danger":
        bg, fg, br = c["danger"], "white", c["danger"]
        hover = "#C0392B"
    else:  # ghost
        bg, fg, br = "white", c["navy"], _ADMIN_BORDER
        hover = _ADMIN_HOVER
    return (
        f"QPushButton {{"
        f"  background-color: {bg}; color: {fg};"
        f"  border: 1px solid {br}; border-radius: 8px;"
        f"  padding: 9px 18px; font-size: 11pt; font-weight: bold;"
        f"  min-height: 22px;"
        f"}}"
        f"QPushButton:hover {{ background-color: {hover}; color: white; }}"
        f"QPushButton:disabled {{"
        f"  background: #ECEFF1; color: #B0B7BF; border-color: #DDE1E5;"
        f"}}"
    )


def promo_type_tile_qss(color: str) -> str:
    """Large colored tile for launching a new promo of a given type."""
    return (
        f"QPushButton {{"
        f"  background-color: {color}; color: white;"
        f"  border: none; border-radius: 12px;"
        f"  padding: 14px 16px;"
        f"  font-size: 12pt; font-weight: bold;"
        f"  text-align: center; min-height: 76px;"
        f"}}"
        f"QPushButton:pressed {{"
        f"  padding: 16px 14px 12px 18px;"
        f"}}"
    )


def promo_card_qss(active: bool = True, selected: bool = False) -> str:
    """Card style for a single promotion in the dashboard grid."""
    border = COLORS["blue_mid"] if selected else _ADMIN_BORDER
    width = "2px" if selected else "1px"
    bg = "#FFFFFF" if active else "#F4F6F8"
    return (
        f"QFrame {{"
        f"  background-color: {bg};"
        f"  border: {width} solid {border};"
        f"  border-radius: 10px;"
        f"}}"
        f"QFrame:hover {{ border: {width} solid {COLORS['blue_mid']}; }}"
    )


def status_badge_qss(kind: str = "active") -> str:
    """Small pill badge: active | upcoming | expired | inactive."""
    palette = {
        "active":   ("#E6F4EA", "#137333"),
        "upcoming": ("#FEF7E0", "#B26A00"),
        "expired":  ("#FCE8E6", "#C5221F"),
        "inactive": ("#ECEFF1", "#5A6573"),
    }.get(kind, ("#ECEFF1", "#5A6573"))
    bg, fg = palette
    return (
        f"QLabel {{"
        f"  background-color: {bg}; color: {fg};"
        f"  border-radius: 9px; padding: 2px 10px;"
        f"  font-size: 9pt; font-weight: bold;"
        f"}}"
    )


def dialog_titlebar_qss() -> str:
    """Navy title band for premium dialogs. Apply to QFrame#dialogTitle."""
    c = COLORS
    return (
        f"QFrame#dialogTitle {{"
        f"  background-color: {c['navy']};"
        f"  border-top-left-radius: 4px; border-top-right-radius: 4px;"
        f"  min-height: 44px;"
        f"}}"
        f"QFrame#dialogTitle QLabel {{"
        f"  color: white; font-size: 14pt; font-weight: bold;"
        f"  padding: 8px 16px; background: transparent;"
        f"}}"
    )


def get_font_tuple(key: str) -> Optional[tuple]:
    """Return SIZES[key] if it's a font tuple, else None.

    UI code: `family, pt, weight = get_font_tuple('font_total')` → `QFont(family, pt, QFont.Weight.Bold if weight=='Bold' else QFont.Weight.Normal)`.
    """
    val = SIZES.get(key)
    if val is None or len(val) != 3:
        return None
    return val
