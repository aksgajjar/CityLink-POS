"""Domain dataclasses. Thin shapes — no business logic. Money in cents (int)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Role = Literal["cashier", "admin"]
Deposit = Literal["none", "355ml", "1L"]
PaymentMethod = Literal["cash", "card", "split"]
TxnStatus = Literal["completed", "voided", "held"]
ShiftStatus = Literal["open", "closed"]
DealType = Literal["bundle", "qty_discount", "cross_dept", "spend_discount"]
LineKind = Literal["item", "bag", "lottery"]


def _b(v: Any) -> bool:
    """SQLite stores bools as 0/1 INTEGER. Coerce safely."""
    return bool(v) if v is not None else False


# ─── User ────────────────────────────────────────────────────────────────────

@dataclass
class User:
    id: int
    name: str
    role: Role
    is_active: bool = True
    pin_hash: str = ""           # never display; loaded for auth only
    created_at: Optional[str] = None

    def __repr__(self) -> str:
        masked = "***" if self.pin_hash else ""
        return (
            f"User(id={self.id!r}, name={self.name!r}, role={self.role!r}, "
            f"is_active={self.is_active!r}, pin_hash={masked!r}, "
            f"created_at={self.created_at!r})"
        )

    @classmethod
    def from_row(cls, row: dict) -> "User":
        return cls(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            is_active=_b(row.get("is_active", 1)),
            pin_hash=row.get("pin_hash", ""),
            created_at=row.get("created_at"),
        )


# ─── Item ────────────────────────────────────────────────────────────────────

@dataclass
class Item:
    id: int
    barcode: Optional[str]
    name: str
    price_cents: int
    department: str
    tax_gst: bool = True
    tax_pst: bool = False
    bottle_deposit: Deposit = "none"
    age_restricted: bool = False
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "Item":
        return cls(
            id=row["id"],
            barcode=row.get("barcode"),
            name=row["name"],
            price_cents=row["price_cents"],
            department=row["department"],
            tax_gst=_b(row.get("tax_gst", 1)),
            tax_pst=_b(row.get("tax_pst", 0)),
            bottle_deposit=row.get("bottle_deposit", "none"),
            age_restricted=_b(row.get("age_restricted", 0)),
            is_active=_b(row.get("is_active", 1)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


# ─── CartItem ────────────────────────────────────────────────────────────────

@dataclass
class CartItem:
    """One line in the active cart. Mutable: qty changes, deals/tax recomputed by cart engine.

    `kind` distinguishes regular items from synthetic bag-charge / lottery-sale lines.
    Runtime-only (not persisted to transaction_items table).
    """
    name: str
    unit_price_cents: int
    quantity: int = 1
    item_id: Optional[int] = None        # None = manual / unlisted price entry
    department: str = ""
    tax_gst: bool = True
    tax_pst: bool = False
    bottle_deposit: Deposit = "none"
    age_restricted: bool = False
    manual_price_override: bool = False
    deal_id: Optional[int] = None
    deal_discount_cents: int = 0
    gst_cents: int = 0
    pst_cents: int = 0
    deposit_cents: int = 0
    line_total_cents: int = 0
    basket_share_cents: int = 0
    kind: LineKind = "item"

    @classmethod
    def from_item(cls, item: Item, quantity: int = 1) -> "CartItem":
        """Build a fresh cart line from a catalog item."""
        return cls(
            item_id=item.id,
            name=item.name,
            unit_price_cents=item.price_cents,
            quantity=quantity,
            department=item.department,
            tax_gst=item.tax_gst,
            tax_pst=item.tax_pst,
            bottle_deposit=item.bottle_deposit,
            age_restricted=item.age_restricted,
        )

    def to_db_dict(self) -> dict:
        """Shape expected by db.insert_transaction(items=[...])."""
        return {
            "item_id": self.item_id,
            "name": self.name,
            "quantity": self.quantity,
            "unit_price_cents": self.unit_price_cents,
            "manual_price_override": int(self.manual_price_override),
            "deal_id": self.deal_id,
            "deal_discount_cents": self.deal_discount_cents,
            "gst_cents": self.gst_cents,
            "pst_cents": self.pst_cents,
            "deposit_cents": self.deposit_cents,
            "line_total_cents": self.line_total_cents,
        }


# ─── Deal ────────────────────────────────────────────────────────────────────

@dataclass
class Deal:
    id: int
    name: str
    deal_type: DealType
    trigger: dict           # decoded from trigger_json
    reward: dict            # decoded from reward_json
    start_date: str         # 'YYYY-MM-DD'
    expiry_date: str
    is_active: bool = True
    created_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "Deal":
        # db.list_active_deals() already attaches decoded trigger/reward.
        # Fall back to raw JSON keys if a caller passes a raw row.
        import json as _json
        trigger = row.get("trigger") if "trigger" in row else _json.loads(row["trigger_json"])
        reward = row.get("reward") if "reward" in row else _json.loads(row["reward_json"])
        return cls(
            id=row["id"],
            name=row["name"],
            deal_type=row["deal_type"],
            trigger=trigger,
            reward=reward,
            start_date=row["start_date"],
            expiry_date=row["expiry_date"],
            is_active=_b(row.get("is_active", 1)),
            created_at=row.get("created_at"),
        )


# ─── Transaction ─────────────────────────────────────────────────────────────

@dataclass
class Transaction:
    """Persisted sale. Built by cart at checkout, written by db.insert_transaction."""
    transaction_ref: str
    subtotal_cents: int
    gst_cents: int
    pst_cents: int
    deposit_cents: int
    total_cents: int
    rounded_total_cents: int
    payment_method: PaymentMethod
    items: list[CartItem] = field(default_factory=list)
    discount_cents: int = 0
    bag_charge_cents: int = 0
    cash_tendered_cents: int = 0
    change_cents: int = 0
    card_amount_cents: int = 0
    card_auth_code: Optional[str] = None
    card_last4: Optional[str] = None
    status: TxnStatus = "completed"
    cashier_id: Optional[int] = None
    cashier_name: Optional[str] = None
    shift_id: Optional[int] = None
    id: Optional[int] = None              # set after DB insert
    created_at: Optional[str] = None

    def header_dict(self) -> dict:
        """Shape expected by db.insert_transaction(txn=...)."""
        return {
            "transaction_ref": self.transaction_ref,
            "subtotal_cents": self.subtotal_cents,
            "discount_cents": self.discount_cents,
            "gst_cents": self.gst_cents,
            "pst_cents": self.pst_cents,
            "deposit_cents": self.deposit_cents,
            "bag_charge_cents": self.bag_charge_cents,
            "total_cents": self.total_cents,
            "rounded_total_cents": self.rounded_total_cents,
            "payment_method": self.payment_method,
            "cash_tendered_cents": self.cash_tendered_cents,
            "change_cents": self.change_cents,
            "card_amount_cents": self.card_amount_cents,
            "card_auth_code": self.card_auth_code,
            "card_last4": self.card_last4,
            "status": self.status,
            "cashier_id": self.cashier_id,
            "cashier_name": self.cashier_name,
            "shift_id": self.shift_id,
        }

    @classmethod
    def from_db(cls, header_row: dict, item_rows: list[dict]) -> "Transaction":
        """Reconstruct from db.get_transaction() result."""
        items: list[CartItem] = []
        for r in item_rows:
            items.append(CartItem(
                item_id=r.get("item_id"),
                name=r["name"],
                quantity=r.get("quantity", 1),
                unit_price_cents=r["unit_price_cents"],
                manual_price_override=_b(r.get("manual_price_override", 0)),
                deal_id=r.get("deal_id"),
                deal_discount_cents=r.get("deal_discount_cents", 0),
                gst_cents=r.get("gst_cents", 0),
                pst_cents=r.get("pst_cents", 0),
                deposit_cents=r.get("deposit_cents", 0),
                line_total_cents=r["line_total_cents"],
            ))
        return cls(
            transaction_ref=header_row["transaction_ref"],
            subtotal_cents=header_row["subtotal_cents"],
            discount_cents=header_row.get("discount_cents", 0),
            gst_cents=header_row["gst_cents"],
            pst_cents=header_row["pst_cents"],
            deposit_cents=header_row["deposit_cents"],
            bag_charge_cents=header_row.get("bag_charge_cents", 0),
            total_cents=header_row["total_cents"],
            rounded_total_cents=header_row["rounded_total_cents"],
            payment_method=header_row["payment_method"],
            cash_tendered_cents=header_row.get("cash_tendered_cents", 0),
            change_cents=header_row.get("change_cents", 0),
            card_amount_cents=header_row.get("card_amount_cents", 0),
            card_auth_code=header_row.get("card_auth_code"),
            card_last4=header_row.get("card_last4"),
            status=header_row.get("status", "completed"),
            cashier_id=header_row.get("cashier_id"),
            cashier_name=header_row.get("cashier_name"),
            shift_id=header_row.get("shift_id"),
            id=header_row.get("id"),
            created_at=header_row.get("created_at"),
            items=items,
        )


# ─── Shift ───────────────────────────────────────────────────────────────────

@dataclass
class Shift:
    id: int
    cashier_id: int
    cashier_name: str
    opening_float_cents: int
    status: ShiftStatus = "open"
    closing_cash_cents: Optional[int] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @classmethod
    def from_row(cls, row: dict) -> "Shift":
        return cls(
            id=row["id"],
            cashier_id=row["cashier_id"],
            cashier_name=row["cashier_name"],
            opening_float_cents=row.get("opening_float_cents", 0),
            status=row.get("status", "open"),
            closing_cash_cents=row.get("closing_cash_cents"),
            opened_at=row.get("opened_at"),
            closed_at=row.get("closed_at"),
        )
