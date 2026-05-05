"""Mock payment terminal for development.

Sleeps `delay_seconds` then returns a configurable response. By default returns
APPROVED with a synthetic auth code and last-4 digits. Used when
config.payment.terminal_type == "mock".

Force outcomes via constructor args (or runtime mutation) for tests:
    MockTerminal(force_decline=True)   # → declined
    MockTerminal(force_timeout=True)   # → timeout (still sleeps delay_seconds)
    MockTerminal(force_error="oops")   # → error result
"""

from __future__ import annotations

import time
from typing import Optional

from core.logger import get_logger
from core.payment.base import (
    PaymentRequest,
    PaymentResponse,
    PaymentTerminal,
    RESULT_APPROVED,
    RESULT_DECLINED,
    RESULT_ERROR,
    RESULT_TIMEOUT,
)

log = get_logger("payment.mock")


class MockTerminal(PaymentTerminal):
    """Fake terminal. Useful for dev and the cash-only fallback path."""

    def __init__(
        self,
        *,
        delay_seconds: float = 2.0,
        force_decline: bool = False,
        force_timeout: bool = False,
        force_error: Optional[str] = None,
        approval_auth_code: str = "MOCK123",
        approval_card_last4: str = "4242",
    ) -> None:
        self.delay_seconds = delay_seconds
        self.force_decline = force_decline
        self.force_timeout = force_timeout
        self.force_error = force_error
        self.approval_auth_code = approval_auth_code
        self.approval_card_last4 = approval_card_last4
        self._connected = False

    @property
    def name(self) -> str:
        return "MockTerminal"

    # ─── Connection lifecycle ────────────────────────────────────────────────

    def connect(self) -> bool:
        log.info("mock terminal connect()")
        self._connected = True
        return True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        if self._connected:
            log.info("mock terminal disconnect()")
        self._connected = False

    # ─── Payment ─────────────────────────────────────────────────────────────

    def request_payment(self, req: PaymentRequest) -> PaymentResponse:
        if not self._connected:
            return PaymentResponse.error("Terminal not connected")
        if req.amount_cents <= 0:
            return PaymentResponse.error(f"Invalid amount: {req.amount_cents}")

        log.info(
            "mock terminal request_payment ref=%s amount=%s (sleep %.1fs)",
            req.transaction_ref, req.amount_cents, self.delay_seconds,
        )
        time.sleep(self.delay_seconds)

        if self.force_error:
            return PaymentResponse.error(self.force_error)
        if self.force_timeout:
            return PaymentResponse.timeout()
        if self.force_decline:
            return PaymentResponse.declined("Mock decline")

        return PaymentResponse(
            approved=True,
            result=RESULT_APPROVED,
            auth_code=self.approval_auth_code,
            card_last4=self.approval_card_last4,
        )
