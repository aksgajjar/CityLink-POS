"""All SQLite operations for CityLink POS. No raw SQL allowed outside this file."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from core.logger import get_logger

log = get_logger("db")

DEFAULT_DB_PATH = Path("data/store.db")
_conn: Optional[sqlite3.Connection] = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'cashier',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    department TEXT NOT NULL,
    tax_gst INTEGER DEFAULT 1,
    tax_pst INTEGER DEFAULT 0,
    bottle_deposit TEXT DEFAULT 'none',
    age_restricted INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    old_price_cents INTEGER NOT NULL,
    new_price_cents INTEGER NOT NULL,
    changed_by TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_ref TEXT UNIQUE NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    discount_cents INTEGER DEFAULT 0,
    gst_cents INTEGER NOT NULL,
    pst_cents INTEGER NOT NULL,
    deposit_cents INTEGER NOT NULL,
    bag_charge_cents INTEGER DEFAULT 0,
    total_cents INTEGER NOT NULL,
    rounded_total_cents INTEGER NOT NULL,
    payment_method TEXT NOT NULL,
    cash_tendered_cents INTEGER DEFAULT 0,
    change_cents INTEGER DEFAULT 0,
    card_amount_cents INTEGER DEFAULT 0,
    card_auth_code TEXT,
    card_last4 TEXT,
    status TEXT DEFAULT 'completed',
    cashier_id INTEGER,
    cashier_name TEXT,
    shift_id INTEGER,
    synced INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    item_id INTEGER,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price_cents INTEGER NOT NULL,
    manual_price_override INTEGER DEFAULT 0,
    deal_id INTEGER,
    deal_discount_cents INTEGER DEFAULT 0,
    gst_cents INTEGER DEFAULT 0,
    pst_cents INTEGER DEFAULT 0,
    deposit_cents INTEGER DEFAULT 0,
    line_total_cents INTEGER NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    deal_type TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    reward_json TEXT NOT NULL,
    start_date TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lottery_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    description TEXT,
    transaction_id INTEGER,
    shift_id INTEGER,
    cashier_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS held_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hold_label TEXT,
    cart_json TEXT NOT NULL,
    cashier_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS void_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_transaction_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    reason TEXT,
    authorized_by TEXT,
    cashier_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cashier_id INTEGER NOT NULL,
    cashier_name TEXT NOT NULL,
    opening_float_cents INTEGER DEFAULT 0,
    closing_cash_cents INTEGER,
    status TEXT DEFAULT 'open',
    opened_at TEXT DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS cash_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    note TEXT,
    cashier_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    detail TEXT,
    admin_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS barcode_misses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT NOT NULL UNIQUE,
    scan_count INTEGER DEFAULT 1,
    last_scanned TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_barcode ON items(barcode);
CREATE INDEX IF NOT EXISTS idx_items_dept    ON items(department);
CREATE INDEX IF NOT EXISTS idx_txn_ref       ON transactions(transaction_ref);
CREATE INDEX IF NOT EXISTS idx_txn_created   ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_txnitems_txn  ON transaction_items(transaction_id);
CREATE INDEX IF NOT EXISTS idx_lottery_shift ON lottery_ledger(shift_id);
CREATE INDEX IF NOT EXISTS idx_cashev_shift  ON cash_events(shift_id);
"""


# ─── Connection management ────────────────────────────────────────────────────

