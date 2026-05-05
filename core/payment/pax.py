"""PAX terminal stub. Not implemented yet — returns errors only.

REQUIRES PHYSICAL PAX TERMINAL TO TEST. Implement per PAX POSLINK Integrators
Guide when hardware is on hand. Until then, selecting `terminal_type: "pax"`
will load this stub and surface a clear "Not implemented" error rather than
silently downgrading to mock.
"""

from __future__ import annotations

from core.logger import get_logger
from core.payment.base import PaymentRequest, PaymentResponse, PaymentTerminal

log = get_logger("payment.pax")


class PaxTerminal(PaymentTerminal):
    """Placeholder PAX adapter. Not functional — implement when hardware available."""

    def __init__(self, *, host: str, port: int = 9100, timeout: int = 60):
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"PAX@{self.host}:{self.port} (stub)"

    def connect(self) -> bool:
        log.warning("PaxTerminal stub — connect() always fails")
        return False

    def is_connected(self) -> bool:
        return False

    def disconnect(self) -> None:
        return None

    def request_payment(self, req: PaymentRequest) -> PaymentResponse:
        return PaymentResponse.error("PAX adapter not implemented (no hardware)")
