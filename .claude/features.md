# Features Rules — CityLink POS

Deals, lottery, voids, shifts, reports, inventory, cash mgmt, security, item scan, build order, packaging, config.

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

## Deals (`core/deals.py` + `ui/cashier/deals_banner.py`)

### Types

| Type | Trigger JSON | Reward JSON |
|---|---|---|
| `bundle` | `{items: [id_A, id_B]}` | `{fixed_price_cents: 399}` |
| `qty_discount` | `{item_id: X, qty: 3}` | `{total_price_cents: 500}` |
| `cross_dept` | `{dept: "drinks"}` | `{target_dept: "snacks", discount_pct: 50}` |
| `spend_discount` | `{item_id: X, qty: 2}` | `{discount_cents: 100}` |

### Auto-apply

```
On every cart change:
1. Load active deals (expiry_date >= today AND is_active=1)
2. Check each deal's trigger against current cart
3. If triggered → apply discount, mark line item with deal_id
4. If partially triggered (e.g. buy 2, only 1 scanned):
   → flash deals banner: "Add 1 more Monster → save $1.00!"
5. Tax calculated on price AFTER deal discount (see tax.md)
```

### Banner

- Static below header, always visible during cashier shift
- Lists active deals by name
- Triggered deal row → green
- 1 item away → yellow + nudge text
- Expired deals auto-hide

---

## Lottery (`ui/cashier/lottery.py` + `lottery_ledger`)

BCLC machine = separate hardware. POS tracks money only — **never ticket types**.

### Sale (+)
- Numpad "Sale Amount"
- Optional label: Lotto Max / BC49 / Extra / Scratch
- Added to cart — `gst=0, pst=0, deposit=none`
- `lottery_ledger`: `entry_type='sale'`

### Payout (−)
- Numpad "Payout Amount"
- **Not in cart** — this is cash OUT
- Cash drawer opens
- `lottery_ledger`: `entry_type='payout'`
- Deducted in EOD cash reconciliation

---

## Void

```
[VOID] on register:
A. Remove selected item from open cart
B. Void completed transaction (enter receipt #)
Admin PIN required if config.features.require_pin_for_void = true
Reason text entry (optional but logged)
void_log row created
Original transaction.status → 'voided'
Void receipt printed
```

---

## Shifts

- Cashier login → shift-open dialog (enter float) → `shifts` row inserted, `status='open'`
- Cashier close shift → count cash → EOD prints → `closed_at` + `closing_cash_cents` set
- Two cashiers same day = two **sequential** shifts. No concurrent shifts.
- Admin role → no shift required.

---

## Reports (`core/reports.py` → ReportLab PDF in `exports/`)

### EOD (auto-prints on shift close)
- Transaction count, gross, voids, net
- Tax collected: GST / PST / deposits / bag charges
- Payment split: cash / card
- Lottery: sales / payouts / net
- Department breakdown
- Cash reconciliation: `float + cash_sales − payouts − drops + petty − no_sale_drift = expected`

### Admin-only
- Hourly sales (bar chart)
- Weekly / monthly summary
- Best selling items (top 20)
- Cashier performance (per shift)
- Void / override log
- Lottery monthly net
- Tax summary (CRA-ready: GST + PST totals by period)
- Bottle deposit collected
- Barcode misses log
- Deal performance (most-triggered)

All reports → PDF to `exports/` + optional print.

---

## Inventory (`ui/admin/inventory.py`)

- **Add**: barcode (scan/type) | name | price | dept | GST/PST (auto from dept defaults, editable) | deposit | age_restricted | active
- **Edit**: same form pre-filled. Price change → auto-log to `price_history`.
- **Deactivate**: `is_active = 0`. Never hard delete.
- **CSV import**: columns `barcode, name, price, department, gst, pst, deposit`. Validate per row, show errors, import valid rows. Duplicate barcode = update existing.
- **Price-label export**: selected items → CSV `name, price, barcode`. Standard label printer compatible.
- **Barcode misses**: admin reviews → "Add as item" inline.

---

## Cash management (`ui/admin/cash_management.py`)

