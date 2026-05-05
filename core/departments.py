"""Department catalog. Single source of truth for id, label, color, tax defaults.

Imported by: core/cart.py (tax defaults for manual entry), ui/styles.py (color map),
ui/cashier/departments.py (button grid).
"""

from __future__ import annotations

from typing import TypedDict


class DeptDef(TypedDict):
    id: str
    label: str
    color: str           # hex, e.g. "#FF6B6B"
    gst: int             # 0 or 1 (matches DB schema)
    pst: int             # 0 or 1
    deposit: str         # 'none' | '355ml' | '1L'


DEPARTMENTS: list[DeptDef] = [
    {"id": "candy",          "label": "Candy",          "color": "#FF6B6B", "gst": 1, "pst": 1, "deposit": "none"},
    {"id": "drinks",         "label": "Drinks",         "color": "#4A90D9", "gst": 1, "pst": 0, "deposit": "355ml"},
    {"id": "carbonated",     "label": "Carbonated",     "color": "#E74C3C", "gst": 1, "pst": 1, "deposit": "355ml"},
    {"id": "non_carbonated", "label": "Non-Carbonated", "color": "#E67E22", "gst": 1, "pst": 0, "deposit": "355ml"},
    {"id": "snacks",         "label": "Snacks",         "color": "#F39C12", "gst": 1, "pst": 1, "deposit": "none"},
    {"id": "confectionery",  "label": "Confectionery",  "color": "#D4AC0D", "gst": 1, "pst": 1, "deposit": "none"},
    {"id": "medicine",       "label": "Medicine",       "color": "#27AE60", "gst": 1, "pst": 0, "deposit": "none"},  # OTC: GST yes, PST no (BC)
    {"id": "stationary",     "label": "Stationary",     "color": "#8E44AD", "gst": 1, "pst": 0, "deposit": "none"},
    {"id": "gift_items",     "label": "Gift Items",     "color": "#E91E8C", "gst": 1, "pst": 0, "deposit": "none"},
    {"id": "gift_cards",     "label": "Gift Cards",     "color": "#16A085", "gst": 0, "pst": 0, "deposit": "none"},
    {"id": "ice_cream",      "label": "Ice Cream",      "color": "#FF8C42", "gst": 1, "pst": 1, "deposit": "none"},
    {"id": "slush",          "label": "Slush",          "color": "#00BCD4", "gst": 1, "pst": 1, "deposit": "none"},
    {"id": "lottery",        "label": "Lottery",        "color": "#6C3483", "gst": 0, "pst": 0, "deposit": "none"},
    {"id": "accessories",    "label": "Accessories",    "color": "#546E7A", "gst": 1, "pst": 0, "deposit": "none"},
    {"id": "retail",         "label": "Retail",         "color": "#1B6B3A", "gst": 1, "pst": 0, "deposit": "none"},
]

# id → full def (lookup convenience)
DEPT_BY_ID: dict[str, DeptDef] = {d["id"]: d for d in DEPARTMENTS}

# id → tax defaults only (consumed by cart.add_manual)
DEPT_TAX_DEFAULTS: dict[str, dict] = {
    d["id"]: {"gst": d["gst"], "pst": d["pst"], "deposit": d["deposit"]}
    for d in DEPARTMENTS
}

# id → color (consumed by ui/styles.py)
DEPT_COLORS: dict[str, str] = {d["id"]: d["color"] for d in DEPARTMENTS}


def get_dept(dept_id: str) -> DeptDef:
    """Return department definition by id. Raises KeyError if unknown."""
    try:
        return DEPT_BY_ID[dept_id]
    except KeyError:
        raise KeyError(f"unknown department: {dept_id!r}")
