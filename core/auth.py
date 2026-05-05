"""PIN authentication, role check, and lockout enforcement.

Single till per store → lockout state is module-global, in-memory.
Power-cycle clears lockout (employee just waits or restarts).
"""

from __future__ import annotations

import time
from typing import Optional

from core import db
from core.logger import get_logger
from core.models import Role, User

log = get_logger("auth")

MAX_ATTEMPTS: int = 3
LOCKOUT_SECONDS: int = 300   # 5 minutes per blueprint

_failed_attempts: int = 0
_locked_until: float = 0.0   # epoch seconds; 0 = not locked


# ─── Lockout state ───────────────────────────────────────────────────────────

def is_locked() -> bool:
    """Return True if PIN entry is currently locked out."""
    return time.time() < _locked_until


def seconds_until_unlock() -> int:
    """Seconds remaining on current lockout (0 if not locked)."""
    remaining = _locked_until - time.time()
    return max(0, int(remaining + 0.999))   # ceil


def reset_lockout(by_admin: str = "system") -> None:
    """Clear lockout + attempt counter. For admin override or successful login."""
    global _failed_attempts, _locked_until
    was_locked = is_locked()
    _failed_attempts = 0
    _locked_until = 0.0
    if was_locked:
        log.info("lockout reset by %s", by_admin)
        db.log_admin_action("lockout_reset", by_admin)


def _trigger_lockout(attempted_pin_len: int) -> None:
    """Engage lockout after MAX_ATTEMPTS failures. Logs to admin_log."""
    global _locked_until
    _locked_until = time.time() + LOCKOUT_SECONDS
    log.error(
        "PIN lockout engaged: %d failed attempts, locked for %ds",
        _failed_attempts, LOCKOUT_SECONDS,
    )
    db.log_admin_action(
        "pin_lockout",
        admin_name="system",
        detail=f"{_failed_attempts} failed attempts (last pin len={attempted_pin_len})",
    )


# ─── PIN verification ────────────────────────────────────────────────────────

def verify_pin(pin: str) -> Optional[User]:
    """Look up user by PIN. Returns User on success, None on failure.

    Tracks failed attempts. After MAX_ATTEMPTS consecutive failures, engages
    lockout for LOCKOUT_SECONDS. While locked, returns None without checking PIN.
    Successful login resets the counter.
    """
    global _failed_attempts, _locked_until

    if is_locked():
        log.warning("PIN attempt while locked (%ds remaining)", seconds_until_unlock())
        return None

    if not pin:
        return None

    row = db.get_user_by_pin(pin)
    if row is None:
        _failed_attempts += 1
        log.warning("PIN failure %d/%d", _failed_attempts, MAX_ATTEMPTS)
        if _failed_attempts >= MAX_ATTEMPTS:
            _trigger_lockout(attempted_pin_len=len(pin))
        return None

    user = User.from_row(row)
    if _failed_attempts > 0 or _locked_until > 0:
        log.info("login OK for %s after %d prior failures", user.name, _failed_attempts)
    _failed_attempts = 0
    _locked_until = 0.0
    return user


# ─── Role check ──────────────────────────────────────────────────────────────

def has_role(user: Optional[User], role: Role) -> bool:
    """True if user is active and has the given role."""
    return user is not None and user.is_active and user.role == role


def require_admin(user: Optional[User]) -> bool:
    """True if user is an active admin. Use as gate for void/override/admin panel."""
    return has_role(user, "admin")


def require_cashier(user: Optional[User]) -> bool:
    """True if user is an active cashier or admin (admin implicitly allowed)."""
    return user is not None and user.is_active and user.role in ("cashier", "admin")


# ─── Test hook ───────────────────────────────────────────────────────────────

def _reset_state_for_tests() -> None:
    """Test-only: hard reset module state. Do not call from app code."""
    global _failed_attempts, _locked_until
    _failed_attempts = 0
    _locked_until = 0.0
