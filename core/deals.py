"""Deal engine — pure logic, no UI.

Four deal types per `.claude/features.md`:
  - bundle         : items A+B together at fixed_price_cents
  - qty_discount   : N of item X for total_price_cents
  - spend_discount : buy N of X → discount_cents off
  - cross_dept     : presence in dept A → discount_pct off all dept B lines

Public API:
  apply_deals(lines, deals)         mutates CartItems' deal_id + deal_discount_cents
  compute_hints(lines, deals)       returns list of nudge hints for near-miss deals

Cart engine (core/cart.py) delegates to these on every recompute().
"""

from __future__ import annotations

from typing import Iterable

from core.logger import get_logger
from core.models import CartItem, Deal

log = get_logger("deals")


# ─── Public entry points ─────────────────────────────────────────────────────

def apply_deals(lines: list[CartItem], deals: Iterable[Deal]) -> None:
    """Reset deal state on each line then re-apply all matching deals.

    Mutates `lines` in place. Cart recompute then re-tax-es each line based on
    the new (post-discount) net price.
    """
    for ln in lines:
        ln.deal_id = None
        ln.deal_discount_cents = 0

    for d in deals:
        try:
            if d.deal_type == "qty_discount":
                _apply_qty_discount(lines, d)
            elif d.deal_type == "bundle":
                _apply_bundle(lines, d)
            elif d.deal_type == "spend_discount":
                _apply_spend_discount(lines, d)
            elif d.deal_type == "cross_dept":
                _apply_cross_dept(lines, d)
            else:
                log.warning("unknown deal_type: %s (deal id=%s)", d.deal_type, d.id)
        except Exception:
            log.exception("deal id=%s (%s) failed to apply", d.id, d.deal_type)


def compute_hints(lines: list[CartItem], deals: Iterable[Deal]) -> list[dict]:
    """Nudge hints for partially-triggered deals.

    Each hint shape (qty/spend):
      {deal_id, deal_name, item_id, need_qty, have_qty, savings_cents}
    Bundle hint shape:
      {deal_id, deal_name, missing_item_ids, savings_cents}

    cross_dept deals trigger on presence — no near-miss state to surface.
    """
    hints: list[dict] = []
    for d in deals:
        try:
            if d.deal_type in ("qty_discount", "spend_discount"):
                qual_ids = _trigger_item_ids(d)
                need_qty = int(d.trigger.get("qty", 0))
                if not qual_ids or need_qty <= 0:
                    continue
                have = sum(ln.quantity for ln in lines if ln.item_id in qual_ids)
                if 0 < have < need_qty:
                    if d.deal_type == "qty_discount":
                        # Best-case unit price among present qualifying items.
                        present_units = [
                            ln.unit_price_cents for ln in lines
                            if ln.item_id in qual_ids
                        ]
                        unit = max(present_units) if present_units else 0
                        savings = unit * need_qty - int(d.reward.get("total_price_cents", 0))
                    else:
                        savings = int(d.reward.get("discount_cents", 0))
                    if savings > 0:
                        hints.append({
                            "deal_id": d.id,
                            "deal_name": d.name,
                            "item_id": qual_ids[0],
                            "need_qty": need_qty,
                            "have_qty": have,
                            "savings_cents": savings,
                        })
            elif d.deal_type == "bundle":
                item_ids = d.trigger.get("items", [])
                have_ids = {ln.item_id for ln in lines if ln.item_id is not None}
                missing = [i for i in item_ids if i not in have_ids]
                if not (0 < len(missing) < len(item_ids)):
                    continue
                # Savings approximation requires knowing the missing items' unit prices,
                # which we don't have at this point (only in cart). So we estimate
                # using the lines we DO have plus the bundle fixed price. If that
                # estimate is non-positive (customer wouldn't save by completing),
                # skip the hint — don't surface a "save $0.00" nudge.
                regular = 0
                for need_id in item_ids:
                    ln = next((l for l in lines if l.item_id == need_id), None)
                    if ln is not None:
                        regular += ln.unit_price_cents
                savings = regular - int(d.reward.get("fixed_price_cents", 0))
                if savings <= 0:
                    continue
                hints.append({
                    "deal_id": d.id,
                    "deal_name": d.name,
                    "missing_item_ids": missing,
                    "savings_cents": savings,
                })
        except Exception:
            log.exception("deal hint computation failed for deal id=%s", d.id)
    return hints


# ─── Per-type appliers ───────────────────────────────────────────────────────

def _trigger_item_ids(d: Deal) -> list[int]:
    """Return list of qualifying item ids. Supports both legacy
    `{item_id: X}` and multi-item `{items: [X, Y, ...]}` triggers.
    """
    if "items" in d.trigger:
        return [int(i) for i in (d.trigger.get("items") or []) if i]
    iid = d.trigger.get("item_id")
    return [int(iid)] if iid else []


