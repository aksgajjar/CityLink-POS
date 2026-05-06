"""UPC lookup pipeline.

Order of resolution:
  1. Local items table (via db.get_item_by_barcode) — already-stocked SKU.
  2. Local upc_cache table (db.get_upc_cache)         — previously fetched online.
  3. Online providers (currently OpenFoodFacts only)  — fetched off the UI thread.

Public API:
    lookup_local(barcode)       — instant, uses items + upc_cache only.
    lookup_online_sync(barcode) — blocking HTTPS; call from a QThread.
    auto_map_department(name)   — keyword → dept id (single source of truth).
    tax_defaults_for_dept(d)    — (gst_bool, pst_bool) per BC defaults.
    UpcLookupWorker             — QObject worker for QThread network calls.

Provider architecture is open for extension: subclass `UpcProvider`, add to
`PROVIDERS` list. No API keys required for the default OpenFoodFacts provider.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core import db
from core.logger import get_logger

log = get_logger("core.upc")


# ─── Result type ─────────────────────────────────────────────────────────────

@dataclass
class UpcResult:
    """Normalized product info returned by any provider."""
    name: str = ""
    brand: str = ""
    category: str = ""
    quantity: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        return bool(self.name)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "brand": self.brand,
            "category": self.category,
            "quantity": self.quantity,
            "source": self.source,
        }


# ─── Department keyword map (auto-categorize) ────────────────────────────────

# Order matters: more specific keywords first. Extend as needed; depts must
# match ids in core/departments.py.
_DEPT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("medicine",       ["tylenol", "advil", "aspirin", "ibuprofen",
                        "acetaminophen", "antacid", "cough", "cold relief",
                        "pain reliever", "bandage", "first aid"]),
    ("lottery",        ["lotto", "lottery", "scratch", "ticket"]),
    ("ice_cream",      ["ice cream", "ice-cream", "popsicle", "frozen dessert"]),
    ("slush",          ["slush", "slushy", "slurpee"]),
    ("carbonated",     ["coke", "pepsi", "soda", "sprite", "fanta",
                        "ginger ale", "root beer", "carbonated"]),
    ("non_carbonated", ["water", "juice", "iced tea", "gatorade",
                        "powerade", "lemonade", "smoothie"]),
    ("drinks",         ["drink", "beverage", "redbull", "red bull",
                        "energy drink", "coffee", "tea"]),
    ("candy",          ["candy", "chocolate", "kitkat", "kit kat",
                        "snickers", "m&m", "twix", "reese", "skittles",
                        "gum", "mint", "lollipop", "licorice"]),
    ("confectionery",  ["cookie", "biscuit", "wafer", "cake"]),
    ("snacks",         ["chip", "chips", "snack", "popcorn", "nuts",
                        "pretzel", "cracker", "jerky"]),
    ("stationary",     ["pen", "pencil", "notebook", "paper", "marker"]),
    ("gift_cards",     ["gift card", "giftcard"]),
]


def auto_map_department(name: str, category_hint: str = "") -> Optional[str]:
    """Return a department id based on keyword match. None if no match."""
    haystack = f"{name or ''} {category_hint or ''}".lower()
    if not haystack.strip():
        return None
    for dept_id, keywords in _DEPT_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return dept_id
    return None


def tax_defaults_for_dept(dept_id: str) -> tuple[bool, bool]:
    """BC defaults: (gst, pst). Sourced from core/departments.py DEPT_TAX_DEFAULTS
    when available, with sensible fallbacks for the keyword-only path.
    """
    try:
        from core.departments import DEPT_TAX_DEFAULTS
        d = DEPT_TAX_DEFAULTS.get(dept_id)
        if d is not None:
            return (bool(d.get("gst", 1)), bool(d.get("pst", 0)))
    except Exception:
        pass
    if dept_id == "medicine":     return (True, False)
    if dept_id == "lottery":      return (False, False)
    if dept_id == "gift_cards":   return (False, False)
    if dept_id in {"snacks", "candy", "carbonated", "non_carbonated",
                   "drinks", "ice_cream", "slush", "confectionery"}:
        return (True, True)
    return (True, False)


# ─── Provider abstraction ────────────────────────────────────────────────────

class UpcProvider:
    """Pluggable provider interface. Implement `fetch(barcode)`."""
    name: str = "base"
    timeout: float = 4.0

    def fetch(self, barcode: str) -> Optional[UpcResult]:
        raise NotImplementedError


class OpenFoodFactsProvider(UpcProvider):
    """Free, no key. https://world.openfoodfacts.org/data — best for grocery."""

    name = "openfoodfacts"
    URL = "https://world.openfoodfacts.org/api/v2/product/{bc}.json"
    USER_AGENT = "CityLinkPOS/1.0 (POS app)"

    def fetch(self, barcode: str) -> Optional[UpcResult]:
        if not barcode or not barcode.isdigit():
            return None
        url = self.URL.format(bc=barcode)
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            log.warning("OpenFoodFacts fetch failed for %s: %s", barcode, e)
            return None
        except Exception:
            log.exception("OpenFoodFacts fetch exception for %s", barcode)
            return None
        if payload.get("status") != 1:
            return None
        prod = payload.get("product") or {}
        name = (prod.get("product_name") or prod.get("product_name_en")
                or prod.get("generic_name") or "").strip()
        if not name:
            return None
        return UpcResult(
            name=name,
            brand=(prod.get("brands") or "").split(",")[0].strip(),
            category=(prod.get("categories") or "").split(",")[0].strip(),
            quantity=(prod.get("quantity") or "").strip(),
            source=self.name,
            extra={"raw_categories": prod.get("categories")},
        )


