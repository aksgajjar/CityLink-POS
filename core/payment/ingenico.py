"""Ingenico Desk 5000 — POSLINK 2.0 TCP adapter.

REQUIRES PHYSICAL INGENICO TERMINAL ON LOCAL NETWORK TO TEST.
No smoke test exists for this file. The wire-level XML format below is a
reasonable POSLINK 2.0 approximation — every site-specific byte (STX/ETX
framing, LRC checksum, exact field names) MUST be verified against Ingenico's
POSLINK Integrators Guide before going live. Look for `# VERIFY:` comments.

Configuration: pulls host, port, timeout_seconds from config.payment.
Always called from a QThread (see RegisterScreen._on_card).
"""

from __future__ import annotations

import socket
import xml.etree.ElementTree as ET
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

log = get_logger("payment.ingenico")

# POSLINK framing characters (verify against Ingenico docs)
STX = b"\x02"
ETX = b"\x03"

# Response code → ours
# VERIFY: actual Ingenico response codes from their integrators guide
_APPROVAL_CODES = {"000", "00"}     # commonly "approval"


def _lrc(data: bytes) -> int:
    """Longitudinal Redundancy Check (XOR over bytes after STX, including ETX)."""
    x = 0
    for b in data:
        x ^= b
    return x & 0xFF


class IngenicoTerminal(PaymentTerminal):
    """Ingenico Desk 5000 over POSLINK TCP."""

    def __init__(self, *, host: str, port: int = 9100, timeout: int = 60):
        if not host:
            raise ValueError("IngenicoTerminal: host is required")
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._connected: bool = False

    @property
    def name(self) -> str:
        return f"Ingenico@{self.host}:{self.port}"

    # ─── Connection ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Open TCP socket to terminal. Returns True on success.

        Real POSLINK terminals usually keep a long-lived connection; reconnect
        on each transaction is also acceptable but adds latency.
        """
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._connected = True
            log.info("ingenico connected: %s:%s", self.host, self.port)
            return True
        except Exception:
            log.exception("ingenico connect failed (%s:%s)", self.host, self.port)
            self._sock = None
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            log.info("ingenico disconnected")
        self._sock = None
        self._connected = False

    # ─── Payment ─────────────────────────────────────────────────────────────

    def request_payment(self, req: PaymentRequest) -> PaymentResponse:
        if not self.is_connected():
            # Try one reconnect attempt before failing
            if not self.connect():
                return PaymentResponse.error("Terminal not connected")
        if req.amount_cents <= 0:
            return PaymentResponse.error(f"Invalid amount: {req.amount_cents}")

        try:
            payload = self._build_request_xml(req)
            framed = self._frame(payload)
            self._sock.sendall(framed)
            log.info("ingenico SALE sent ref=%s amount=%s", req.transaction_ref, req.amount_cents)

            raw = self._recv_framed()
        except socket.timeout:
            log.warning("ingenico timeout ref=%s", req.transaction_ref)
            return PaymentResponse.timeout()
        except Exception:
            log.exception("ingenico transport failure ref=%s", req.transaction_ref)
            self._connected = False  # force reconnect next call
            return PaymentResponse.error("Transport failure — see errors.log")

        try:
            return self._parse_response_xml(raw)
        except Exception:
            log.exception("ingenico response parse failed; raw=%r", raw[:200])
            return PaymentResponse.error("Bad response from terminal")

    # ─── Wire format helpers (VERIFY against POSLINK docs) ───────────────────

    def _build_request_xml(self, req: PaymentRequest) -> bytes:
        """Build POSLINK XML request payload.

        VERIFY: Ingenico's Integrators Guide for exact element names and
        whether amounts are sent in cents or dollars.
        """
        root = ET.Element("Transaction")
        ET.SubElement(root, "TransType").text = "SALE"
        ET.SubElement(root, "Amount").text = str(req.amount_cents)   # cents
        ET.SubElement(root, "RefNum").text = req.transaction_ref
        return ET.tostring(root, encoding="utf-8")

    def _frame(self, payload: bytes) -> bytes:
        """Wrap payload in STX … ETX + LRC. VERIFY framing per POSLINK docs."""
        body = payload + ETX
        lrc = _lrc(body)
        return STX + body + bytes([lrc])

    def _recv_framed(self) -> bytes:
        """Read until ETX is encountered, then read LRC byte. Strip framing.

        Caller is responsible for setting socket timeout.
        """
        assert self._sock is not None
        self._sock.settimeout(self.timeout)
        buf = bytearray()
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("terminal closed connection")
            buf += chunk
            etx_idx = buf.find(ETX)
            if etx_idx == -1:
                continue
            # Wait for one more byte after ETX (LRC)
            if len(buf) <= etx_idx + 1:
                # Need one more byte
                more = self._sock.recv(1)
                if not more:
                    raise ConnectionError("terminal closed before LRC")
                buf += more
            # Strip leading STX if present
            start = 1 if buf[:1] == STX else 0
            payload = bytes(buf[start:etx_idx])
            return payload

    def _parse_response_xml(self, payload: bytes) -> PaymentResponse:
        """Parse POSLINK XML response.

        VERIFY: actual element names from Ingenico docs. Common shapes:
          <Response>
            <ResponseCode>000</ResponseCode>
            <ResponseText>APPROVAL</ResponseText>
            <AuthCode>123456</AuthCode>
            <PAN>************4242</PAN>
          </Response>
        """
        root = ET.fromstring(payload)

        def find_text(*names: str) -> str:
            for n in names:
                el = root.find(n)
                if el is not None and el.text is not None:
                    return el.text.strip()
            return ""

        code = find_text("ResponseCode", "ResultCode")
        text = find_text("ResponseText", "ResultText", "Message")
        auth = find_text("AuthCode", "ApprovalCode")
        pan = find_text("PAN", "AcctNum")
        last4 = pan[-4:] if pan else ""

        if code in _APPROVAL_CODES:
            return PaymentResponse(
                approved=True,
                result=RESULT_APPROVED,
                auth_code=auth,
                card_last4=last4,
            )
        # Anything else is treated as decline (timeouts surface earlier as socket.timeout)
        return PaymentResponse(
            approved=False,
            result=RESULT_DECLINED,
            error_message=text or f"Response code {code}",
            card_last4=last4,
        )
