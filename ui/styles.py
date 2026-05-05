"""CityLink theme: colors, sizes, full Qt stylesheet (QSS).

Department colors are imported from `core.departments` — single source of truth.
This module deliberately does NOT import PyQt6 so it can be used from tests
or non-UI code paths. UI files build QFont / QApplication themselves and pass
SIZES / get_stylesheet() into Qt.
"""

from __future__ import annotations

from typing import Optional

from core.departments import DEPT_COLORS

FONT_FAMILY: str = "Segoe UI"   # Windows-native; falls back via fontFamily list in QSS

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
    "font_cart":   (FONT_FAMILY, 13, "Normal"),
    "font_total":  (FONT_FAMILY, 18, "Bold"),
    "font_numpad": (FONT_FAMILY, 20, "Bold"),
    "font_action": (FONT_FAMILY, 12, "Bold"),
    "font_amount": (FONT_FAMILY, 28, "Bold"),
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
        font-family: '{FONT_FAMILY}', 'Helvetica Neue', 'Helvetica', sans-serif;
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
        background-color: {c['white']};
        border: 1px solid {c['blue_mid']};
        border-radius: 4px;
        font-size: 13pt;
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
        font-size: 18pt;
        font-weight: bold;
        color: {c['text_dark']};
    }}
    QLabel#total_amount {{
        font-size: 28pt;
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


def get_font_tuple(key: str) -> Optional[tuple]:
    """Return SIZES[key] if it's a font tuple, else None.

    UI code: `family, pt, weight = get_font_tuple('font_total')` → `QFont(family, pt, QFont.Weight.Bold if weight=='Bold' else QFont.Weight.Normal)`.
    """
    val = SIZES.get(key)
    if val is None or len(val) != 3:
        return None
    return val
