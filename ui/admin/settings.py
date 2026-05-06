"""Admin → Settings: terminal, sound, system. Persists to config.json."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.settings")

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
DB_PATH = Path(__file__).resolve().parents[2] / "data" / "store.db"


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.exception("config.json read failed; using empty")
        return {}


def _save_cfg(cfg: dict) -> bool:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception:
        log.exception("config.json write failed")
        return False


class SettingsScreen(QWidget):
    """Minimal settings panel — terminal / sound / system."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("settings_screen")
        self._cfg = _load_cfg()
        self._build()

    # ─── build ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(
            "QLineEdit { padding: 8px 10px; border: 1px solid #B0BEC5;"
            " border-radius: 6px; background: white; min-height: 22px; }"
            "QLineEdit:focus { border: 2px solid "
            f"{styles.COLORS['blue_mid']}; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 20, 40, 20)
        v.setSpacing(18)

        title = QLabel("Settings")
        tf = QFont(styles.FONT_FAMILY, 20); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {styles.COLORS['navy']};")
        v.addWidget(title)

        v.addWidget(self._terminal_section())
        v.addWidget(self._sound_section())
        v.addWidget(self._system_section())
        v.addStretch(1)

        # Save bar
        save_bar = QHBoxLayout(); save_bar.setSpacing(10); save_bar.addStretch(1)
        save = QPushButton("Save Settings")
        save.setObjectName("settings_save")
        save.setMinimumSize(180, 48)
        save.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_cash']};"
            f" color: white; border: none; border-radius: 6px;"
            f" font-weight: bold; font-size: 14pt; }}"
        )
        save.clicked.connect(self._on_save)
        save_bar.addWidget(save)
        v.addLayout(save_bar)

    def _section_box(self, heading: str) -> tuple[QFrame, QVBoxLayout]:
        box = QFrame()
        box.setObjectName(f"settings_box_{heading.lower().replace(' ','_')}")
        box.setStyleSheet(
            f"QFrame#{box.objectName()} {{ background-color: white;"
            f" border: 1px solid #C8D0E0; border-radius: 8px; }}"
        )
        outer = QVBoxLayout(box)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        head = QLabel(heading)
        hf = QFont(styles.FONT_FAMILY, 13); hf.setBold(True)
        head.setFont(hf)
        head.setStyleSheet(f"color: {styles.COLORS['navy']};")
        outer.addWidget(head)
        return box, outer

    def _terminal_section(self) -> QWidget:
        box, outer = self._section_box("Terminal Settings")
        pay = self._cfg.get("payment", {})
        # Enable terminal
        self._term_enable = QCheckBox("Enable Payment Terminal")
        self._term_enable.setChecked(pay.get("terminal_type", "mock") != "disabled")
        outer.addWidget(self._term_enable)
        # IP / host
        ip_row = QHBoxLayout(); ip_row.setSpacing(10)
        ip_row.addWidget(QLabel("Terminal IP:"))
        self._term_ip = QLineEdit(pay.get("host", "192.168.1.100"))
        self._term_ip.setObjectName("settings_term_ip")
        ip_row.addWidget(self._term_ip, stretch=1)
        outer.addLayout(ip_row)
        # Test connection
        test_row = QHBoxLayout(); test_row.addStretch(1)
        test = QPushButton("Test Connection")
        test.setObjectName("settings_term_test")
        test.setMinimumHeight(40)
        test.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_hold']};"
            f" color: white; border: none; border-radius: 6px;"
            f" padding: 6px 16px; font-weight: bold; }}"
        )
        test.clicked.connect(self._on_test_terminal)
        test_row.addWidget(test)
        outer.addLayout(test_row)
        return box

    def _sound_section(self) -> QWidget:
        box, outer = self._section_box("Sound Settings")
        snd = self._cfg.get("sound", {})
        self._snd_enable = QCheckBox("Enable Sound")
        self._snd_enable.setChecked(bool(snd.get("enabled", True)))
        outer.addWidget(self._snd_enable)
        vol_row = QHBoxLayout(); vol_row.setSpacing(10)
        vol_row.addWidget(QLabel("Volume:"))
        self._snd_slider = QSlider(Qt.Orientation.Horizontal)
        self._snd_slider.setObjectName("settings_snd_slider")
        self._snd_slider.setRange(0, 100)
        self._snd_slider.setValue(int(snd.get("volume", 80)))
        self._snd_value = QLabel(f"{self._snd_slider.value()}")
        self._snd_value.setMinimumWidth(40)
        self._snd_slider.valueChanged.connect(
            lambda val: self._snd_value.setText(str(val))
        )
        vol_row.addWidget(self._snd_slider, stretch=1)
        vol_row.addWidget(self._snd_value)
        outer.addLayout(vol_row)
        return box

    def _system_section(self) -> QWidget:
        box, outer = self._section_box("System")
        info = QLabel(f"Database: {DB_PATH}")
        info.setStyleSheet(f"color: {styles.COLORS['text_muted']};")
        outer.addWidget(info)
        backup_row = QHBoxLayout(); backup_row.addStretch(1)
        backup = QPushButton("Export Backup")
        backup.setObjectName("settings_export_backup")
        backup.setMinimumHeight(40)
        backup.setStyleSheet(
            f"QPushButton {{ background-color: {styles.COLORS['btn_split']};"
            f" color: white; border: none; border-radius: 6px;"
            f" padding: 6px 16px; font-weight: bold; }}"
        )
        backup.clicked.connect(self._on_export_backup)
        backup_row.addWidget(backup)
        outer.addLayout(backup_row)
        return box

    # ─── handlers ────────────────────────────────────────────────────────────

    def _on_test_terminal(self) -> None:
        ip = self._term_ip.text().strip()
        if not ip:
            QMessageBox.warning(self, "Test Connection", "Enter an IP first.")
            return
        # Mock OK response (real ping would need socket / threading).
        QMessageBox.information(
            self, "Test Connection",
            f"Mock OK\n\nTerminal at {ip} responded.\n"
            "(Real ping not implemented; this is a placeholder.)",
        )

    def _on_export_backup(self) -> None:
        if not DB_PATH.exists():
            QMessageBox.warning(self, "Export Backup",
                                f"Database not found at:\n{DB_PATH}")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested = f"store_backup_{ts}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DB Backup", suggested, "SQLite DB (*.db)"
        )
        if not path:
            return
        try:
            shutil.copy2(DB_PATH, path)
            QMessageBox.information(self, "Export Backup",
                                    f"Backup saved:\n{path}")
        except Exception:
            log.exception("backup export failed")
            QMessageBox.critical(self, "Export Backup",
                                 "Failed to save backup. See errors.log.")

    def _on_save(self) -> None:
        # Re-read file fresh, patch keys, write back (preserves unrelated keys).
        cfg = _load_cfg() or self._cfg
        pay = cfg.setdefault("payment", {})
        pay["terminal_type"] = "mock" if self._term_enable.isChecked() else "disabled"
        pay["host"] = self._term_ip.text().strip() or pay.get("host", "")
        snd = cfg.setdefault("sound", {})
        snd["enabled"] = bool(self._snd_enable.isChecked())
        snd["volume"] = int(self._snd_slider.value())
        if _save_cfg(cfg):
            self._cfg = cfg
            QMessageBox.information(
                self, "Settings",
                "Saved.\n\nRestart the app for terminal/tax changes to take effect.",
            )
        else:
            QMessageBox.critical(self, "Settings",
                                 "Failed to save config.json. See errors.log.")
