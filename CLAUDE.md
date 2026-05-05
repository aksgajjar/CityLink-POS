# CityLink POS — Master Blueprint v3.0
# BC, Canada | Convenience Store | Multi-location Ready
# Python 3.11 + PyQt6 | Ingenico/PAX POSLINK | macOS dev → Windows prod

---

## CLAUDE CODE BEHAVIOR RULES (Read First — Always)

### Token Efficiency
- Read ONLY the file you are currently editing
- Never re-read files you have already read in this session unless explicitly told
- Before reading any file, check if its content is already in context
- Do not summarize files back to the user — just act on them
- Make surgical changes only — never rewrite a working file
- One task at a time — complete it fully before moving to next

### Accuracy Rules
- If you are not 100% sure about something — STOP and ask
- Never guess at: tax rules, payment protocol, DB schema, file paths
- If a requirement is ambiguous — ask ONE specific question before proceeding
- Never assume a feature works — state what needs to be tested

### Change Management
- When making a change: state WHAT you changed and WHERE (file + line)
- Never change logic in a file unless that file is the current task
- If a change in File A requires a change in File B — flag it, ask before touching File B
- Keep a mental note of what is complete vs what is stub

### Code Quality
- All money = integers in cents — NEVER float — no exceptions
- No raw SQL outside core/db.py — ever
- No hardcoded values — everything from config.json or DB
- Every function has a docstring (one line is enough)
- Every DB write has try/except with explicit rollback
- Payment and print always run in QThread — never block UI thread
- No force unwrap, no bare except, no print() for errors — use logger

### When Stuck
- Do not hallucinate a solution
- Do not write placeholder code and move on
- Stop, describe the exact problem, ask for direction

---

## PROJECT OVERVIEW

Production-grade POS for CityLink convenience stores (BC, Canada).
- Touch-friendly — any employee can operate register
- Admin panel completely separate from cashier view
- Runs on macOS (dev/test) and Windows 10/11 (production .exe)
- Semi-integrated payment: Ingenico Desk 5000 / PAX via POSLINK TCP
- BC tax compliant: GST 5%, PST 7%, bottle deposit
- No monthly fees — fully owned software
- Multi-location ready (Phase 2 cloud sync)

---

## TECH STACK

```
UI:           Python 3.11 + PyQt6
DB:           SQLite via sqlite3 (local, offline-first)
Receipts:     python-escpos (ESC/POS thermal printer)
Reports:      ReportLab (PDF)
Payment:      Abstract class → Ingenico / PAX / Mock adapters
Packaging:    PyInstaller → .exe (Windows) / .app (macOS test)
Config:       config.json (one per store location)
Logging:      Python logging module → errors.log (rotating, 10MB)
```

---

## FILE STRUCTURE

```
citylink-pos/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .gitignore
├── main.py                      ← Entry point: login → role routing
├── config.json                  ← Store config (edit per location)
│
├── core/
│   ├── db.py                    ← ALL database operations (CRUD only here)
│   ├── models.py                ← Dataclasses: Item, Transaction, CartItem, Deal
│   ├── tax.py                   ← BC tax engine (GST, PST, deposit, rounding)
│   ├── cart.py                  ← Cart: add/remove/qty/totals/split/deals
│   ├── deals.py                 ← Deal engine: load active deals, auto-apply
│   ├── receipt.py               ← ESC/POS receipt builder
│   ├── reports.py               ← All report data (EOD, tax, lottery, cashier)
│   ├── auth.py                  ← PIN hash (SHA-256), role check, lockout
│   ├── logger.py                ← Rotating file logger setup
│   └── payment/
│       ├── base.py              ← Abstract PaymentTerminal
│       ├── ingenico.py          ← Ingenico Desk 5000 POSLINK TCP
│       ├── pax.py               ← PAX POSLINK TCP
│       ├── mock.py              ← Mock: returns APPROVED after 2s (dev only)
│       └── detector.py          ← Auto-detect terminal from config on startup
│
├── ui/
│   ├── main_window.py           ← App shell, role routing, inactivity timer
│   ├── login.py                 ← 4-digit PIN screen
│   ├── styles.py                ← CityLink stylesheet (colors, fonts, sizes)
│   │
│   ├── cashier/
│   │   ├── register.py          ← Main POS screen (cashier home)
│   │   ├── cart_widget.py       ← Cart list with qty/remove controls
│   │   ├── numpad.py            ← Reusable numpad widget
│   │   ├── departments.py       ← Department button grid (scrollable)
│   │   ├── deals_banner.py      ← Active deals reminder banner
│   │   ├── lottery.py           ← Lottery sale + payout dialogs
│   │   └── hold.py              ← Hold / retrieve transaction
│   │
│   └── admin/
│       ├── dashboard.py         ← Admin home grid
│       ├── inventory.py         ← Add / edit / deactivate items + CSV import
│       ├── deals_admin.py       ← Create / edit / expire deals
│       ├── reports.py           ← All reports UI + PDF export
│       ├── users.py             ← Staff PIN management
│       ├── cash_management.py   ← Float, cash drop, petty cash
│       ├── terminal.py          ← Payment terminal config + test
│       └── store_settings.py    ← Store info, tax rates, features
│
├── data/
│   └── store.db                 ← Auto-created on first run
│
├── exports/                     ← PDF reports, CSV label exports land here
│
└── assets/
    ├── logo.png                 ← CityLink navy logo
    └── icons/
```

