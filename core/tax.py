"""BC tax engine + Canadian cash rounding. All values in cents (int).

Tax math uses integer half-up rounding (not Python's banker `round()`), and is
applied to the post-deal-discount price. Cash rounding is applied only to the
grand total when payment method is cash; card payments use the exact total.
"""

from __future__ import annotations

from typing import Optional

# Default BC rates. Override at app startup via set_rates() with values from config.json.
GST_RATE_BP: int = 500    # basis-points: 5.00% = 500 / 10_000
PST_RATE_BP: int = 700    # 7.00%
DEPOSIT_355ML_CENTS: int = 10
DEPOSIT_1L_CENTS: int = 25
BAG_CHARGE_CENTS: int = 25

_DEPOSIT_MAP = {
    "none": 0,
    "355ml": DEPOSIT_355ML_CENTS,
    "1L": DEPOSIT_1L_CENTS,
}


def set_rates(
    *,
    gst_rate: Optional[float] = None,
    pst_rate: Optional[float] = None,
    bottle_deposit_355ml: Optional[float] = None,
    bottle_deposit_1L: Optional[float] = None,
    bag_charge_cents: Optional[int] = None,
) -> None:
    """Override default rates. Call once at startup with config.json values.

    Float rates (e.g. 0.05) are converted to integer basis points internally
    to keep all subsequent math in integers.
    """
    global GST_RATE_BP, PST_RATE_BP, DEPOSIT_355ML_CENTS, DEPOSIT_1L_CENTS, BAG_CHARGE_CENTS, _DEPOSIT_MAP
    if gst_rate is not None:
        GST_RATE_BP = _half_up(int(round(gst_rate * 10_000)))
    if pst_rate is not None:
        PST_RATE_BP = _half_up(int(round(pst_rate * 10_000)))
    if bottle_deposit_355ml is not None:
        DEPOSIT_355ML_CENTS = int(round(bottle_deposit_355ml * 100))
    if bottle_deposit_1L is not None:
        DEPOSIT_1L_CENTS = int(round(bottle_deposit_1L * 100))
    if bag_charge_cents is not None:
        BAG_CHARGE_CENTS = int(bag_charge_cents)
    _DEPOSIT_MAP = {
        "none": 0,
        "355ml": DEPOSIT_355ML_CENTS,
        "1L": DEPOSIT_1L_CENTS,
    }


def _half_up(n: int) -> int:
    """Identity for ints. Placeholder so the conversion site is explicit."""
    return n


def _pct_half_up(amount_cents: int, rate_bp: int) -> int:
    """Half-up integer rounding: amount * rate_bp / 10_000.

    Half-up (not banker's): boundary cases like 2.5 cents → 3 cents.
    """
    if amount_cents < 0:
        # POS lines should never be negative; guard the formula's positive-only assumption.
        raise ValueError(f"amount_cents must be >= 0, got {amount_cents}")
    return (amount_cents * rate_bp + 5_000) // 10_000


def calculate_line_tax(
    price_cents: int,
    gst: bool | int,
    pst: bool | int,
    deposit: str,
    qty: int = 1,
) -> dict:
    """Calculate tax for one cart line. All values in cents.

    Pass `price_cents` as the *post-deal-discount* unit price. Caller is
    responsible for applying any deal/manual discount before calling this.

    Returns dict with keys: subtotal, gst, pst, deposit, line_total.
    """
    if qty < 0:
        raise ValueError(f"qty must be >= 0, got {qty}")
    subtotal = price_cents * qty
    gst_amt = _pct_half_up(subtotal, GST_RATE_BP) if gst else 0
    pst_amt = _pct_half_up(subtotal, PST_RATE_BP) if pst else 0
    dep_unit = _DEPOSIT_MAP.get(deposit)
    if dep_unit is None:
        raise ValueError(f"unknown deposit type: {deposit!r}")
    dep_amt = dep_unit * qty
    return {
        "subtotal":   subtotal,
        "gst":        gst_amt,
        "pst":        pst_amt,
        "deposit":    dep_amt,
        "line_total": subtotal + gst_amt + pst_amt + dep_amt,
    }


def apply_cash_rounding(total_cents: int) -> int:
    """Canadian cash rounding to nearest 5 cents. Card = no rounding (do not call).

    Rules: remainder 1-2 → round down, remainder 3-4 → round up.
    """
    if total_cents < 0:
        raise ValueError(f"total_cents must be >= 0, got {total_cents}")
    remainder = total_cents % 5
    if remainder < 3:
        return total_cents - remainder
    return total_cents + (5 - remainder)
