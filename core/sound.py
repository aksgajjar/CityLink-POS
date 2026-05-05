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


def generate_sounds_if_missing() -> None:
    """Idempotent: only writes WAV files that don't yet exist."""
    if not CLICK_PATH.exists():
        _gen_click(CLICK_PATH); log.info("generated %s", CLICK_PATH)
    if not SUCCESS_PATH.exists():
        _gen_success(SUCCESS_PATH); log.info("generated %s", SUCCESS_PATH)
    if not ERROR_PATH.exists():
        _gen_error(ERROR_PATH); log.info("generated %s", ERROR_PATH)


# ─── Sound player ────────────────────────────────────────────────────────────

class SoundPlayer:
    """Loads the 3 sound effects + plays them respecting config.sound."""

    def __init__(self, *, enabled: bool = True, volume_pct: int = 80):
        self.enabled = enabled
        self.volume = max(0.0, min(1.0, volume_pct / 100.0))
        self._effects: dict[str, QSoundEffect] = {}
        self._load_all()

    def _load_all(self) -> None:
        for name, path in [
            ("click",   CLICK_PATH),
            ("success", SUCCESS_PATH),
            ("error",   ERROR_PATH),
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
