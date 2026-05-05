"""Build a PaymentTerminal from config and connect.

Reads `config.payment.terminal_type` and instantiates the matching adapter.
Always returns a terminal instance — even if `connect()` fails — so the UI can
inspect `is_connected()` and react. Unknown terminal types fall back to
MockTerminal with a logged warning.
"""

from __future__ import annotations

from typing import Optional

from core.logger import get_logger
from core.payment.base import PaymentTerminal

log = get_logger("payment.detector")


def get_terminal(config: dict) -> PaymentTerminal:
    """Construct + connect terminal per config. Returns the instance regardless of connect outcome."""
    payment_cfg = config.get("payment", {}) or {}
    ttype = (payment_cfg.get("terminal_type") or "mock").lower()
    host = payment_cfg.get("host") or "127.0.0.1"
    port = int(payment_cfg.get("port") or 9100)
    timeout = int(payment_cfg.get("timeout_seconds") or 60)

    terminal: PaymentTerminal

    if ttype == "mock":
        from core.payment.mock import MockTerminal
        terminal = MockTerminal()
    elif ttype == "ingenico":
        from core.payment.ingenico import IngenicoTerminal
        terminal = IngenicoTerminal(host=host, port=port, timeout=timeout)
    elif ttype == "pax":
        from core.payment.pax import PaxTerminal
        terminal = PaxTerminal(host=host, port=port, timeout=timeout)
    else:
        log.warning("unknown terminal_type %r — falling back to MockTerminal", ttype)
        from core.payment.mock import MockTerminal
        terminal = MockTerminal()

    try:
        ok = terminal.connect()
        if ok:
            log.info("payment terminal connected: %s", terminal.name)
        else:
            log.warning("payment terminal failed to connect: %s — POS will run in cash-only mode", terminal.name)
    except Exception:
        log.exception("payment terminal connect raised; falling back to disconnected state")

    return terminal


def is_mock(terminal: Optional[PaymentTerminal]) -> bool:
    """True if terminal is a MockTerminal instance (UI uses this for the orange MOCK header dot)."""
    if terminal is None:
        return False
    return type(terminal).__name__ == "MockTerminal"
