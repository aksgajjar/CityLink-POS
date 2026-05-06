"""Sound feedback for the POS.

Generates 3 WAV files programmatically (no external deps):
  - assets/sounds/click.wav    short tick on every button press
  - assets/sounds/success.wav  two-tone for cash/card approval
  - assets/sounds/error.wav    low buzz for declined/error

Plays via QSoundEffect (PyQt6.QtMultimedia). Volume controlled by config.sound.

`ClickSoundFilter` is installed on the QApplication to fire `click` on every
QPushButton release without touching individual button code.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QUrl
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtWidgets import QPushButton

from core.logger import get_logger

log = get_logger("sound")

SOUND_DIR = Path("assets/sounds")
CLICK_PATH = SOUND_DIR / "click.wav"
SUCCESS_PATH = SOUND_DIR / "success.wav"
ERROR_PATH = SOUND_DIR / "error.wav"
CHACHING_PATH = SOUND_DIR / "chaching.wav"

# Premium MP3 transaction-completion clips at project root.
CASH_MP3_PATH = Path("Cash-Register.mp3")
CARD_MP3_PATH = Path("CardPayment.mp3")


# ─── WAV generators ──────────────────────────────────────────────────────────

def _write_wav(path: Path, samples: list[int], sample_rate: int = 22050) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)   # 16-bit
        f.setframerate(sample_rate)
        f.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def _gen_click(path: Path) -> None:
    """Short 20ms 800Hz tick with quick decay."""
    sr = 22050
    n = int(sr * 0.020)
    samples = []
    for i in range(n):
        env = 1.0 - (i / n)               # linear decay
        s = env * math.sin(2 * math.pi * 800 * i / sr)
        samples.append(int(s * 32767 * 0.6))
    _write_wav(path, samples, sr)


def _gen_success(path: Path) -> None:
    """Two-tone uplifting chime: 600Hz then 900Hz, 250ms total."""
    sr = 22050
    samples = []
    for hz, ms in [(600, 100), (900, 150)]:
        n = int(sr * ms / 1000)
        for i in range(n):
            attack = min(1.0, i / 100.0)
            decay = max(0.0, 1.0 - (i / n))
            env = attack * decay
            s = env * math.sin(2 * math.pi * hz * i / sr)
            samples.append(int(s * 32767 * 0.7))
    _write_wav(path, samples, sr)


def _gen_error(path: Path) -> None:
    """Low 200Hz square-wave buzz, 300ms."""
    sr = 22050
    n = int(sr * 0.300)
    samples = []
    for i in range(n):
        attack = min(1.0, i / 200.0)
        decay = max(0.0, 1.0 - (i / n))
        env = attack * decay
        s = env * (1.0 if math.sin(2 * math.pi * 200 * i / sr) > 0 else -1.0)
        samples.append(int(s * 32767 * 0.5))
    _write_wav(path, samples, sr)


def _gen_chaching(path: Path) -> None:
    """Soft 'cha-ching': sharp metallic ding (1500Hz) + soft register ring
    (900Hz then 600Hz). ~600ms total, moderate amplitude."""
    sr = 22050
    samples = []
    # Phase 1: bright bell tone with quick decay (the 'cha').
    n1 = int(sr * 0.090)
    for i in range(n1):
        env = max(0.0, 1.0 - (i / n1) ** 1.5)
        s = env * (
            0.6 * math.sin(2 * math.pi * 1500 * i / sr)
            + 0.4 * math.sin(2 * math.pi * 2200 * i / sr)
        )
        samples.append(int(s * 32767 * 0.45))
    # Phase 2: short gap.
    samples.extend([0] * int(sr * 0.020))
    # Phase 3: warmer second chime (the 'ching').
    for hz, ms in [(900, 140), (600, 220)]:
        n = int(sr * ms / 1000)
        for i in range(n):
            attack = min(1.0, i / 80.0)
            decay = max(0.0, 1.0 - (i / n))
            env = attack * decay
            s = env * math.sin(2 * math.pi * hz * i / sr)
            samples.append(int(s * 32767 * 0.55))
    _write_wav(path, samples, sr)


def generate_sounds_if_missing() -> None:
    """Idempotent: only writes WAV files that don't yet exist."""
    if not CLICK_PATH.exists():
        _gen_click(CLICK_PATH); log.info("generated %s", CLICK_PATH)
    if not SUCCESS_PATH.exists():
        _gen_success(SUCCESS_PATH); log.info("generated %s", SUCCESS_PATH)
    if not ERROR_PATH.exists():
        _gen_error(ERROR_PATH); log.info("generated %s", ERROR_PATH)
    if not CHACHING_PATH.exists():
        _gen_chaching(CHACHING_PATH); log.info("generated %s", CHACHING_PATH)