---

## config.json

```json
{
  "store": {
    "name": "CityLink Convenience",
    "location_id": "LOC001",
    "address": "Station Name, Vancouver, BC",
    "terminal_id": "T001"
  },
  "tax": {
    "gst_rate": 0.05,
    "pst_rate": 0.07,
    "bottle_deposit_355ml": 0.10,
    "bottle_deposit_1L": 0.25,
    "bag_charge_cents": 25
  },
  "payment": {
    "terminal_type": "mock",
    "connection": "tcp",
    "host": "192.168.1.100",
    "port": 9100,
    "timeout_seconds": 60
  },
  "printer": {
    "type": "escpos",
    "connection": "usb",
    "vendor_id": "0x04b8",
    "product_id": "0x0202"
  },
  "features": {
    "lottery_enabled": true,
    "bottle_deposit_enabled": true,
    "bag_charge_enabled": true,
    "require_pin_for_void": true,
    "require_pin_for_price_override": true,
    "inactivity_timeout_seconds": 120
  },
  "sync": {
    "enabled": false,
    "cloudflare_worker_url": "",
    "api_key": ""
  }
}
```

---

## DATABASE SCHEMA (core/db.py creates this on first run)

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

---

## UI — CITYLINK THEME (ui/styles.py)

### Colors
```python
COLORS = {
    # Brand (CityLink navy logo)
    'navy':           '#1B3A6B',
    'blue_mid':       '#2E5BA8',
    'blue_light':     '#5B9BD5',
    'bg':             '#F4F6F9',
    'white':          '#FFFFFF',

    # Departments
    'candy':          '#FF6B6B',
    'drinks':         '#4A90D9',
    'carbonated':     '#E74C3C',
    'non_carbonated': '#E67E22',
    'snacks':         '#F39C12',
    'confectionery':  '#D4AC0D',
    'medicine':       '#27AE60',
    'stationary':     '#8E44AD',
    'gift_items':     '#E91E8C',
    'gift_cards':     '#16A085',
    'ice_cream':      '#FF8C42',
    'slush':          '#00BCD4',
    'lottery':        '#6C3483',
    'accessories':    '#546E7A',
    'retail':         '#1B6B3A',

    # Action buttons
    'btn_cash':       '#27AE60',
    'btn_card':       '#E74C3C',
    'btn_hold':       '#2E5BA8',
    'btn_void':       '#7F8C8D',
    'btn_cancel':     '#E74C3C',
    'btn_lottery_s':  '#6C3483',
    'btn_lottery_p':  '#E67E22',
    'btn_no_sale':    '#546E7A',
    'btn_split':      '#2E5BA8',

    # Status
    'success':        '#27AE60',
    'warning':        '#F39C12',
    'danger':         '#E74C3C',
    'text_dark':      '#1A1A1A',
    'text_muted':     '#7F8C8D',
    'deal_highlight': '#FFF3CD',  -- deal applied row bg
}
```