def init_db(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open connection, set pragmas, create schema. Idempotent."""
    global _conn
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(
        path,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.execute("PRAGMA journal_mode = WAL")
    _conn.executescript(SCHEMA_SQL)
    _conn.commit()
    log.info("db initialized at %s", path)
    return _conn


def conn() -> sqlite3.Connection:
    """Return active connection. Auto-init at default path if not opened."""
    if _conn is None:
        return init_db()
    return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Atomic write block. Commits on success, rolls back + re-raises on error."""
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        log.exception("transaction rollback")
        raise


def close_db() -> None:
    """Close connection. Call on app exit."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict]:
    return dict(row) if row is not None else None


# ─── Users ────────────────────────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    """SHA-256 hash of PIN string."""
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def create_user(name: str, pin: str, role: str = "cashier") -> int:
    """Insert user. Returns new id."""
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO users (name, pin_hash, role) VALUES (?, ?, ?)",
            (name, hash_pin(pin), role),
        )
        return cur.lastrowid


def get_user_by_pin(pin: str) -> Optional[dict]:
    """Return active user matching PIN, or None."""
    row = conn().execute(
        "SELECT * FROM users WHERE pin_hash = ? AND is_active = 1",
        (hash_pin(pin),),
    ).fetchone()
    return _row_to_dict(row)


def get_user(user_id: int) -> Optional[dict]:
    """Return user by id, or None."""
    row = conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row)


def list_users(active_only: bool = True) -> list[dict]:
    """Return all users (optionally only active)."""
    sql = "SELECT * FROM users"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    return [dict(r) for r in conn().execute(sql).fetchall()]


def deactivate_user(user_id: int) -> None:
    """Soft-delete user."""
    with transaction() as c:
        c.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))


def update_user_pin(user_id: int, new_pin: str) -> None:
    """Change PIN for user."""
    with transaction() as c:
        c.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (hash_pin(new_pin), user_id))


# ─── Items ────────────────────────────────────────────────────────────────────

_ITEM_UPDATE_COLS = {
    "barcode", "name", "price_cents", "department",
    "tax_gst", "tax_pst", "bottle_deposit", "age_restricted",
    "is_active",
}


def create_item(
    barcode: Optional[str],
    name: str,
    price_cents: int,
    department: str,
    *,
    gst: int = 1,
    pst: int = 0,
    deposit: str = "none",
    age_restricted: int = 0,
) -> int:
    """Insert item. Returns new id."""
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO items
               (barcode, name, price_cents, department, tax_gst, tax_pst, bottle_deposit, age_restricted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (barcode, name, price_cents, department, gst, pst, deposit, age_restricted),
        )
        return cur.lastrowid


def get_item(item_id: int) -> Optional[dict]:
    """Return item by id."""
    row = conn().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _row_to_dict(row)


def get_item_by_barcode(barcode: str) -> Optional[dict]:
    """Return active item by barcode, or None."""
    row = conn().execute(
        "SELECT * FROM items WHERE barcode = ? AND is_active = 1",
        (barcode,),
    ).fetchone()
    return _row_to_dict(row)


def list_items_by_department(dept: str, active_only: bool = True) -> list[dict]:
    """Return items in a department."""
    sql = "SELECT * FROM items WHERE department = ?"
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY name"
    return [dict(r) for r in conn().execute(sql, (dept,)).fetchall()]


def list_all_items(active_only: bool = True) -> list[dict]:
    """Return all items."""
    sql = "SELECT * FROM items"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY department, name"
    return [dict(r) for r in conn().execute(sql).fetchall()]


def update_item(item_id: int, *, changed_by: str = "system", **fields: Any) -> None:
    """Update item fields. Logs to price_history if price_cents changes."""
    bad = set(fields) - _ITEM_UPDATE_COLS
    if bad:
        raise ValueError(f"unknown item fields: {sorted(bad)}")
    if not fields:
        return
    with transaction() as c:
        old = c.execute(
            "SELECT price_cents FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if old is None:
            raise ValueError(f"item {item_id} not found")

        cols = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [item_id]
        c.execute(
            f"UPDATE items SET {cols}, updated_at = datetime('now') WHERE id = ?",
            params,
        )

        if "price_cents" in fields and fields["price_cents"] != old["price_cents"]:
            c.execute(
                """INSERT INTO price_history
                   (item_id, old_price_cents, new_price_cents, changed_by)
                   VALUES (?, ?, ?, ?)""",
                (item_id, old["price_cents"], fields["price_cents"], changed_by),
            )


def deactivate_item(item_id: int) -> None:
    """Soft-delete item."""
    with transaction() as c:
        c.execute("UPDATE items SET is_active = 0 WHERE id = ?", (item_id,))


def get_price_history(item_id: int) -> list[dict]:
    """Return all price changes for an item, newest first."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM price_history WHERE item_id = ? ORDER BY id DESC",
        (item_id,),
    ).fetchall()]


# ─── Transactions ────────────────────────────────────────────────────────────

