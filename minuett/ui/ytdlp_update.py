"""Help ▸ Check for yt-dlp Update. YouTube breaks yt-dlp regularly."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

PYPI = "https://pypi.org/pypi/yt-dlp/json"


def installed_version() -> str:
    from yt_dlp.version import __version__
    return __version__


def _norm(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


class _Task(QObject):
    done = Signal(object, object)  # result, error

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


def _in_thread(parent: QObject, fn, callback) -> None:
    thread = QThread(parent)
    task = _Task(fn)
    task.moveToThread(thread)
    thread.started.connect(task.run)
    task.done.connect(callback)
    task.done.connect(thread.quit)
    thread.finished.connect(task.deleteLater)
    thread.finished.connect(thread.deleteLater)
    parent._ytdlp_task = (thread, task)  # keep alive
    thread.start()


def check_for_update(parent: QWidget, busy) -> None:
    """``busy()`` says whether downloads are running (don't swap yt-dlp mid-run)."""
    def fetch_latest() -> str:
        with urllib.request.urlopen(PYPI, timeout=15) as r:
            return json.load(r)["info"]["version"]

    def on_latest(latest, err) -> None:
        current = installed_version()
        if err:
            QMessageBox.warning(parent, "yt-dlp", f"Couldn't check for updates:\n{err}")
            return
        if _norm(latest) <= _norm(current):
            QMessageBox.information(parent, "yt-dlp", f"yt-dlp {current} is the latest version.")
            return
        in_venv = sys.prefix != sys.base_prefix
        if not in_venv:
            QMessageBox.information(
                parent, "yt-dlp",
                f"yt-dlp {latest} is available (you have {current}).\n\n"
                "Update it with your package manager, or:\n"
                "pip install -U 'yt-dlp[default]'")
            return
        if busy():
            QMessageBox.information(parent, "yt-dlp",
                                    f"yt-dlp {latest} is available. Try again once downloads finish.")
            return
        answer = QMessageBox.question(
            parent, "yt-dlp", f"Update yt-dlp from {current} to {latest}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        cmd = [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]"]
        _in_thread(parent, lambda: subprocess.run(cmd, capture_output=True, text=True, check=True),
                   on_installed)

    def on_installed(_result, err) -> None:
        if err:
            detail = getattr(err, "stderr", "") or str(err)
            QMessageBox.warning(parent, "yt-dlp", f"Update failed:\n{detail[-800:]}")
        else:
            QMessageBox.information(parent, "yt-dlp",
                                    "yt-dlp updated. Restart Minuett to use the new version.")

    _in_thread(parent, fetch_latest, on_latest)