# ─── Sound player ────────────────────────────────────────────────────────────

class SoundPlayer:
    """Loads sound effects + plays them respecting config.sound.

    WAV effects (click/success/error/chaching) → QSoundEffect (low-latency).
    MP3 transaction-completion clips (Cash-Register / CardPayment) →
    QMediaPlayer + QAudioOutput. Both preloaded once; instances reused;
    failsafe if file missing.
    """

    def __init__(self, *, enabled: bool = True, volume_pct: int = 80):
        self.enabled = enabled
        self.volume = max(0.0, min(1.0, volume_pct / 100.0))
        self._effects: dict[str, QSoundEffect] = {}
        # MP3 players: name → (QMediaPlayer, QAudioOutput, base_volume_0_1)
        self._mp3: dict[str, tuple] = {}
        self._load_all()
        self._load_mp3_completion_sounds()

    def _load_all(self) -> None:
        for name, path in [
            ("click",    CLICK_PATH),
            ("success",  SUCCESS_PATH),
            ("error",    ERROR_PATH),
            ("chaching", CHACHING_PATH),
        ]:
            if not path.exists():
                log.warning("sound asset missing: %s", path)
                continue
            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(str(path.resolve())))
            eff.setVolume(self.volume)
            self._effects[name] = eff

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_volume_pct(self, pct: int) -> None:
        self.volume = max(0.0, min(1.0, pct / 100.0))
        for eff in self._effects.values():
            eff.setVolume(self.volume)

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        eff = self._effects.get(name)
        if eff is None:
            return
        eff.play()

    def play_click(self) -> None: self.play("click")
    def play_success(self) -> None: self.play("success")
    def play_error(self) -> None: self.play("error")
    def play_chaching(self) -> None:
        """Legacy WAV cha-ching kept as fallback; cash flow now prefers
        play_cash_sound() (MP3) when the file exists."""
        eff = self._effects.get("chaching")
        if eff is None or not self.enabled:
            return
        prev = eff.volume()
        eff.setVolume(min(1.0, self.volume * 0.85))
        eff.play()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(800, lambda: eff.setVolume(prev))

    # ─── MP3 transaction-completion clips ────────────────────────────────────

    def _load_mp3_completion_sounds(self) -> None:
        """Preload Cash-Register.mp3 and CardPayment.mp3 via QMediaPlayer.

        Failsafe: if QtMultimedia MP3 backend or files unavailable, skip
        silently — no exceptions surfaced to the cashier flow.
        """
        for name, path, base_vol in [
            ("cash", CASH_MP3_PATH, 0.70),   # ~70%
            ("card", CARD_MP3_PATH, 0.60),   # ~60%
        ]:
            try:
                if not path.exists():
                    log.warning("payment sound missing: %s", path)
                    continue
                from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
                player = QMediaPlayer()
                out = QAudioOutput()
                player.setAudioOutput(out)
                player.setSource(QUrl.fromLocalFile(str(path.resolve())))
                out.setVolume(base_vol * self.volume)
                self._mp3[name] = (player, out, base_vol)
            except Exception:
                log.exception("failed loading payment sound %s", path)

    def _play_mp3(self, name: str) -> None:
        if not self.enabled:
            return
        rec = self._mp3.get(name)
        if rec is None:
            return
        player, out, base_vol = rec
        try:
            # Refresh per-clip volume against current global volume (in case
            # config.sound was changed mid-shift).
            out.setVolume(base_vol * self.volume)
            # Restart from start; reuse the same player instance.
            player.stop()
            player.setPosition(0)
            player.play()
        except Exception:
            log.exception("mp3 play failed for %s", name)

    def play_cash_sound(self) -> None:
        """Play Cash-Register.mp3 on cash transaction completion."""
        self._play_mp3("cash")

    def play_card_sound(self) -> None:
        """Play CardPayment.mp3 on card approval completion."""
        self._play_mp3("card")

    def stop_all(self) -> None:
        """Stop any in-flight MP3 playback. Safe no-op if nothing playing."""
        for player, _out, _v in self._mp3.values():
            try:
                player.stop()
            except Exception:
                pass


# ─── App-wide click event filter ─────────────────────────────────────────────

class ClickSoundFilter(QObject):
    """Install on QApplication. Plays `click` on every QPushButton release."""

    def __init__(self, sound_player: SoundPlayer):
        super().__init__()
        self.sp = sound_player

    def eventFilter(self, obj: Optional[QObject], event: Optional[QEvent]) -> bool:
        if event is not None and event.type() == QEvent.Type.MouseButtonRelease:
            if isinstance(obj, QPushButton) and obj.isEnabled():
                self.sp.play_click()
        return False   # never consume — let normal handling continue
