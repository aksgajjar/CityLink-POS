"""Admin → Tools: live hardware/system diagnostics for the POS station.

Real tests only — never fake a status. Hardware that is unavailable shows a
graceful "Not Configured" pill, never crashes the app.

Threaded: network/HTTP/printer probes run on QThread so the UI stays
responsive on touchscreens.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui import styles

log = get_logger("ui.admin.tools")

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "store.db"


# ─── Status pill ─────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "ok":      "#27AE60",    # green
    "warn":    "#F1C40F",    # yellow
    "fail":    "#E74C3C",    # red
    "info":    "#7F8C8D",    # grey
    "checking":"#95A5A6",    # grey-blue
    "blue":    "#2196F3",    # action
}


def _status_label(text: str, kind: str = "info") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("tools_status_pill")
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setMinimumHeight(42)
    lab.setMinimumWidth(180)
    lf = QFont(styles.FONT_FAMILY, 12); lf.setBold(True)
    lab.setFont(lf)
    lab.setStyleSheet(
        f"background-color: {STATUS_COLORS.get(kind, '#95A5A6')}; color: white;"
        f" border: none; border-radius: 6px; font-weight: bold; padding: 6px 14px;"
    )
    return lab


def _action_btn(text: str, kind: str = "blue") -> QPushButton:
    b = QPushButton(text)
    b.setMinimumHeight(42)
    b.setMinimumWidth(180)
    bf = QFont(styles.FONT_FAMILY, 12); bf.setBold(True)
    b.setFont(bf)
    color = STATUS_COLORS.get(kind, "#2196F3")
    b.setStyleSheet(
        f"QPushButton {{ background-color: {color}; color: white;"
        f" border: none; border-radius: 6px; padding: 8px 18px;"
        f" font-weight: bold; }}"
        f"QPushButton:hover {{ background-color: {color}DD; }}"
    )
    return b


# ─── Workers (run on QThread) ────────────────────────────────────────────────

class _NetworkWorker(QObject):
    finished = pyqtSignal(dict)   # {ok, latency_ms, local_ip, error}

    def run(self) -> None:
        result = {"ok": False, "latency_ms": 0, "local_ip": "", "error": ""}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); result["local_ip"] = s.getsockname()[0]
            s.close()
        except Exception as e:
            result["error"] = f"local IP: {e}"
        try:
            t0 = time.perf_counter()
            with socket.create_connection(("8.8.8.8", 53), timeout=2.5):
                pass
            result["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            result["ok"] = True
        except Exception as e:
            result["error"] = (result["error"] + f" net: {e}").strip()
        self.finished.emit(result)


class _HttpWorker(QObject):
    finished = pyqtSignal(dict)   # {ok, code, latency_ms, error}

    URL = "https://www.google.com/generate_204"

    def run(self) -> None:
        out = {"ok": False, "code": 0, "latency_ms": 0, "error": ""}
        try:
            req = urllib.request.Request(self.URL, method="GET")
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                out["code"] = resp.status
                out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                out["ok"] = 200 <= resp.status < 400
        except Exception as e:
            out["error"] = str(e)
        self.finished.emit(out)


class _PrinterProbeWorker(QObject):
    finished = pyqtSignal(dict)   # {ok, msg}

    def run(self) -> None:
        out = {"ok": False, "msg": ""}
        try:
            from escpos.printer import Usb  # type: ignore
        except Exception as e:
            out["msg"] = f"python-escpos not installed: {e}"
            self.finished.emit(out); return
        try:
            p = Usb(0x04b8, 0x0202)
            try: p.close()
            except Exception: pass
            out["ok"] = True
            out["msg"] = "USB thermal printer detected"
        except Exception as e:
            out["msg"] = f"no thermal printer: {e}"
        self.finished.emit(out)


# ─── Touchscreen full-screen test ────────────────────────────────────────────

class TouchTestDialog(QDialog):
    """Full-screen click/touch echo. Esc to exit."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Touch Test")
        self.setModal(True)
        self.setStyleSheet("background-color: #0B1E3F;")
        self._points: list[tuple[int, int, float]] = []
        # Auto-decay points after 1.5s.
        self._timer = QTimer(self); self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.showFullScreen()

    def mousePressEvent(self, e):
        self._points.append((e.position().x(), e.position().y(), time.time()))
        self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._timer.stop(); self.accept()

    def _tick(self) -> None:
        now = time.time()
        self._points = [(x, y, t) for (x, y, t) in self._points if now - t < 1.5]
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Hint text
        p.setPen(QColor("#FFFFFF"))
        f = QFont(styles.FONT_FAMILY, 18); f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                   "Touch anywhere — Esc to exit")
        # Touch ripples
        now = time.time()
        for (x, y, t) in self._points:
            age = min(1.5, now - t) / 1.5
            radius = int(20 + 60 * age)
            alpha = max(0, 255 - int(255 * age))
            pen = QPen(QColor(39, 174, 96, alpha), 4)
            p.setPen(pen)
            p.drawEllipse(int(x - radius), int(y - radius), radius * 2, radius * 2)