### Sizing (touch-friendly — non-negotiable)
```python
SIZES = {
    'dept_btn':    (90, 65),    # min px
    'numpad_btn':  (70, 60),
    'action_btn':  (110, 65),
    'font_header': ('Segoe UI', 11, 'Bold'),
    'font_dept':   ('Segoe UI', 11, 'Bold'),
    'font_cart':   ('Segoe UI', 13),
    'font_total':  ('Segoe UI', 18, 'Bold'),
    'font_numpad': ('Segoe UI', 20, 'Bold'),
    'font_action': ('Segoe UI', 12, 'Bold'),
    'font_amount': ('Segoe UI', 28, 'Bold'),
}
```

---

## REGISTER SCREEN LAYOUT

```
┌──────────────────────────────────────────────────────────────────────┐
│ [Logo] CITYLINK CONVENIENCE       Cashier: John   3:45 PM   [● TCP]  │ navy
├───────────────────────────────────────────────────────────────────────┤
│ 🔔 ACTIVE DEALS: Buy 2 Monster save $1.00 | 3 Pepsi for $5.00        │ yellow
├────────────────────────┬─────────────────────────────────────────────┤
│ CART                   │ DEPARTMENTS                                  │
│ ─────────────────────  │ [Candy][Drinks][Carbonated][Non-Carb]        │
│ Coca Cola 2x   $4.98   │ [Snacks][Confect][Medicine][Stationary]      │
│   PST $0.35            │ [Gift Items][Gift Cards][Ice Cream][Slush]   │
│ 💰 3-Pepsi Deal -$0.97 │ [Lottery][Accessories][Retail]               │
│ Kit Kat        $1.99   ├─────────────────────────────────────────────┤
│                        │ NUMPAD                                       │
│ ─────────────────────  │ [7][8][9]  [LOTTERY+][$20][$10]             │
│ Subtotal:     $12.45   │ [4][5][6]  [LOTTERY-][$5 ][QTY]            │
│ Discount:     -$0.97   │ [1][2][3]  [  CARD  ][CASH]                │
│ GST (5%):      $0.57   │ [0][00][.] [  VOID  ][BAG ]                │
│ PST (7%):      $0.63   ├─────────────────────────────────────────────┤
│ Deposit:       $0.20   │ [Hold][Retrieve][No Sale][Split][Price Chk]  │
│ ━━━━━━━━━━━━━━━━━━━━━  │ [Cancel Item][Clear Cart][Override Price]    │
│ TOTAL:        $12.88   │                                              │
│ (Cash→$12.90)          │                                              │
├────────────────────────┴─────────────────────────────────────────────┤
│ [≡ Menu]  [Calculator]  [Receipts]  [Reprint]  [EOD]  [Admin ▶]      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## DEPARTMENTS + TAX DEFAULTS

```python
DEPARTMENTS = [
    {"id":"candy",          "label":"Candy",          "color":"#FF6B6B", "gst":1,"pst":1,"deposit":"none"},
    {"id":"drinks",         "label":"Drinks",         "color":"#4A90D9", "gst":1,"pst":0,"deposit":"355ml"},
    {"id":"carbonated",     "label":"Carbonated",     "color":"#E74C3C", "gst":1,"pst":1,"deposit":"355ml"},
    {"id":"non_carbonated", "label":"Non-Carbonated", "color":"#E67E22", "gst":1,"pst":0,"deposit":"355ml"},
    {"id":"snacks",         "label":"Snacks",         "color":"#F39C12", "gst":1,"pst":1,"deposit":"none"},
    {"id":"confectionery",  "label":"Confectionery",  "color":"#D4AC0D", "gst":1,"pst":1,"deposit":"none"},
    {"id":"medicine",       "label":"Medicine",       "color":"#27AE60", "gst":0,"pst":0,"deposit":"none"},
    {"id":"stationary",     "label":"Stationary",     "color":"#8E44AD", "gst":1,"pst":0,"deposit":"none"},
    {"id":"gift_items",     "label":"Gift Items",     "color":"#E91E8C", "gst":1,"pst":0,"deposit":"none"},
    {"id":"gift_cards",     "label":"Gift Cards",     "color":"#16A085", "gst":0,"pst":0,"deposit":"none"},
    {"id":"ice_cream",      "label":"Ice Cream",      "color":"#FF8C42", "gst":1,"pst":1,"deposit":"none"},
    {"id":"slush",          "label":"Slush",          "color":"#00BCD4", "gst":1,"pst":1,"deposit":"none"},
    {"id":"lottery",        "label":"Lottery",        "color":"#6C3483", "gst":0,"pst":0,"deposit":"none"},
    {"id":"accessories",    "label":"Accessories",    "color":"#546E7A", "gst":1,"pst":0,"deposit":"none"},
    {"id":"retail",         "label":"Retail",         "color":"#1B6B3A", "gst":1,"pst":0,"deposit":"none"},
]
```

---

## BC TAX ENGINE (core/tax.py)

```python
def calculate_line_tax(price_cents: int, gst: int, pst: int,
                        deposit: str, qty: int = 1) -> dict:
    """Calculate tax for one line item. All values in cents."""
    subtotal = price_cents * qty
    gst_amt  = round(subtotal * 0.05) if gst else 0
    pst_amt  = round(subtotal * 0.07) if pst else 0
    dep_map  = {'none': 0, '355ml': 10, '1L': 25}
    dep_amt  = dep_map.get(deposit, 0) * qty
    return {
        'subtotal':   subtotal,
        'gst':        gst_amt,
        'pst':        pst_amt,
        'deposit':    dep_amt,
        'line_total': subtotal + gst_amt + pst_amt + dep_amt
    }

