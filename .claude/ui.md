# UI Rules — CityLink POS (PyQt6)

Touch-first. Sizes are minimums — never shrink. All styles in `ui/styles.py`.

## File tree (`ui/`)

```
ui/
├── main_window.py           App shell, role routing, inactivity timer
├── login.py                 4-digit PIN screen
├── styles.py                CityLink stylesheet (colors, fonts, sizes)
├── cashier/
│   ├── register.py          Main POS screen (cashier home)
│   ├── cart_widget.py       Cart list + qty/remove
│   ├── numpad.py            Reusable numpad
│   ├── departments.py       Dept button grid (scrollable)
│   ├── deals_banner.py      Active deals reminder banner
│   ├── lottery.py           Lottery sale + payout dialogs
│   └── hold.py              Hold / retrieve transaction
└── admin/
    ├── dashboard.py         Admin home grid
    ├── inventory.py         Add/edit/deactivate items + CSV import
    ├── deals_admin.py       Create/edit/expire deals
    ├── reports.py           All reports UI + PDF export
    ├── users.py             Staff PIN management
    ├── cash_management.py   Float, drop, petty cash
    ├── terminal.py          Payment terminal config + test
    └── store_settings.py    Store info, tax rates, features
```

## Colors

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
    'deal_highlight': '#FFF3CD',  # deal applied row bg
}
```

## Sizing (touch-friendly — non-negotiable)

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

## Departments + tax defaults

**Source of truth:** `core/departments.py` — do not duplicate the list here.

Exports:
- `DEPARTMENTS: list[DeptDef]` — full per-dept defs (id, label, color, gst, pst, deposit). 15 entries.
- `DEPT_BY_ID: dict[str, DeptDef]` — id lookup (raises `KeyError` via `get_dept(id)`).
- `DEPT_TAX_DEFAULTS: dict[str, dict]` — tax-only view, consumed by `core/cart.py:add_manual()`.
- `DEPT_COLORS: dict[str, str]` — color-only view, consumed by `ui/styles.py`.

When adding/changing a department, edit `core/departments.py` only. `ui/styles.py` and `core/cart.py` pick it up automatically.


## Register screen layout

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

## Login + inactivity (UI side)

- Login = 4-digit PIN, large touch buttons, navy bg.
- 3 wrong PINs → 5 min lockout (handled in `core/auth.py`, UI shows countdown).
- Inactivity timer in `main_window.py` — reads `config.features.inactivity_timeout_seconds`. Fires → auto-hold open cart, return to PIN screen.
- Admin role skips shift-open dialog and lands on `admin/dashboard.py`.
- Cashier role → shift-open dialog (enter float) → `cashier/register.py`.

## Hard rules

- Never block UI thread. Payment + print = `QThread`.
- Barcode scanner = USB HID = keyboard input ending in `\n`. Capture in `QMainWindow.keyPressEvent`. Forward to `cart.add_item()` after `db.get_item_by_barcode()`.
- All Qt signals use named slots, not lambdas with captured state from outer scope.
