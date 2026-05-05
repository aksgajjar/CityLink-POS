# Database Rules — CityLink POS

All DB code lives in `core/db.py`. **No raw SQL anywhere else.**

## Hard rules

- All money columns end in `_cents`, type `INTEGER`. Never `REAL`/float.
- Every write wraps `try/except` with explicit `conn.rollback()` on error.
- No hard delete. Use `is_active = 0` (items, users).
- Price changes auto-log to `price_history` (old + new + who + when).
- Schema created on first run (idempotent `CREATE TABLE IF NOT EXISTS`).
- Connection: `sqlite3` with `PARSE_DECLTYPES`, `foreign_keys = ON` pragma.
- Transactions table ref format: `TXN-YYYYMMDD-NNNN` (sequential per day).

## Schema

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,          -- SHA-256
    role TEXT NOT NULL DEFAULT 'cashier',  -- 'cashier' | 'admin'
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL,    -- ALWAYS cents, never float
    department TEXT NOT NULL,
    tax_gst INTEGER DEFAULT 1,       -- 1=taxable
    tax_pst INTEGER DEFAULT 0,
    bottle_deposit TEXT DEFAULT 'none',  -- 'none'|'355ml'|'1L'
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
    transaction_ref TEXT UNIQUE NOT NULL,  -- TXN-YYYYMMDD-NNNN
    subtotal_cents INTEGER NOT NULL,
    discount_cents INTEGER DEFAULT 0,
    gst_cents INTEGER NOT NULL,
    pst_cents INTEGER NOT NULL,
    deposit_cents INTEGER NOT NULL,
    bag_charge_cents INTEGER DEFAULT 0,
    total_cents INTEGER NOT NULL,
    rounded_total_cents INTEGER NOT NULL,  -- cash rounding applied
    payment_method TEXT NOT NULL,          -- 'cash'|'card'|'split'
    cash_tendered_cents INTEGER DEFAULT 0,
    change_cents INTEGER DEFAULT 0,
    card_amount_cents INTEGER DEFAULT 0,
    card_auth_code TEXT,
    card_last4 TEXT,
    status TEXT DEFAULT 'completed',       -- 'completed'|'voided'|'held'
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
    deal_type TEXT NOT NULL,  -- 'bundle'|'qty_discount'|'cross_dept'|'spend_discount'
    trigger_json TEXT NOT NULL,   -- JSON: items/depts that trigger deal
    reward_json TEXT NOT NULL,    -- JSON: discount applied
    start_date TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lottery_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,   -- 'sale'|'payout'
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
    action_type TEXT NOT NULL,  -- 'void'
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
    status TEXT DEFAULT 'open',  -- 'open'|'closed'
    opened_at TEXT DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS cash_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,  -- 'drop'|'petty_cash'|'no_sale'
    amount_cents INTEGER NOT NULL,
    note TEXT,
    cashier_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,      -- 'void'|'price_override'|'refund'|'settings_change'
    detail TEXT,
    admin_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS barcode_misses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT NOT NULL,
    scan_count INTEGER DEFAULT 1,
    last_scanned TEXT DEFAULT (datetime('now'))
);
```

## Barcode-miss flow (DB side)

On unknown scan: `INSERT … ON CONFLICT(barcode) DO UPDATE SET scan_count = scan_count + 1, last_scanned = datetime('now')`. Admin reviews via `barcode_misses` to add real items.
