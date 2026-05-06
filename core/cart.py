"""Cart engine: add/remove/qty, deal auto-apply, tax recompute, totals.

Money in cents. Tax is computed on post-deal-discount price per line.
Cash rounding is *previewed* in `totals['rounded_total_cents']` but the caller
must explicitly choose cash vs card at checkout — only cash uses the rounded total.
"""

from __future__ import annotations

import json
from typing import Optional

from core import db, tax
from core import deals as _deals_engine
from core.departments import DEPT_TAX_DEFAULTS
from core.logger import get_logger
from core.models import CartItem, Deal, Item

log = get_logger("cart")

# Bag line lives in accessories dept by default but is identified by CartItem.kind == "bag",
# not by name/dept matching. See models.py LineKind.
_BAG_DEPARTMENT = "accessories"
_BAG_NAME = "Bag"


class Cart:
    """In-memory shopping cart. Recomputes deals + totals on every change.

    Single instance per cashier session. Empty after each completed sale.
    """

    def __init__(self) -> None:
        self.lines: list[CartItem] = []
        # Cart-level (basket) discount applied AFTER per-line deals, BEFORE tax.
        # Set either as a flat amount in cents OR a percent (0-100). Percent
        # wins if both set.
        self.basket_discount_cents: int = 0
        self.basket_discount_pct: float = 0.0
        self._totals: dict = self._zero_totals()

    def set_basket_discount(self, *, cents: int = 0, pct: float = 0.0) -> None:
        """Apply or clear basket discount. Pct=0 + cents=0 → no discount."""
        self.basket_discount_cents = max(0, int(cents))
        self.basket_discount_pct = max(0.0, min(100.0, float(pct)))
        self.recompute()

    # ─── construction helpers ────────────────────────────────────────────────

    @staticmethod
    def _zero_totals() -> dict:
        return {
            "subtotal_cents": 0,
            "discount_cents": 0,
            "gst_cents": 0,
            "pst_cents": 0,
            "deposit_cents": 0,
            "bag_charge_cents": 0,
            "total_cents": 0,
            "rounded_total_cents": 0,
        }

    # ─── add / remove / qty ──────────────────────────────────────────────────

    def add_item(self, item: Item, quantity: int = 1) -> CartItem:
        """Add catalog item. Stacks onto existing line if same item_id and no manual override / no deal."""
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")
        for ln in self.lines:
            if (
                ln.item_id == item.id
                and not ln.manual_price_override
                and ln.deal_id is None
            ):
                ln.quantity += quantity
                self.recompute()
                return ln
        ln = CartItem.from_item(item, quantity=quantity)
        self.lines.append(ln)
        self.recompute()
        return ln

    def add_manual(
        self,
        name: str,
        unit_price_cents: int,
        department: str,
        quantity: int = 1,
    ) -> CartItem:
        """Add manual / unlisted price entry. Tax flags from DEPT_TAX_DEFAULTS."""
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")
        if unit_price_cents < 0:
            raise ValueError(f"unit_price_cents must be >= 0, got {unit_price_cents}")
        d = DEPT_TAX_DEFAULTS.get(department)
        if d is None:
            raise ValueError(f"unknown department: {department!r}")
        ln = CartItem(
            name=name,
            unit_price_cents=unit_price_cents,
            quantity=quantity,
            item_id=None,
            department=department,
            tax_gst=bool(d["gst"]),
            tax_pst=bool(d["pst"]),
            bottle_deposit=d["deposit"],
            manual_price_override=True,
            kind="item",
        )
        self.lines.append(ln)
        self.recompute()
        return ln

    def add_lottery_sale(
        self,
        amount_cents: int,
        label: Optional[str] = None,
    ) -> CartItem:
        """Lottery sale = cart line, no tax, no deposit. Label optional (Lotto Max / BC49 / Extra / Scratch)."""
        if amount_cents <= 0:
            raise ValueError(f"lottery amount must be > 0, got {amount_cents}")
        name = f"Lottery: {label}" if label else "Lottery Sale"
        ln = CartItem(
            name=name,
            unit_price_cents=amount_cents,
            quantity=1,
            item_id=None,
            department="lottery",
            tax_gst=False,
            tax_pst=False,
            bottle_deposit="none",
            manual_price_override=True,
            kind="lottery",
        )
        self.lines.append(ln)
        self.recompute()
        return ln

    def add_lottery_payout(self, amount_cents: int) -> CartItem:
        """Lottery PAYOUT line — negative price (store paying customer).
        No tax, no deposit. Reduces cart total by `amount_cents`.
        """
        if amount_cents <= 0:
            raise ValueError(f"payout must be > 0, got {amount_cents}")
        ln = CartItem(
            name="LOTTERY PAYOUT",
            unit_price_cents=-amount_cents,
            quantity=1,
            item_id=None,
            department="lottery",
            tax_gst=False,
            tax_pst=False,
            bottle_deposit="none",
            manual_price_override=True,
            kind="lottery",
        )
        self.lines.append(ln)
        self.recompute()
        return ln

    def add_bag_charge(self) -> CartItem:
        """Add bag charge. Taxed GST + PST per .claude/tax.md.

        Idempotent: if a bag line already exists, return it instead of adding
        a duplicate (prevents double-charge on accidental double-tap).
        """
        for existing in self.lines:
            if existing.kind == "bag":
                return existing
        ln = CartItem(
            name=_BAG_NAME,
            unit_price_cents=tax.BAG_CHARGE_CENTS,
            quantity=1,
            item_id=None,
            department=_BAG_DEPARTMENT,
            tax_gst=True,
            tax_pst=True,
            bottle_deposit="none",
            manual_price_override=True,
            kind="bag",
        )
        self.lines.append(ln)
        self.recompute()
        return ln

    def set_quantity(self, line_index: int, qty: int) -> None:
        """Set line quantity. qty <= 0 removes the line."""
        if qty <= 0:
            self.remove_line(line_index)
            return
        self.lines[line_index].quantity = qty
        self.recompute()

    def remove_line(self, line_index: int) -> None:
        """Remove line at index."""
        self.lines.pop(line_index)
        self.recompute()

    def clear(self) -> None:
        """Empty the cart."""
        self.lines.clear()
        self._totals = self._zero_totals()

    # ─── queries ─────────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not self.lines

    def has_age_restricted(self) -> bool:
        """True if any line is age-restricted (cashier must verify ID)."""
        return any(ln.age_restricted for ln in self.lines)

    def line_count(self) -> int:
        return len(self.lines)

    def item_count(self) -> int:
        """Total quantity across all lines."""
        return sum(ln.quantity for ln in self.lines)

    @property
    def totals(self) -> dict:
        """Snapshot of latest computed totals (dict, copy)."""
        return dict(self._totals)

    # ─── deal application + recompute ────────────────────────────────────────

    def recompute(self, deals: Optional[list[Deal]] = None) -> dict:
        """Recompute deals, line tax, and totals. Returns the totals dict.

        Pass `deals=` to inject a deal list (tests, deterministic UI). Default
        loads active deals from `db.list_active_deals()`.
        """
        if deals is None:
            deals = [Deal.from_row(r) for r in db.list_active_deals()]

        self._reset_line_state()
        self._apply_deals(deals)
        self._recalc_line_tax()
        self._sum_totals()
        return self._totals

    def _reset_line_state(self) -> None:
        # `apply_deals` resets deal_id/deal_discount itself; here we only reset
        # tax + line_total fields that we'll rewrite in _recalc_line_tax.
        for ln in self.lines:
            ln.gst_cents = 0
            ln.pst_cents = 0
            ln.deposit_cents = 0
            ln.line_total_cents = 0
            ln.basket_share_cents = 0

    def _apply_deals(self, deals: list[Deal]) -> None:
        """Delegate to core/deals.py engine. Walks deals in order; first wins."""
        _deals_engine.apply_deals(self.lines, deals)

    # ─── tax recompute (post-discount) ───────────────────────────────────────

    def _recalc_line_tax(self) -> None:
        """Compute GST/PST/deposit/line_total per line on post-deal price.

        Basket discount (cart-level) is allocated pro-rata across non-bag,
        non-lottery lines BEFORE tax — so GST/PST reflect the discounted
        price.
        """
        # Pass 1: per-line net post-deal, also identify discountable lines.
        nets: list[int] = []
        discountable_idx: list[int] = []
        discountable_total = 0
        for i, ln in enumerate(self.lines):
            gross = ln.unit_price_cents * ln.quantity
            net = max(0, gross - ln.deal_discount_cents)
            nets.append(net)
            if ln.kind == "item" and not ln.manual_price_override is None:
                pass
            if ln.kind in ("item", "bag") or ln.kind == "lottery":
                pass
            # Allow basket discount on regular item / manual lines only.
            # Exclude lottery (face-value) and bag (operational fee).
            if ln.kind not in ("lottery", "bag"):
                discountable_idx.append(i)
                discountable_total += net

        # Compute total basket discount cents.
        basket_total = 0
        if self.basket_discount_pct > 0:
            basket_total = int(round(discountable_total * self.basket_discount_pct / 100.0))
        elif self.basket_discount_cents > 0:
            basket_total = self.basket_discount_cents
        basket_total = min(basket_total, discountable_total)

        # Pass 2: allocate per line, compute tax on adjusted net.
        allocated = 0
        for j, i in enumerate(discountable_idx):
            ln = self.lines[i]
            if discountable_total <= 0 or basket_total <= 0:
                ln.basket_share_cents = 0
            elif j == len(discountable_idx) - 1:
                # Final share absorbs rounding remainder.
                ln.basket_share_cents = basket_total - allocated
            else:
                share = int(round(basket_total * nets[i] / discountable_total))
                ln.basket_share_cents = share
                allocated += share
        # Lines not in the discountable set get share = 0.
        for i, ln in enumerate(self.lines):
            if i not in discountable_idx:
                ln.basket_share_cents = 0

        # Now tax + line_total per line.
        for ln in self.lines:
            gross = ln.unit_price_cents * ln.quantity
            adjusted = gross - ln.deal_discount_cents - getattr(ln, "basket_share_cents", 0)
            # Negative-priced lines (e.g. lottery payout) keep their negative
            # net so line_total reflects the deduction. Non-negative items
            # are clamped to >= 0 (deal/basket can't go below zero).
            net = adjusted if adjusted < 0 else max(0, adjusted)
            r = tax.calculate_line_tax(max(0, net), gst=ln.tax_gst, pst=ln.tax_pst,
                                       deposit="none", qty=1)
            ln.gst_cents = r["gst"]
            ln.pst_cents = r["pst"]
            dep_unit = {
                "none": 0,
                "355ml": tax.DEPOSIT_355ML_CENTS,
                "1L": tax.DEPOSIT_1L_CENTS,
            }.get(ln.bottle_deposit, 0)
            ln.deposit_cents = dep_unit * ln.quantity
            ln.line_total_cents = net + ln.gst_cents + ln.pst_cents + ln.deposit_cents

    def _sum_totals(self) -> None:
        subtotal = 0
        deal_discount = 0
        basket_discount = 0
        gst = 0
        pst = 0
        dep = 0
        bag = 0
        for ln in self.lines:
            subtotal += ln.unit_price_cents * ln.quantity
            deal_discount += ln.deal_discount_cents
            basket_discount += getattr(ln, "basket_share_cents", 0)
            gst += ln.gst_cents
            pst += ln.pst_cents
            dep += ln.deposit_cents
            if ln.kind == "bag":
                bag += ln.unit_price_cents * ln.quantity
        total = subtotal - deal_discount - basket_discount + gst + pst + dep
        # `discount_cents` (combined) preserved for receipts/reports compat;
        # `basket_discount_cents` exposed separately for UI display.
        self._totals = {
            "subtotal_cents": subtotal,
            "discount_cents": deal_discount + basket_discount,
            "deal_discount_cents": deal_discount,
            "basket_discount_cents": basket_discount,
            "gst_cents": gst,
            "pst_cents": pst,
            "deposit_cents": dep,
            "bag_charge_cents": bag,
            "total_cents": total,
            "rounded_total_cents": tax.apply_cash_rounding(total),
        }

    # ─── deal hints (for nudge banner — "add 1 more X to save $Y") ───────────

    def deal_hints(self, deals: Optional[list[Deal]] = None) -> list[dict]:
        """Return list of hints for partially-triggered deals.

        Delegates to core/deals.py:compute_hints.
        """
        if deals is None:
            deals = [Deal.from_row(r) for r in db.list_active_deals()]
        return _deals_engine.compute_hints(self.lines, deals)

    # ─── snapshot for hold/restore ───────────────────────────────────────────

    def to_json(self) -> str:
        """Serialize cart for `db.hold_transaction(cart_json=...)`."""
        return json.dumps([self._line_to_dict(ln) for ln in self.lines])

    @staticmethod
    def _line_to_dict(ln: CartItem) -> dict:
        return {
            "name": ln.name,
            "unit_price_cents": ln.unit_price_cents,
            "quantity": ln.quantity,
            "item_id": ln.item_id,
            "department": ln.department,
            "tax_gst": ln.tax_gst,
            "tax_pst": ln.tax_pst,
            "bottle_deposit": ln.bottle_deposit,
            "age_restricted": ln.age_restricted,
            "manual_price_override": ln.manual_price_override,
            "kind": ln.kind,
        }

    @classmethod
    def from_json(cls, s: str, *, deals: Optional[list[Deal]] = None) -> "Cart":
        """Restore from `to_json()` output. Re-applies deals + recomputes totals."""
        c = cls()
        for d in json.loads(s):
            c.lines.append(CartItem(
                name=d["name"],
                unit_price_cents=d["unit_price_cents"],
                quantity=d.get("quantity", 1),
                item_id=d.get("item_id"),
                department=d.get("department", ""),
                tax_gst=d.get("tax_gst", True),
                tax_pst=d.get("tax_pst", False),
                bottle_deposit=d.get("bottle_deposit", "none"),
                age_restricted=d.get("age_restricted", False),
                manual_price_override=d.get("manual_price_override", False),
                kind=d.get("kind", "item"),
            ))
        c.recompute(deals=deals)
        return c