def apply_cash_rounding(total_cents: int) -> int:
    """Canada cash rounding to nearest $0.05. Card = no rounding."""
    remainder = total_cents % 5
    if remainder < 3:
        return total_cents - remainder
    else:
        return total_cents + (5 - remainder)
```

---

## DEALS ENGINE (core/deals.py)

### Deal Types
```
BUNDLE       → Item A + Item B together = fixed price
               trigger: {items: [id_A, id_B]}, reward: {fixed_price_cents: 399}

QTY_DISCOUNT → Buy X of same item = fixed total price
               trigger: {item_id: X, qty: 3}, reward: {total_price_cents: 500}

CROSS_DEPT   → Buy from dept A = discount on dept B item
               trigger: {dept: "drinks"}, reward: {target_dept: "snacks", discount_pct: 50}

SPEND_DISC   → Buy X qty of item = $Y off
               trigger: {item_id: X, qty: 2}, reward: {discount_cents: 100}
```

### Auto-Apply Logic
```
On every cart change:
1. Load all active deals (expiry_date >= today, is_active=1)
2. Check each deal's trigger against current cart items
3. If triggered: apply discount, mark line item with deal_id
4. If partially triggered (e.g. buy 2, only 1 scanned):
   → Flash deals banner: "Add 1 more Monster → save $1.00!"
5. Tax calculated on price AFTER deal discount
```

### Deal Reminder Banner (ui/cashier/deals_banner.py)
```
Static banner below header — always visible during cashier shift
Lists all active deals by name
When a deal is triggered in cart → that deal row highlights green
When 1 item away from deal → that row highlights yellow + nudge text
Expired deals auto-remove from banner
```

---

## PAYMENT FLOWS

### Cash
```
[CASH] → numpad "Amount Tendered"
→ $10/$20 shortcut buttons
→ Cash rounding shown: "Total $12.88 → Cash $12.90"
→ Change displayed large
→ Cash drawer signal sent via printer
→ Receipt prints
→ Cart clears
```

### Card (POSLINK TCP)
```
[CARD] → total_cents sent to terminal
→ UI shows "Waiting for terminal... $XX.XX"
→ QThread polls terminal response
→ APPROVED  → receipt, cart clear
→ DECLINED  → dialog, return to cart
→ TIMEOUT   → dialog with manual override option
→ No cash rounding applied for card
```

### Split
```
[SPLIT] → "Cash portion?" numpad
→ Remainder = card
→ Cash rounded, card exact
→ Both logged in transaction
```

---

## LOTTERY FLOW

```
BCLC machine is SEPARATE HARDWARE.
POS tracks money only — not ticket types.

[LOTTERY SALE] (+):
→ Numpad "Sale Amount"
→ Optional label: Lotto Max / BC49 / Extra / Scratch
→ Added to cart — GST=0, PST=0, deposit=none
→ lottery_ledger: type='sale'

