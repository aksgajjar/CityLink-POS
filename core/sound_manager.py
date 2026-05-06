"""Centralized payment-completion sound manager.

Thin convenience wrapper around `core.sound.SoundPlayer` that exposes the
two production transaction-completion clips:

  - Cash-Register.mp3  → cash sale finalized
  - CardPayment.mp3    → card approval finalized

Usage:
    from core import sound_manager as sm
    sm.bind(app_sound_player)        # once at startup
    sm.preload_sounds()               # idempotent; SoundPlayer already preloaded
    sm.play_cash_sound()
    sm.play_card_sound()

Failsafe: if no SoundPlayer is bound (e.g. headless tests) or files are
missing, calls become silent no-ops.
"""

from __future__ import annotations

from typing import Optional

from core.logger import get_logger
from core.sound import SoundPlayer

log = get_logger("sound_manager")

_player: Optional[SoundPlayer] = None


def bind(player: SoundPlayer) -> None:
    """Bind the application's SoundPlayer instance."""
    global _player
    _player = player


def preload_sounds() -> None:
    """No-op (SoundPlayer constructor preloads). Kept for API symmetry."""
    return None


def play_cash_sound() -> None:
    if _player is None:
        return
    try:
        _player.play_cash_sound()
    except Exception:
        log.exception("play_cash_sound failed")


def play_card_sound() -> None:
    if _player is None:
        return
    try:
        _player.play_card_sound()
    except Exception:
        log.exception("play_card_sound failed")


def stop_all() -> None:
    if _player is None:
        return
    try:
        _player.stop_all()
    except Exception:
        log.exception("stop_all failed")