def next_transaction_ref(today: Optional[str] = None) -> str:
    """Return next TXN-YYYYMMDD-NNNN ref. Sequential per day."""
    today = today or datetime.now().strftime("%Y%m%d")
    prefix = f"TXN-{today}-"
    row = conn().execute(
        """SELECT transaction_ref FROM transactions
           WHERE transaction_ref LIKE ?
           ORDER BY id DESC LIMIT 1""",
        (prefix + "%",),
    ).fetchone()
    n = 1 if row is None else int(row["transaction_ref"].split("-")[-1]) + 1
    return f"{prefix}{n:04d}"


def insert_transaction(txn: dict, items: list[dict]) -> int:
    """Atomic write: header + line items. Returns new transaction id."""
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO transactions
               (transaction_ref, subtotal_cents, discount_cents, gst_cents, pst_cents,
                deposit_cents, bag_charge_cents, total_cents, rounded_total_cents,
                payment_method, cash_tendered_cents, change_cents, card_amount_cents,
                card_auth_code, card_last4, status, cashier_id, cashier_name, shift_id)
               VALUES (:transaction_ref, :subtotal_cents, :discount_cents, :gst_cents, :pst_cents,
                       :deposit_cents, :bag_charge_cents, :total_cents, :rounded_total_cents,
                       :payment_method, :cash_tendered_cents, :change_cents, :card_amount_cents,
                       :card_auth_code, :card_last4, :status, :cashier_id, :cashier_name, :shift_id)""",
            {
                "transaction_ref": txn["transaction_ref"],
                "subtotal_cents": txn["subtotal_cents"],
                "discount_cents": txn.get("discount_cents", 0),
                "gst_cents": txn["gst_cents"],
                "pst_cents": txn["pst_cents"],
                "deposit_cents": txn["deposit_cents"],
                "bag_charge_cents": txn.get("bag_charge_cents", 0),
                "total_cents": txn["total_cents"],
                "rounded_total_cents": txn["rounded_total_cents"],
                "payment_method": txn["payment_method"],
                "cash_tendered_cents": txn.get("cash_tendered_cents", 0),
                "change_cents": txn.get("change_cents", 0),
                "card_amount_cents": txn.get("card_amount_cents", 0),
                "card_auth_code": txn.get("card_auth_code"),
                "card_last4": txn.get("card_last4"),
                "status": txn.get("status", "completed"),
                "cashier_id": txn.get("cashier_id"),
                "cashier_name": txn.get("cashier_name"),
                "shift_id": txn.get("shift_id"),
            },
        )
        txn_id = cur.lastrowid
        for it in items:
            c.execute(
                """INSERT INTO transaction_items
                   (transaction_id, item_id, name, quantity, unit_price_cents,
                    manual_price_override, deal_id, deal_discount_cents,
                    gst_cents, pst_cents, deposit_cents, line_total_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    txn_id,
                    it.get("item_id"),
                    it["name"],
                    it.get("quantity", 1),
                    it["unit_price_cents"],
                    it.get("manual_price_override", 0),
                    it.get("deal_id"),
                    it.get("deal_discount_cents", 0),
                    it.get("gst_cents", 0),
                    it.get("pst_cents", 0),
                    it.get("deposit_cents", 0),
                    it["line_total_cents"],
                ),
            )
        return txn_id


def get_transaction(txn_id: int) -> Optional[dict]:
    """Return {transaction, items} or None."""
    row = conn().execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if row is None:
        return None
    items = conn().execute(
        "SELECT * FROM transaction_items WHERE transaction_id = ? ORDER BY id",
        (txn_id,),
    ).fetchall()
    return {"transaction": dict(row), "items": [dict(i) for i in items]}


def get_transaction_by_ref(ref: str) -> Optional[dict]:
    """Return {transaction, items} for a TXN ref, or None."""
    row = conn().execute(
        "SELECT id FROM transactions WHERE transaction_ref = ?", (ref,)
    ).fetchone()
    return get_transaction(row["id"]) if row else None