# ─── Tools screen ────────────────────────────────────────────────────────────

class ToolsScreen(QWidget):
    """Live diagnostics dashboard (admin)."""

    back_requested = pyqtSignal()

    def __init__(self, *, admin_name: str = "admin",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("tools_screen")
        self._admin_name = admin_name
        self._threads: list[QThread] = []
        self._build()
        # Fire initial probes after layout settles.
        QTimer.singleShot(50, self._probe_network)
        QTimer.singleShot(60, self._probe_http)
        QTimer.singleShot(70, self._probe_printer)
        QTimer.singleShot(80, self._probe_system)

    # ── construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(
            "QWidget#tools_screen { background-color: #F4F6F8; }"
            "QLabel#tools_row_name { color: #1B3A6B; font-weight: bold; font-size: 12pt; }"
            "QFrame#tools_row { background: white; border: 1px solid #E1E4EA;"
            " border-radius: 8px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16); outer.setSpacing(10)

        # Header
        head = QHBoxLayout()
        title = QLabel("Tools")
        tf = QFont(styles.FONT_FAMILY, 22); tf.setBold(True)
        title.setFont(tf); title.setStyleSheet("color: #1B3A6B;")
        head.addWidget(title); head.addStretch(1)
        # Live ONLINE/OFFLINE indicator. Updates only on open + manual Network Test.
        self._live_indicator = QLabel("● CHECKING")
        lif = QFont(styles.FONT_FAMILY, 12); lif.setBold(True)
        self._live_indicator.setFont(lif)
        self._live_indicator.setStyleSheet("color: #95A5A6; padding: 0 14px;")
        head.addWidget(self._live_indicator)
        back = _action_btn("Back to Home", "blue")
        back.clicked.connect(self.back_requested.emit)
        head.addWidget(back)
        outer.addLayout(head)

        # Subtitle row (terminal id + IP)
        sub = QHBoxLayout()
        self._term_label = QLabel(self._terminal_id_text())
        self._term_label.setStyleSheet("color: #1B3A6B; font-weight: bold;")
        sub.addWidget(self._term_label)
        sub.addStretch(1)
        self._ip_label = QLabel("IP: —")
        self._ip_label.setStyleSheet("color: #1B3A6B; font-weight: bold;")
        sub.addWidget(self._ip_label)
        outer.addLayout(sub)

        # Diagnostic rows. Each row has a TEST button — no auto-refresh.
        rows = QVBoxLayout(); rows.setSpacing(8)
        self._stat_network  = _status_label("Idle", "info")
        self._stat_http     = _status_label("Idle", "info")
        self._stat_printer  = _status_label("Idle", "info")
        self._stat_drawer   = _status_label("Idle", "info")
        self._stat_label    = _status_label("Not Configured", "warn")
        self._stat_scanner  = _status_label("Idle", "info")
        self._stat_card     = _status_label("Not Configured", "warn")
        self._stat_system   = _status_label("Idle", "info")
        self._stat_touch    = _status_label("Idle", "info")

        rows.addWidget(self._mk_row(
            "🌐  Network", self._stat_network, "Network Test", self._probe_network))
        rows.addWidget(self._mk_row(
            "🔗  HTTP / API", self._stat_http, "HTTP Test", self._probe_http))
        rows.addWidget(self._mk_row(
            "🧾  Receipt Printer", self._stat_printer, "Print Test", self._on_printer_test))
        rows.addWidget(self._mk_row(
            "💵  Cash Drawer", self._stat_drawer, "Open Drawer", self._on_drawer_test))
        rows.addWidget(self._mk_row(
            "🏷  Label Printer", self._stat_label, "Print Label", self._on_label_test))
        rows.addWidget(self._mk_scanner_row())
        rows.addWidget(self._mk_row(
            "💳  Card Machine", self._stat_card, "Test Connection", self._on_card_test))
        rows.addWidget(self._mk_row(
            "🩺  System Health", self._stat_system, "Run Test", self._probe_system))
        rows.addWidget(self._mk_row(
            "🖐  Touch Screen", self._stat_touch, "Touch Test", self._on_touch_test))
        outer.addLayout(rows)
        outer.addStretch(1)

        # Bottom danger row
        danger = QHBoxLayout(); danger.setSpacing(10); danger.addStretch(1)
        b_restart = _action_btn("Restart App", "warn"); b_restart.clicked.connect(self._on_restart_app)
        danger.addWidget(b_restart)
        b_reboot = _action_btn("Reboot System", "fail"); b_reboot.clicked.connect(self._on_reboot)
        danger.addWidget(b_reboot)
        outer.addLayout(danger)

    def _mk_row(self, name: str, status: QLabel, action_text: str, slot) -> QWidget:
        f = QFrame(); f.setObjectName("tools_row")
        h = QHBoxLayout(f); h.setContentsMargins(16, 8, 16, 8); h.setSpacing(12)
        lab = QLabel(name); lab.setObjectName("tools_row_name")
        lab.setMinimumWidth(160); h.addWidget(lab)
        h.addStretch(1)
        h.addWidget(status)
        h.addStretch(1)
        btn = _action_btn(action_text, "blue")
        btn.clicked.connect(slot)
        h.addWidget(btn)
        return f

    def _mk_scanner_row(self) -> QWidget:
        f = QFrame(); f.setObjectName("tools_row")
        h = QHBoxLayout(f); h.setContentsMargins(16, 8, 16, 8); h.setSpacing(12)
        lab = QLabel("📷  Barcode Scanner"); lab.setObjectName("tools_row_name")
        lab.setMinimumWidth(180); h.addWidget(lab)
        h.addStretch(1)
        h.addWidget(self._stat_scanner)
        h.addStretch(1)
        # Hidden scan input (keyboard never pops; scanner injects keystrokes).
        self._scan_input = QLineEdit()
        self._scan_input.setProperty("touchKeyboard", "off")
        self._scan_input.setMaximumWidth(0)
        self._scan_input.setStyleSheet("QLineEdit { border: none; background: transparent; }")
        self._scan_input.returnPressed.connect(self._on_scanner_input)
        h.addWidget(self._scan_input)
        # Test button → arms scanner mode for 8 seconds.
        b = _action_btn("Scanner Test", "blue")
        b.clicked.connect(self._arm_scanner_test)
        h.addWidget(b)
        # Scanner timeout timer.
        self._scan_timer = QTimer(self); self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self._on_scanner_timeout)
        return f

    def _arm_scanner_test(self) -> None:
        self._set_status(self._stat_scanner, "Scan a barcode now…", "blue")
        self._scan_input.clear()
        self._scan_input.setFocus()
        self._scan_timer.start(8000)

    def _on_scanner_timeout(self) -> None:
        if self._scan_input.text().strip():
            return  # already handled
        self._set_status(self._stat_scanner,
                         "FAILED — no scanner input received", "fail")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _terminal_id_text(self) -> str:
        try:
            host = socket.gethostname()
        except Exception:
            host = "unknown"
        return f"Terminal ID: {host}"

    def _set_status(self, label: QLabel, text: str, kind: str) -> None:
        label.setText(text)
        label.setStyleSheet(
            f"background-color: {STATUS_COLORS.get(kind, '#95A5A6')}; color: white;"
            f" border: none; border-radius: 4px; font-weight: bold; padding: 4px 12px;"
        )

    def _spawn(self, worker_cls, on_finish) -> None:
        thread = QThread(self); worker = worker_cls()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_finish)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        thread.start()

    # ── probes ───────────────────────────────────────────────────────────────

    def _probe_network(self) -> None:
        self._set_status(self._stat_network, "Checking…", "checking")
        self._spawn(_NetworkWorker, self._on_network_done)

    def _on_network_done(self, out: dict) -> None:
        if out.get("ok"):
            self._set_status(self._stat_network,
                             f"Online ({out['latency_ms']} ms)", "ok")
            self._live_indicator.setText("● ONLINE")
            self._live_indicator.setStyleSheet(
                "color: #27AE60; padding: 0 14px;")
        else:
            self._set_status(self._stat_network,
                             f"Offline — {out.get('error') or 'no link'}", "fail")
            self._live_indicator.setText("● OFFLINE")
            self._live_indicator.setStyleSheet(
                "color: #E74C3C; padding: 0 14px;")
        if out.get("local_ip"):
            self._ip_label.setText(f"IP: {out['local_ip']}")

    def _probe_http(self) -> None:
        self._set_status(self._stat_http, "Checking…", "checking")
        self._spawn(_HttpWorker, self._on_http_done)

    def _on_http_done(self, out: dict) -> None:
        if out.get("ok"):
            self._set_status(self._stat_http,
                             f"OK ({out['code']}, {out['latency_ms']} ms)", "ok")
        else:
            err = out.get("error") or f"code {out.get('code', 0)}"
            self._set_status(self._stat_http, f"Failed: {err[:40]}", "fail")

    def _probe_printer(self) -> None:
        self._set_status(self._stat_printer, "Probing…", "checking")
        self._spawn(_PrinterProbeWorker, self._on_printer_done)

    def _on_printer_done(self, out: dict) -> None:
        if out.get("ok"):
            self._set_status(self._stat_printer, "Online", "ok")
        else:
            self._set_status(self._stat_printer, "Not Detected", "warn")

    def _on_printer_test(self) -> None:
        """Print a real sample receipt: logo/date/sample items + 'Printer OK'."""
        from core import receipt as _r
        from core.models import Transaction, CartItem
        sample_items = [
            CartItem(name="Sample Snack",  unit_price_cents=199, quantity=1,
                     department="snacks", tax_gst=True, tax_pst=True,
                     gst_cents=10, pst_cents=14, line_total_cents=223),
            CartItem(name="Test Soda",     unit_price_cents=200, quantity=2,
                     department="carbonated", tax_gst=True, tax_pst=True,
                     gst_cents=20, pst_cents=28, line_total_cents=448),
        ]
        txn = Transaction(
            transaction_ref=f"TEST-{int(time.time())}",
            subtotal_cents=599, discount_cents=0, gst_cents=30, pst_cents=42,
            deposit_cents=0, bag_charge_cents=0,
            total_cents=671, rounded_total_cents=670,
            payment_method="preview",
            cash_tendered_cents=0, change_cents=0,
            cashier_id=0, cashier_name=self._admin_name, shift_id=None,
            items=sample_items,
        )
        try:
            _r.print_receipt(txn, store_name="CityLink Convenience",
                             cashier_name=self._admin_name)
            self._set_status(self._stat_printer, "PASS — Printer OK", "ok")
        except Exception:
            log.exception("printer test failed")
            self._set_status(self._stat_printer, "FAILED — see errors.log", "fail")

    def _on_drawer_test(self) -> None:
        """Send drawer-kick to thermal printer's drawer port. Real test only."""
        try:
            from escpos.printer import Usb  # type: ignore
        except Exception:
            self._set_status(self._stat_drawer,
                             "FAILED — escpos not installed", "fail")
            return
        try:
            p = Usb(0x04b8, 0x0202)
        except Exception as e:
            self._set_status(self._stat_drawer,
                             f"FAILED — printer not found: {str(e)[:40]}", "fail")
            return
        try:
            p.cashdraw(2)
            self._set_status(self._stat_drawer, "PASS — drawer kicked", "ok")
        except Exception as e:
            self._set_status(self._stat_drawer,
                             f"FAILED — kick failed: {str(e)[:40]}", "fail")
        finally:
            try: p.close()
            except Exception: pass

    def _on_label_test(self) -> None:
        # No label printer driver implemented yet — graceful warning only.
        QMessageBox.information(
            self, "Label Printer",
            "No label printer driver configured for this terminal.\n\n"
            "Connect a Zebra/ZPL printer via USB and configure in Settings.",
        )
        self._set_status(self._stat_label, "Not Configured", "warn")

    def _on_card_test(self) -> None:
        # Detect via existing payment terminal config (config.json → payment.host).
        try:
            import json
            cfg_path = Path(__file__).resolve().parents[2] / "config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            host = cfg.get("payment", {}).get("host", "")
            terminal_type = cfg.get("payment", {}).get("terminal_type", "")
        except Exception:
            host = ""; terminal_type = ""
        if not host or terminal_type in ("disabled", ""):
            self._set_status(self._stat_card, "Not Configured", "warn")
            return
        # Probe TCP connect (no auth, no fake approval).
        try:
            with socket.create_connection((host, 9100), timeout=2.0):
                pass
            self._set_status(self._stat_card,
                             f"{terminal_type} @ {host}", "ok")
        except Exception as e:
            self._set_status(self._stat_card, f"Unreachable: {e}", "fail")

    def _probe_system(self) -> None:
        # CPU / RAM / disk / DB size / uptime.
        warnings: list[str] = []
        ok = True
        # Disk
        try:
            usage = shutil.disk_usage(str(Path.home()))
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            disk_pct = 100.0 - (usage.free / usage.total * 100.0)
            if free_gb < 1.0:
                warnings.append("disk<1GB"); ok = False
            disk_text = f"disk {disk_pct:.0f}% ({free_gb:.1f}/{total_gb:.0f}GB free)"
        except Exception:
            disk_text = "disk?"
        # RAM via psutil if available
        ram_text = "ram?"
        cpu_text = "cpu?"
        try:
            import psutil  # type: ignore
            vm = psutil.virtual_memory()
            ram_text = f"ram {vm.percent:.0f}%"
            cpu = psutil.cpu_percent(interval=0.1)
            cpu_text = f"cpu {cpu:.0f}%"
            if vm.percent > 90 or cpu > 95:
                warnings.append("high load"); ok = False
        except Exception:
            pass
        # DB size
        try:
            db_mb = DB_PATH.stat().st_size / (1024**2)
            db_text = f"db {db_mb:.1f}MB"
        except Exception:
            db_text = "db?"
        # Uptime (best-effort across platforms)
        try:
            import psutil  # type: ignore
            up_s = int(time.time() - psutil.boot_time())
            up_text = f"up {up_s // 3600}h"
        except Exception:
            up_text = ""
        kind = "ok" if ok and not warnings else ("warn" if not ok else "ok")
        text = " · ".join(filter(None, [cpu_text, ram_text, disk_text, db_text, up_text]))
        self._set_status(self._stat_system, text, kind)

    def _on_scanner_input(self) -> None:
        bc = self._scan_input.text().strip()
        self._scan_timer.stop()
        if bc:
            self._set_status(self._stat_scanner,
                             f"PASS — scanned {bc[:24]}", "ok")
            self._scan_input.clear()

    def _on_touch_test(self) -> None:
        self._set_status(self._stat_touch, "Running", "ok")
        dlg = TouchTestDialog(parent=self)
        dlg.exec()
        self._set_status(self._stat_touch, "Idle", "info")

    # ── Restart / Reboot ─────────────────────────────────────────────────────

    def _on_restart_app(self) -> None:
        reply = QMessageBox.question(
            self, "Restart App",
            "Restart the POS application?\nUnsaved cart data will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().quit()
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            log.exception("restart_app failed")
            QMessageBox.critical(self, "Restart App", "Restart failed. See errors.log.")

    def _on_reboot(self) -> None:
        reply = QMessageBox.warning(
            self, "Reboot System",
            "Reboot the operating system NOW?\n\n"
            "This will close all applications and restart the machine.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["shutdown", "/r", "/t", "5"])
            elif sys.platform == "darwin":
                subprocess.Popen(["sudo", "shutdown", "-r", "now"])
            else:
                subprocess.Popen(["sudo", "shutdown", "-r", "now"])
        except Exception:
            log.exception("reboot failed")
            QMessageBox.critical(self, "Reboot", "Reboot command failed. Run manually.")