| Action | Effect |
|---|---|
| Opening float | At shift start, written to `shifts.opening_float_cents` |
| Cash drop | Mid-day to safe → `cash_events` `event_type='drop'` |
| Petty cash out | Amount + note → `cash_events` `event_type='petty_cash'` |
| No sale | Drawer open without txn → `cash_events` `event_type='no_sale'` |
| Till count | Anytime cash count vs expected → logged |

---

## Security + admin log

Admin PIN required for:
- Void completed transaction
- Price override
- Refund (out of scope but hook exists)
- Access admin panel

Every admin PIN use → `admin_log` row with action + timestamp.

PIN failures: 3 attempts → 5 min lockout. Lockout logged.

Inactivity timeout: configurable. Returns to PIN screen. Open cart auto-held.

---

## Item scan flow

```
QMainWindow.keyPressEvent catches USB-HID scanner input (ends with Enter)

1. db.get_item_by_barcode(barcode)
2. Found + active:
   a. cart.add_item(item)
   b. deals.check_cart() → auto-apply if triggered
   c. If age_restricted: "Verify Age 18+" dialog
3. Not found:
   a. Log to barcode_misses (increment if exists)
   b. Dialog: [Add New Item] / [Manual Price Entry] / [Cancel]
4. Manual price entry:
   a. Enter price → select department
   b. Tax from DEPT_TAX_DEFAULTS (see ui.md)
   c. Added as unlisted item (item_id = NULL in transaction_items)
```

---

## Phase 1 build order (strict — do not skip or reorder)

```
FOUNDATION
1.  core/logger.py           Logging setup
2.  core/db.py               Schema creation + all CRUD
3.  core/models.py           All dataclasses
4.  core/tax.py              Tax engine + cash rounding
5.  core/auth.py             PIN hash + role check + lockout
6.  core/cart.py             Cart logic + totals

UI BASE
7.  ui/styles.py             Full CityLink stylesheet
8.  ui/login.py              PIN screen
9.  ui/cashier/numpad.py     Reusable numpad
10. ui/cashier/cart_widget   Cart display + qty controls
11. ui/cashier/departments   Department grid
12. ui/cashier/register.py   Full register screen (cash only first)
13. main.py                  Entry + routing + barcode event filter

── CHECKPOINT 1: scan → cart → cash payment → receipt prints ──

PAYMENT
14. core/payment/base.py     Abstract class
15. core/payment/mock.py     Mock adapter
16. core/payment/ingenico    POSLINK TCP
17. core/payment/detector    Auto-detect + header status

── CHECKPOINT 2: card payment with mock terminal ──

DEALS + LOTTERY
18. core/deals.py            Engine + auto-apply
19. ui/cashier/deals_banner  Active deals banner
20. ui/cashier/lottery.py    Sale + payout dialogs

── CHECKPOINT 3: deals auto-apply + lottery in/out ──

REPORTS + ADMIN
21. core/reports.py          All report data
22. ui/admin/inventory.py    Add/edit/CSV import
23. ui/admin/deals_admin     Create/edit deals
24. ui/admin/cash_management Float/drop/petty
25. ui/admin/reports.py      Reports UI + PDF
26. ui/admin/users.py        Staff mgmt
27. ui/admin/dashboard       Admin home

── CHECKPOINT 4: full day simulation — open shift, sell, EOD ──
```

---

## Packaging

```bash
# macOS dev setup
pip install PyQt6 python-escpos reportlab pyinstaller pyserial

# Run dev (terminal_type=mock, printer skips gracefully)
python main.py

# macOS .app (testing)
pyinstaller --onefile --windowed \
  --add-data "assets:assets" \
  --add-data "config.json:." \
  --name "CityLinkPOS" main.py

# Windows .exe (build on Windows)
pyinstaller --onefile --windowed \
  --add-data "assets;assets" \
  --add-data "config.json;." \
  --icon "assets/logo.ico" \
  --name "CityLinkPOS" main.py
```

---

## Phase 2 (DO NOT BUILD NOW)

- Cloudflare D1 sync (transactions nightly push)
- Mobile reporting web app
- Multi-location admin dashboard
- Customer loyalty / points
- Inventory count tracking

## Out of scope (do not propose)

- Alcohol sales
- Returns / refunds
- Supplier / purchase orders
- Multi-till per store
- Customer-facing display
- Online ordering
- Email receipts
- Item notes on cart