def void_transaction(
    txn_id: int,
    reason: Optional[str],
    authorized_by: str,
    cashier_name: str,
) -> None:
    """Mark transaction voided + write void_log row."""
    with transaction() as c:
        row = c.execute(
            "SELECT total_cents, status FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"transaction {txn_id} not found")
        if row["status"] == "voided":
            raise ValueError(f"transaction {txn_id} already voided")
        c.execute(
            "UPDATE transactions SET status = 'voided' WHERE id = ?", (txn_id,)
        )
        c.execute(
            """INSERT INTO void_log
               (original_transaction_id, action_type, amount_cents, reason, authorized_by, cashier_name)
               VALUES (?, 'void', ?, ?, ?, ?)""",
            (txn_id, row["total_cents"], reason, authorized_by, cashier_name),
        )


def list_transactions_by_date(date_str: str) -> list[dict]:
    """Return all transactions on YYYY-MM-DD (local date). created_at stored in UTC."""
    rows = conn().execute(
        "SELECT * FROM transactions WHERE date(created_at, 'localtime') = ? ORDER BY id",
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_transactions_for_shift(shift_id: int) -> list[dict]:
    """Return all transactions for a shift."""
    rows = conn().execute(
        "SELECT * FROM transactions WHERE shift_id = ? ORDER BY id",
        (shift_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Deals ───────────────────────────────────────────────────────────────────

def create_deal(
    name: str,
    deal_type: str,
    trigger: dict,
    reward: dict,
    start_date: str,
    expiry_date: str,
) -> int:
    """Insert a deal. trigger/reward dicts are JSON-encoded."""
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO deals
               (name, deal_type, trigger_json, reward_json, start_date, expiry_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, deal_type, json.dumps(trigger), json.dumps(reward), start_date, expiry_date),
        )
        return cur.lastrowid


def list_active_deals(today: Optional[str] = None) -> list[dict]:
    """Return active deals valid today. Decoded trigger/reward attached."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    rows = conn().execute(
        """SELECT * FROM deals
           WHERE is_active = 1 AND start_date <= ? AND expiry_date >= ?
           ORDER BY id""",
        (today, today),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["trigger"] = json.loads(d["trigger_json"])
        d["reward"] = json.loads(d["reward_json"])
        out.append(d)
    return out


def expire_deal(deal_id: int) -> None:
    """Mark deal inactive."""
    with transaction() as c:
        c.execute("UPDATE deals SET is_active = 0 WHERE id = ?", (deal_id,))


# ─── Lottery ─────────────────────────────────────────────────────────────────

def log_lottery(
    entry_type: str,
    amount_cents: int,
    cashier_name: str,
    *,
    shift_id: Optional[int] = None,
    description: Optional[str] = None,
    transaction_id: Optional[int] = None,
) -> int:
    """Insert lottery_ledger row. entry_type = 'sale' | 'payout'."""
    if entry_type not in {"sale", "payout"}:
        raise ValueError(f"invalid lottery entry_type: {entry_type}")
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO lottery_ledger
               (entry_type, amount_cents, description, transaction_id, shift_id, cashier_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_type, amount_cents, description, transaction_id, shift_id, cashier_name),
        )
        return cur.lastrowid


def lottery_totals_for_shift(shift_id: int) -> dict:
    """Return {sales, payouts, net} in cents for a shift."""
    row = conn().execute(
        """SELECT
             COALESCE(SUM(CASE WHEN entry_type='sale'   THEN amount_cents END), 0) AS sales,
             COALESCE(SUM(CASE WHEN entry_type='payout' THEN amount_cents END), 0) AS payouts
           FROM lottery_ledger WHERE shift_id = ?""",
        (shift_id,),
    ).fetchone()
    return {
        "sales": row["sales"],
        "payouts": row["payouts"],
        "net": row["sales"] - row["payouts"],
    }


# ─── Held transactions ───────────────────────────────────────────────────────

def hold_transaction(cart_json: str, cashier_name: str, label: Optional[str] = None) -> int:
    """Stash a cart for later retrieval."""
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO held_transactions (hold_label, cart_json, cashier_name) VALUES (?, ?, ?)",
            (label, cart_json, cashier_name),
        )
        return cur.lastrowid


def list_held() -> list[dict]:
    """Return all held carts, newest first."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM held_transactions ORDER BY created_at DESC"
    ).fetchall()]


def retrieve_held(held_id: int) -> Optional[dict]:
    """Pop a held cart (delete + return). None if not found."""
    with transaction() as c:
        row = c.execute(
            "SELECT * FROM held_transactions WHERE id = ?", (held_id,)
        ).fetchone()
        if row is None:
            return None
        c.execute("DELETE FROM held_transactions WHERE id = ?", (held_id,))
        return dict(row)


# ─── Shifts ──────────────────────────────────────────────────────────────────

def open_shift(cashier_id: int, cashier_name: str, opening_float_cents: int) -> int:
    """Open a new shift. Returns shift id."""
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO shifts
               (cashier_id, cashier_name, opening_float_cents, status)
               VALUES (?, ?, ?, 'open')""",
            (cashier_id, cashier_name, opening_float_cents),
        )
        return cur.lastrowid


def close_shift(shift_id: int, closing_cash_cents: int) -> None:
    """Close shift. Records closing cash + timestamp."""
    with transaction() as c:
        c.execute(
            """UPDATE shifts
               SET closing_cash_cents = ?, status = 'closed', closed_at = datetime('now')
               WHERE id = ?""",
            (closing_cash_cents, shift_id),
        )


def get_open_shift(cashier_id: int) -> Optional[dict]:
    """Return cashier's currently open shift, or None."""
    row = conn().execute(
        "SELECT * FROM shifts WHERE cashier_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (cashier_id,),
    ).fetchone()
    return _row_to_dict(row)


def get_shift(shift_id: int) -> Optional[dict]:
    """Return shift by id."""
    row = conn().execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    return _row_to_dict(row)


# ─── Cash events ─────────────────────────────────────────────────────────────

def log_cash_event(
    shift_id: int,
    event_type: str,
    amount_cents: int,
    cashier_name: str,
    note: Optional[str] = None,
) -> int:
    """Log drop / petty_cash / no_sale event for a shift."""
    if event_type not in {"drop", "petty_cash", "no_sale", "till_count"}:
        raise ValueError(f"invalid cash event_type: {event_type}")
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO cash_events
               (shift_id, event_type, amount_cents, note, cashier_name)
               VALUES (?, ?, ?, ?, ?)""",
            (shift_id, event_type, amount_cents, note, cashier_name),
        )
        return cur.lastrowid


def list_cash_events(shift_id: int) -> list[dict]:
    """Return all cash events for a shift."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM cash_events WHERE shift_id = ? ORDER BY id",
        (shift_id,),
    ).fetchall()]