[LOTTERY PAYOUT] (-):
→ Numpad "Payout Amount"
→ NOT in cart — this is cash OUT
→ Cash drawer opens
→ lottery_ledger: type='payout'
→ Deducted in EOD cash reconciliation
```

---

## VOID FLOW

```
[VOID] on register:
→ Option A: Remove selected item from open cart
→ Option B: Void completed transaction (enter receipt #)
→ Admin PIN required (if feature enabled in config)
→ Reason text entry (optional but logged)
→ void_log entry created
→ Original transaction status → 'voided'
→ Void receipt printed
```

---

## LOGIN + SHIFT SYSTEM

```
App opens → PIN screen (4-digit, large touch buttons)
→ 3 wrong attempts → 5 min lockout, logged
→ Cashier role → shift open dialog (enter float) → register
→ Admin role   → admin dashboard (no shift required)

Shift open: cashier enters opening float → shifts table
Shift close: cashier counts cash → EOD prints → shift closed

Two cashiers same day = two separate shifts (sequential, not concurrent)

Inactivity: X seconds (config) → auto-return to PIN screen
```

---

## REPORTS (core/reports.py → PDF via ReportLab)

```
EOD Report:
  - Transaction count, gross, voids, net
  - Tax collected: GST / PST / deposits / bag charges
  - Payment split: cash / card
  - Lottery: sales / payouts / net
  - Department breakdown
  - Cash reconciliation: float + sales - payouts = expected

Additional reports (admin only):
  - Hourly sales (bar chart in PDF)
  - Weekly / monthly summary
  - Best selling items (top 20)
  - Cashier performance (per shift)
  - Void / override log
  - Lottery monthly net
  - Tax summary (CRA-ready: GST + PST totals by period)
  - Bottle deposit collected
  - Barcode misses log (items to add)
  - Deal performance (which deals triggered most)

All reports: PDF to exports/ folder + option to print
```

---

## INVENTORY (admin/inventory.py)

```
Add item:
  Barcode (scan or type) | Name | Price | Department
  GST/PST flags (auto-filled from dept defaults, editable)
  Bottle deposit | Age restricted | Active

Edit item:
  Same form, pre-filled
  Price change → logged to price_history automatically

Deactivate:
  is_active = 0 (never hard delete)

CSV bulk import:
  Columns: barcode, name, price, department, gst, pst, deposit
  Validates each row, shows errors, imports valid rows
  Duplicate barcode = update existing item

Price label export:
  Export selected items as CSV: name, price, barcode
  Compatible with standard label printer software
  
Barcode miss log:
  Admin can review unknown scans and add items from there
```

---

## CASH MANAGEMENT (admin/cash_management.py)

```
Opening float:   Enter at shift start — recorded in shifts table
Cash drop:       Mid-day remove cash to safe — logged in cash_events
Petty cash out:  Small expense — amount + note — logged in cash_events
No sale:         Cash drawer open without transaction — logged in cash_events
Till count:      Anytime cash count — compared to expected — logged
```

---

## SECURITY + ADMIN LOG

```
Admin PIN required for:
  - Void completed transaction
  - Price override
  - Refund (not in scope but hook exists)
  - Access admin panel

Every admin PIN use → logged in admin_log with action + timestamp

Wrong PIN:
  - 3 attempts → lockout 5 minutes
  - Lockout event logged

Inactivity timeout:
  - Configurable in config.json
  - Returns to PIN screen
  - Current open cart preserved (held automatically)
```

---

## PAYMENT TERMINAL (core/payment/)

```python
# base.py
@dataclass
class PaymentRequest:
    amount_cents: int       # no rounding — exact amount
    transaction_ref: str

@dataclass
class PaymentResponse:
    approved: bool
    auth_code: str = ""
    card_last4: str = ""
    error_message: str = ""

class PaymentTerminal(ABC):
    def connect(self) -> bool: ...
    def is_connected(self) -> bool: ...
    def request_payment(self, req: PaymentRequest) -> PaymentResponse: ...
    def disconnect(self): ...

# mock.py
# Sleeps 2 seconds, returns approved=True
# Use terminal_type: "mock" in config.json for dev

# ingenico.py
# POSLINK 2.0 over TCP
# Send XML to host:port, parse ResponseCode + AuthCode + PAN
# Timeout: config.payment.timeout_seconds

# detector.py
# Reads config terminal_type
# Tries TCP connect on startup
# Sets header status dot: green (connected) / red (cash-only)
# Re-scan available from admin terminal settings screen
```

---

## ITEM SCAN FLOW

```
QMainWindow.keyPressEvent catches barcode scanner input
(USB HID scanner = fast keyboard input ending in Enter)

On barcode received:
1. db.get_item_by_barcode(barcode)
2. Found + active:
   a. cart.add_item(item)
   b. deals.check_cart() → auto-apply if triggered
   c. If age_restricted: show "Verify Age 18+" dialog
3. Not found:
   a. Log to barcode_misses (increment count if exists)
   b. Dialog: [Add New Item] / [Manual Price Entry] / [Cancel]
4. Manual price entry:
   a. Enter price → select department
   b. Tax from DEPT_TAX_DEFAULTS
   c. Added as unlisted item (item_id = NULL in transaction_items)
```

---

## PHASE 1 BUILD ORDER (Strict — do not skip or reorder)

```
FOUNDATION
1.  core/logger.py           Logging setup
2.  core/db.py               Schema creation + all CRUD functions
3.  core/models.py           All dataclasses
4.  core/tax.py              Tax engine + cash rounding
5.  core/auth.py             PIN hash + role check + lockout
6.  core/cart.py             Cart logic + totals

UI BASE
7.  ui/styles.py             Full CityLink stylesheet
8.  ui/login.py              PIN screen
9.  ui/cashier/numpad.py     Reusable numpad widget
10. ui/cashier/cart_widget   Cart display + qty controls
11. ui/cashier/departments   Department grid
12. ui/cashier/register.py   Full register screen (cash only first)
13. main.py                  Entry + window routing + barcode event filter

── CHECKPOINT 1: scan item → cart → cash payment → receipt prints ──

PAYMENT
14. core/payment/base.py     Abstract class
15. core/payment/mock.py     Mock adapter (dev testing)
16. core/payment/ingenico    POSLINK TCP
17. core/payment/detector    Auto-detect + header status

── CHECKPOINT 2: card payment flow with mock terminal ──

DEALS + LOTTERY
18. core/deals.py            Deal engine + auto-apply
19. ui/cashier/deals_banner  Active deals banner
20. ui/cashier/lottery.py    Sale + payout dialogs

── CHECKPOINT 3: deals auto-apply + lottery in/out ──

REPORTS + ADMIN
21. core/reports.py          All report data functions
22. ui/admin/inventory.py    Add/edit/CSV import
23. ui/admin/deals_admin     Create/edit deals
24. ui/admin/cash_management Float/drop/petty cash
25. ui/admin/reports.py      Reports UI + PDF export
26. ui/admin/users.py        Staff management
27. ui/admin/dashboard       Admin home grid

── CHECKPOINT 4: full day simulation — open shift, sell, EOD ──
```

---

## MACOS DEV + WINDOWS PROD

```bash
# macOS setup
pip install PyQt6 python-escpos reportlab pyinstaller pyserial

# Run dev (config: terminal_type = "mock", printer will gracefully skip)
python main.py

# Build macOS .app for testing
pyinstaller --onefile --windowed \
  --add-data "assets:assets" \
  --add-data "config.json:." \
  --name "CityLinkPOS" main.py

# Build Windows .exe (run on Windows machine)
pyinstaller --onefile --windowed \
  --add-data "assets;assets" \
  --add-data "config.json;." \
  --icon "assets/logo.ico" \
  --name "CityLinkPOS" main.py
```

---

## PHASE 2 (After Phase 1 stable — do not build now)

- Cloudflare D1 sync (transactions nightly push)
- Mobile reporting web app
- Multi-location admin dashboard
- Customer loyalty / points system
- Inventory count tracking

---

## EXPLICITLY OUT OF SCOPE

- Alcohol sales (not sold)
- Returns / refunds
- Supplier / purchase orders
- Multi-till (one register per store)
- Customer-facing display
- Online ordering
- Email receipts
- Item notes on cart

---

*CityLink POS v3.0 | aksgajjar/CityLink-POS*
*BC Canada | Python 3.11 + PyQt6 | Ingenico/PAX POSLINK*
