"""Cart engine: add/remove/qty, deal auto-apply, tax recompute, totals.

Money in cents. Tax is computed on post-deal-discount price per line.
Cash rounding is *previewed* in `totals['rounded_total_cents']` but the caller
must explicitly choose cash vs card at checkout — only cash uses the rounded total.
"""

from __future__ import annotations

import json
from typing import Optional

from core import db, tax
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
        self._totals: dict = self._zero_totals()

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

    def add_bag_charge(self) -> CartItem:
        """Add bag charge. Taxed GST + PST per .claude/tax.md."""
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
        for ln in self.lines:
            ln.deal_id = None
            ln.deal_discount_cents = 0
            ln.gst_cents = 0
            ln.pst_cents = 0
            ln.deposit_cents = 0
            ln.line_total_cents = 0

    def _apply_deals(self, deals: list[Deal]) -> None:
        """Walk deals in id order. Lines already bound to a deal are skipped by later deals."""
        for d in deals:
            try:
                if d.deal_type == "qty_discount":
                    self._apply_qty_discount(d)
                elif d.deal_type == "bundle":
                    self._apply_bundle(d)
                elif d.deal_type == "spend_discount":
                    self._apply_spend_discount(d)
                elif d.deal_type == "cross_dept":
                    self._apply_cross_dept(d)
                else:
                    log.warning("unknown deal_type: %s (deal id=%s)", d.deal_type, d.id)
            except Exception:
                log.exception("deal id=%s (%s) failed to apply", d.id, d.deal_type)

    def _apply_qty_discount(self, d: Deal) -> None:
        """trigger:{item_id, qty} reward:{total_price_cents}.

        Buy `qty` of `item_id` for `total_price_cents` total. Applies once per `qty` group.
        """
        target_id = d.trigger.get("item_id")
        need_qty = int(d.trigger.get("qty", 0))
        bundle_price = int(d.reward.get("total_price_cents", 0))
        if not target_id or need_qty <= 0:
            return
        for ln in self.lines:
            if (
                ln.item_id == target_id
                and ln.deal_id is None
                and ln.quantity >= need_qty
            ):
                bundles = ln.quantity // need_qty
                regular = ln.unit_price_cents * need_qty * bundles
                discounted = bundle_price * bundles
                disc = regular - discounted
                if disc > 0:
                    ln.deal_id = d.id
                    ln.deal_discount_cents = disc

    def _apply_bundle(self, d: Deal) -> None:
        """trigger:{items:[id_a, id_b, ...]} reward:{fixed_price_cents}.

        Requires one of each listed item present and unbound. Discount put on first matched line.
        """
        item_ids = d.trigger.get("items", [])
        fixed = int(d.reward.get("fixed_price_cents", 0))
        if not item_ids or fixed <= 0:
            return
        matched: list[CartItem] = []
        for need_id in item_ids:
            ln = next(
                (l for l in self.lines
                 if l.item_id == need_id and l.deal_id is None and l.quantity >= 1),
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

    def _apply_spend_discount(self, d: Deal) -> None:
        """trigger:{item_id, qty} reward:{discount_cents}.

        Buy `qty` of item → flat `discount_cents` off. Repeats per `qty` multiple.
        """
        target_id = d.trigger.get("item_id")
        need_qty = int(d.trigger.get("qty", 0))
        disc = int(d.reward.get("discount_cents", 0))
        if not target_id or need_qty <= 0 or disc <= 0:
            return
        for ln in self.lines:
            if (
                ln.item_id == target_id
                and ln.deal_id is None
                and ln.quantity >= need_qty
            ):
                bundles = ln.quantity // need_qty
                ln.deal_id = d.id
                ln.deal_discount_cents = disc * bundles

    def _apply_cross_dept(self, d: Deal) -> None:
        """trigger:{dept} reward:{target_dept, discount_pct}.

        Any line in trigger dept → discount_pct off all unbound lines in target_dept.
        """
        trigger_dept = d.trigger.get("dept")
        target_dept = d.reward.get("target_dept")
        pct = int(d.reward.get("discount_pct", 0))
        if not trigger_dept or not target_dept or pct <= 0:
            return
        if not any(l.department == trigger_dept for l in self.lines):
            return
        for ln in self.lines:
            if ln.department == target_dept and ln.deal_id is None:
                gross = ln.unit_price_cents * ln.quantity
                ln.deal_id = d.id
                ln.deal_discount_cents = (gross * pct + 50) // 100   # half-up int

    # ─── tax recompute (post-discount) ───────────────────────────────────────

    def _recalc_line_tax(self) -> None:
        """Compute GST/PST/deposit/line_total per line on post-deal price."""
        for ln in self.lines:
            gross = ln.unit_price_cents * ln.quantity
            net = max(0, gross - ln.deal_discount_cents)
            # Tax on net total (not per-unit) avoids divide-by-qty rounding loss.
            r = tax.calculate_line_tax(net, gst=ln.tax_gst, pst=ln.tax_pst, deposit="none", qty=1)
            ln.gst_cents = r["gst"]
            ln.pst_cents = r["pst"]
            # Deposit is per-unit at full quantity (deals don't waive deposits).
            dep_unit = {
                "none": 0,
                "355ml": tax.DEPOSIT_355ML_CENTS,
                "1L": tax.DEPOSIT_1L_CENTS,
            }.get(ln.bottle_deposit, 0)
            ln.deposit_cents = dep_unit * ln.quantity
            ln.line_total_cents = net + ln.gst_cents + ln.pst_cents + ln.deposit_cents

    def _sum_totals(self) -> None:
        subtotal = 0
        discount = 0
        gst = 0
        pst = 0
        dep = 0
        bag = 0
        for ln in self.lines:
            subtotal += ln.unit_price_cents * ln.quantity
            discount += ln.deal_discount_cents
            gst += ln.gst_cents
            pst += ln.pst_cents
            dep += ln.deposit_cents
            if ln.kind == "bag":
                bag += ln.unit_price_cents * ln.quantity
        total = subtotal - discount + gst + pst + dep
        self._totals = {
            "subtotal_cents": subtotal,
            "discount_cents": discount,
            "gst_cents": gst,
            "pst_cents": pst,
            "deposit_cents": dep,
            "bag_charge_cents": bag,
            "total_cents": total,
            "rounded_total_cents": tax.apply_cash_rounding(total),
        }

    # ─── deal hints (for nudge banner — "add 1 more X to save $Y") ───────────

    def deal_hints(self, deals: Optional[list[Deal]] = None) -> list[dict]:
        """Return a list of hints for partially-triggered deals.

        Each hint: {deal_id, deal_name, item_id, need_qty, have_qty, savings_cents}.
        Only qty_discount, spend_discount, and bundle are checked (cross_dept
        triggers on presence, no nudge possible).
        """
        if deals is None:
            deals = [Deal.from_row(r) for r in db.list_active_deals()]
        hints: list[dict] = []
        for d in deals:
            try:
                if d.deal_type in ("qty_discount", "spend_discount"):
                    target_id = d.trigger.get("item_id")
                    need_qty = int(d.trigger.get("qty", 0))
                    if not target_id or need_qty <= 0:
                        continue
                    have = sum(ln.quantity for ln in self.lines if ln.item_id == target_id)
                    if 0 < have < need_qty:
                        if d.deal_type == "qty_discount":
                            unit = next(ln.unit_price_cents for ln in self.lines if ln.item_id == target_id)
                            savings = unit * need_qty - int(d.reward.get("total_price_cents", 0))
                        else:
                            savings = int(d.reward.get("discount_cents", 0))
                        if savings > 0:
                            hints.append({
                                "deal_id": d.id,
                                "deal_name": d.name,
                                "item_id": target_id,
                                "need_qty": need_qty,
                                "have_qty": have,
                                "savings_cents": savings,
                            })
                elif d.deal_type == "bundle":
                    item_ids = d.trigger.get("items", [])
                    have_ids = {ln.item_id for ln in self.lines if ln.item_id is not None}
                    missing = [i for i in item_ids if i not in have_ids]
                    if 0 < len(missing) < len(item_ids):
                        regular = 0
                        for need_id in item_ids:
                            ln = next((l for l in self.lines if l.item_id == need_id), None)
                            if ln is not None:
                                regular += ln.unit_price_cents
                        savings = regular - int(d.reward.get("fixed_price_cents", 0))
                        hints.append({
                            "deal_id": d.id,
                            "deal_name": d.name,
                            "missing_item_ids": missing,
                            "savings_cents": max(0, savings),
                        })
            except Exception:
                log.exception("deal hint failed for deal id=%s", d.id)
        return hints

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