PROVIDERS: list[UpcProvider] = [OpenFoodFactsProvider()]


# ─── Public entry points ─────────────────────────────────────────────────────

def lookup_local(barcode: str) -> tuple[Optional[dict], Optional[UpcResult]]:
    """Resolve barcode without network.

    Returns (existing_item_row, cached_upc_result). Either may be None.
    Existing item takes precedence — caller should offer Load Existing.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return (None, None)
    existing = None
    try:
        existing = db.get_item_by_barcode(barcode)
    except Exception:
        log.exception("get_item_by_barcode failed")
    cached_dict = None
    try:
        cached_dict = db.get_upc_cache(barcode)
    except Exception:
        log.exception("get_upc_cache failed")
    cached: Optional[UpcResult] = None
    if cached_dict:
        cached = UpcResult(
            name=cached_dict.get("name") or "",
            brand=cached_dict.get("brand") or "",
            category=cached_dict.get("category") or "",
            quantity=cached_dict.get("quantity") or "",
            source=cached_dict.get("source") or "cache",
        )
    return (existing, cached)


def lookup_online_sync(barcode: str) -> Optional[UpcResult]:
    """Iterate providers in order. First valid hit wins. Caches result."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None
    for prov in PROVIDERS:
        try:
            result = prov.fetch(barcode)
        except Exception:
            log.exception("provider %s raised", prov.name)
            continue
        if result and result.is_valid():
            try:
                db.cache_upc_result(barcode, result.to_dict(), source=prov.name)
            except Exception:
                log.exception("cache_upc_result failed")
            return result
    return None


# ─── QThread worker (non-blocking UI) ────────────────────────────────────────

class UpcLookupWorker(QObject):
    """Move to a QThread; emit `finished(barcode, result_or_None)`.

    Keeps the UI responsive during network calls. UI connects to signals,
    starts the thread, and acts on `finished`. `not_found` is emitted when
    no provider returned a result.
    """

    finished = pyqtSignal(str, object)   # (barcode, UpcResult or None)
    not_found = pyqtSignal(str)          # (barcode)

    def __init__(self, barcode: str):
        super().__init__()
        self.barcode = barcode

    def run(self) -> None:
        try:
            result = lookup_online_sync(self.barcode)
        except Exception:
            log.exception("upc worker run failed")
            result = None
        if result is None:
            self.not_found.emit(self.barcode)
        self.finished.emit(self.barcode, result)