def _apply_qty_discount(lines: list[CartItem], d: Deal) -> None:
    """trigger:{items:[ids] OR item_id} qty:N  reward:{total_price_cents}.
    Buy `qty` total of ANY listed item(s) for `total_price_cents`.
    Repeats in groups of `qty`. Discount allocated to the most expensive
    qualifying units in each group (best for the customer).
    """
    qual_ids = _trigger_item_ids(d)
    need_qty = int(d.trigger.get("qty", 0))
    bundle_price = int(d.reward.get("total_price_cents", 0))
    if not qual_ids or need_qty <= 0 or bundle_price < 0:
        return

    # Collect (line, unit_price) for every unbound qualifying unit.
    qual_lines = [
        ln for ln in lines
        if ln.kind == "item" and ln.item_id in qual_ids
        and ln.deal_id is None and ln.quantity > 0
    ]
    total_qty = sum(ln.quantity for ln in qual_lines)
    bundles = total_qty // need_qty
    if bundles <= 0:
        return

    # Build expanded unit list (line_ref, price) sorted desc by price so
    # discount applies to the most expensive units within each bundle.
    units: list[tuple] = []
    for ln in qual_lines:
        for _ in range(ln.quantity):
            units.append((ln, ln.unit_price_cents))
    units.sort(key=lambda u: -u[1])

    bundle_total_units = bundles * need_qty
    chosen = units[:bundle_total_units]
    regular = sum(p for _, p in chosen)
    discounted = bundle_price * bundles
    total_disc = regular - discounted
    if total_disc <= 0:
        return

    # Tag every involved line with deal_id; place discount on the
    # cheapest involved line so its `line_total` shows the savings prominently.
    involved = {id(ln) for ln, _ in chosen}
    for ln in qual_lines:
        if id(ln) in involved:
            ln.deal_id = d.id
    # Pick a single line to carry the lump-sum discount.
    cheapest = min((ln for ln, _ in chosen),
                   key=lambda l: l.unit_price_cents, default=None)
    if cheapest is not None:
        cheapest.deal_discount_cents = total_disc


def _apply_bundle(lines: list[CartItem], d: Deal) -> None:
    """trigger:{items:[id_a, id_b, ...]} reward:{fixed_price_cents}.
    All listed items must be present (qty>=1) and unbound.
    Discount applied to first matched line; all are tagged with deal_id.
    """
    item_ids = d.trigger.get("items", [])
    fixed = int(d.reward.get("fixed_price_cents", 0))
    if not item_ids or fixed <= 0:
        return
    matched: list[CartItem] = []
    for need_id in item_ids:
        ln = next(
            (l for l in lines
             if l.kind == "item" and l.item_id == need_id and l.deal_id is None and l.quantity >= 1),
            None,
        )
        if ln is None:
            return
        matched.append(ln)
    regular = sum(ln.unit_price_cents for ln in matched)
    disc = regular - fixed
    if disc <= 0:
        return
    for ln in matched:
        ln.deal_id = d.id
    matched[0].deal_discount_cents = disc


def _apply_spend_discount(lines: list[CartItem], d: Deal) -> None:
    """trigger:{items:[ids] OR item_id, qty} reward:{discount_cents}.
    Buy `qty` total of any listed item(s) → flat `discount_cents` off.
    Repeats per group of `qty`.
    """
    qual_ids = _trigger_item_ids(d)
    need_qty = int(d.trigger.get("qty", 0))
    disc = int(d.reward.get("discount_cents", 0))
    if not qual_ids or need_qty <= 0 or disc <= 0:
        return
    qual_lines = [
        ln for ln in lines
        if ln.kind == "item" and ln.item_id in qual_ids
        and ln.deal_id is None and ln.quantity > 0
    ]
    total_qty = sum(ln.quantity for ln in qual_lines)
    bundles = total_qty // need_qty
    if bundles <= 0:
        return
    # Tag all qualifying lines with the deal so they group visually; place
    # the lump discount on the first qualifying line.
    for ln in qual_lines:
        ln.deal_id = d.id
    qual_lines[0].deal_discount_cents = disc * bundles


def _apply_cross_dept(lines: list[CartItem], d: Deal) -> None:
    """trigger:{dept} reward:{target_dept, discount_pct}.
    Any line in trigger dept → percent off ALL unbound lines in target dept.
    """
    trigger_dept = d.trigger.get("dept")
    target_dept = d.reward.get("target_dept")
    pct = int(d.reward.get("discount_pct", 0))
    if not trigger_dept or not target_dept or pct <= 0:
        return
    if not any(l.department == trigger_dept for l in lines):
        return
    for ln in lines:
        if ln.kind == "item" and ln.department == target_dept and ln.deal_id is None:
            gross = ln.unit_price_cents * ln.quantity
            ln.deal_id = d.id
            ln.deal_discount_cents = (gross * pct + 50) // 100   # half-up integer