# ─── Admin log ───────────────────────────────────────────────────────────────

def log_admin_action(action: str, admin_name: str, detail: Optional[str] = None) -> int:
    """Log an admin action (void, price_override, refund, settings_change, ...)."""
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO admin_log (action, detail, admin_name) VALUES (?, ?, ?)",
            (action, detail, admin_name),
        )
        return cur.lastrowid


def list_admin_log(limit: int = 200) -> list[dict]:
    """Return recent admin log entries."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM admin_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()]


# ─── Barcode misses ──────────────────────────────────────────────────────────

def log_barcode_miss(barcode: str) -> int:
    """Record an unknown-barcode scan. Upserts: increments scan_count if seen."""
    with transaction() as c:
        existing = c.execute(
            "SELECT id FROM barcode_misses WHERE barcode = ?", (barcode,)
        ).fetchone()
        if existing:
            c.execute(
                """UPDATE barcode_misses
                   SET scan_count = scan_count + 1, last_scanned = datetime('now')
                   WHERE id = ?""",
                (existing["id"],),
            )
            return existing["id"]
        cur = c.execute("INSERT INTO barcode_misses (barcode) VALUES (?)", (barcode,))
        return cur.lastrowid


def list_barcode_misses(limit: int = 100) -> list[dict]:
    """Return recent barcode misses, most-scanned first."""
    return [dict(r) for r in conn().execute(
        """SELECT * FROM barcode_misses
           ORDER BY scan_count DESC, last_scanned DESC LIMIT ?""",
        (limit,),
    ).fetchall()]


def clear_barcode_miss(barcode: str) -> None:
    """Remove a barcode-miss row (called after admin creates the missing item)."""
    with transaction() as c:
        c.execute("DELETE FROM barcode_misses WHERE barcode = ?", (barcode,))
