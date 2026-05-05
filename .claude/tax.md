# BC Tax Rules — CityLink POS

All in `core/tax.py`. All values in cents. **Card payment = no rounding. Cash = round to nearest 5¢.**

## Rates (from `config.json`)

| Tax | Rate | Source |
|---|---|---|
| GST | 5% | `config.tax.gst_rate` |
| PST | 7% | `config.tax.pst_rate` |
| Bottle deposit 355ml | 10¢ | `config.tax.bottle_deposit_355ml` |
| Bottle deposit 1L | 25¢ | `config.tax.bottle_deposit_1L` |
| Bag charge | 25¢ | `config.tax.bag_charge_cents` |

## Reference impl

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

## Rules

- Tax calculated on price **after** deal discount applied (see features.md).
- GST applies to taxable items by department default; PST is per-department.
- Medicine, gift cards, lottery → GST=0, PST=0.
- Bottle deposit = flat per-unit, not % — applies on top of subtotal, before tax-of-tax (BC: deposit is not GST-able for our SKUs).
- Cash rounding applied **only to grand total**, never per line.
- For split tender: cash portion rounded, card portion exact, sum stored in `rounded_total_cents`.
- Bag charge taxed (GST + PST) — added as a cart line, not a separate field.
