"""Payment terminal contract: dataclasses + abstract base.

All concrete adapters (mock, ingenico, pax) implement `PaymentTerminal`.
Money is in integer cents — never round before sending to the terminal
(cash rounding is POS-side display only).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# ─── Result codes ────────────────────────────────────────────────────────────

# Stable string codes so callers and reports can match without depending on
# vendor-specific text in error_message.
RESULT_APPROVED = "approved"
RESULT_DECLINED = "declined"
RESULT_TIMEOUT = "timeout"
RESULT_CANCELLED = "cancelled"
RESULT_ERROR = "error"


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PaymentRequest:
    """Sent to the terminal. amount_cents is exact (no cash rounding)."""
    amount_cents: int
    transaction_ref: str


@dataclass(slots=True)
class PaymentResponse:
    """Returned by the terminal. `approved` is the only field callers must check."""
    approved: bool
    result: str = RESULT_APPROVED
    auth_code: str = ""
    card_last4: str = ""
    error_message: str = ""

    @classmethod
    def declined(cls, reason: str = "Card declined") -> "PaymentResponse":
        return cls(approved=False, result=RESULT_DECLINED, error_message=reason)

    @classmethod
    def timeout(cls) -> "PaymentResponse":
        return cls(approved=False, result=RESULT_TIMEOUT, error_message="Terminal timed out")

    @classmethod
    def cancelled(cls) -> "PaymentResponse":
        return cls(approved=False, result=RESULT_CANCELLED, error_message="Cancelled by user")

    @classmethod
    def error(cls, reason: str) -> "PaymentResponse":
        return cls(approved=False, result=RESULT_ERROR, error_message=reason)


# ─── Abstract terminal ───────────────────────────────────────────────────────

class PaymentTerminal(ABC):
    """Abstract payment terminal. Subclass for each vendor (Ingenico, PAX, mock)."""

    @abstractmethod
    def connect(self) -> bool:
        """Open the link to the terminal. Returns True on success."""

    @abstractmethod
    def is_connected(self) -> bool:
        """True if the terminal is currently reachable."""

    @abstractmethod
    def request_payment(self, req: PaymentRequest) -> PaymentResponse:
        """Block until the terminal responds (or timeout). Always called from a QThread."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the link. Idempotent."""

    @property
    def name(self) -> str:
        """Human-readable adapter name. Subclasses may override."""
        return type(self).__name__
