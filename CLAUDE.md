# CityLink POS — Master Index

Production POS for CityLink convenience stores (BC, Canada). Touch-first cashier UI + separate admin panel. Offline-first SQLite. Semi-integrated payment via POSLINK. Built once, no monthly fees.

## Stack

- Python 3.11 + PyQt6
- SQLite (`sqlite3`) — local, offline-first
- python-escpos (thermal receipts) | ReportLab (PDF reports)
- PyInstaller → Windows `.exe` (prod) / macOS `.app` (dev/test)
- Logging: stdlib `logging` → `errors.log` (rotating, 10MB)

## Project structure

```
core/    db, models, tax, cart, deals, receipt, reports, auth, logger, payment/
ui/      main_window, login, styles, cashier/, admin/
data/    store.db (auto-created)
exports/ PDFs + CSV labels
assets/  logo + icons
config.json  per-store config
main.py  entry → login → role routing
```

## Behavior rules (always)

- **Token efficiency**: read only the file you are editing. Never re-read files in context. Surgical edits — never rewrite working files. One task at a time.
- **Accuracy**: not 100% sure → STOP and ask one specific question. Never guess at: tax rules, payment protocol, DB schema, file paths, APIs, versions, package names.
- **Change mgmt**: state WHAT + WHERE (file:line). File-A change forces File-B → flag and ask first.
- **Code quality**: money = integers in cents always. No raw SQL outside `core/db.py`. No hardcoded values — read `config.json` or DB. One-line docstrings minimum. Every DB write = `try/except` + explicit rollback. Payment + print = `QThread`. No `print()` for errors — use logger. No bare `except`. No emojis or em-dashes in code.
- **Output**: thorough reasoning, concise output. No sycophantic openers/closers.
- **When stuck**: don't hallucinate, don't write placeholders. Stop, describe the exact problem, ask for direction.

## Domain rules — load on demand

| File | When to read |
|---|---|
| `.claude/db.md` | Touching schema, queries, transactions, `core/db.py`, migrations |
| `.claude/ui.md` | Building any `ui/`, styles, register layout, departments, login, scanner |
| `.claude/payment.md` | Payment terminal, `core/payment/`, cash/card/split flows |
| `.claude/tax.md` | Tax engine, cash rounding, deposits, bag charge |
| `.claude/features.md` | Deals, lottery, voids, shifts, reports, inventory, cash mgmt, build order, packaging, `config.json` |

## Build order

Strict ordered checklist in `.claude/features.md` → "Phase 1 build order". Do not skip or reorder. 4 checkpoints. Phase 2 + out-of-scope also listed there.
