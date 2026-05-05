# Payment Rules — CityLink POS

Semi-integrated. POS sends amount → terminal handles card → POS gets approved/declined. **Payment always runs in `QThread`** — never block UI.

## File tree (`core/payment/`)

```
core/payment/
├── base.py        Abstract PaymentTerminal + dataclasses
├── ingenico.py    Ingenico Desk 5000 POSLINK TCP
├── pax.py         PAX POSLINK TCP
├── mock.py        Returns APPROVED after 2s (dev only)
└── detector.py    Auto-detect terminal from config.json on startup
```

## Contracts (`base.py`)

```python
@dataclass
class PaymentRequest:
    amount_cents: int       # exact, no rounding
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
```

## Adapters

| Adapter | Behavior |
|---|---|
| `mock.py` | Sleep 2s, return `approved=True`. Use `terminal_type: "mock"` in dev. |
| `ingenico.py` | POSLINK 2.0 over TCP. XML to `host:port`. Parse `ResponseCode` + `AuthCode` + `PAN` last 4. Timeout from `config.payment.timeout_seconds`. |
| `pax.py` | Same POSLINK protocol, PAX-specific tweaks. |
| `detector.py` | Read `config.payment.terminal_type`. Try `connect()` on app startup. Set header status dot: green=connected, red=cash-only. Re-scan available from admin terminal screen. |

## Flows

### Cash
```
[CASH] → numpad "Amount Tendered"
$10/$20 shortcut buttons
Cash rounding shown: "Total $12.88 → Cash $12.90" (see tax.md)
Change displayed large
Cash drawer signal sent via printer
Receipt prints (QThread)
Cart clears
```

### Card (POSLINK TCP)
```
[CARD] → total_cents (exact, no rounding) → terminal
UI shows "Waiting for terminal... $XX.XX" (modal, cancellable)
QThread polls terminal response
APPROVED → write tx, print receipt, clear cart
DECLINED → dialog, return to cart
TIMEOUT → dialog with manual override option (admin PIN required)
```

### Split
```
[SPLIT] → "Cash portion?" numpad
Remainder = card
Cash rounded, card exact
Both logged in transaction (cash_tendered_cents + card_amount_cents)
Receipt shows both lines
```

## Hard rules

- `amount_cents` to terminal = **exact**, never rounded. Cash rounding is POS-side display only.
- On terminal connect failure: header dot → red, all `[CARD]` and `[SPLIT]` buttons disabled with tooltip "Terminal offline".
- Terminal reconnect attempt = manual only (admin terminal screen). No background retry loops.
- Every `request_payment` logged with `transaction_ref` even on failure.
