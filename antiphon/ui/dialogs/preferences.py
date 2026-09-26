"""Preferences: where downloads go and what format they're kept in."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QWidget,
)

from antiphon.core.downloader.playlist import js_runtimes
from antiphon.core.downloader.worker import Preferences
from antiphon.ui.download_manager import default_music_root

FORMATS = [("native", "Original (Opus or M4A, no re-encoding)"),
           ("mp3", "MP3 (for older devices; re-encodes)")]


def load_preferences(settings: QSettings) -> Preferences:
    root = settings.value("downloads/root", str(default_music_root()), type=str)
    fmt = settings.value("downloads/format", "native", type=str)
    return Preferences(music_root=Path(root).expanduser(), audio_format=fmt)


class PreferencesDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        prefs = load_preferences(settings)

        self.root = QLineEdit(str(prefs.music_root))
        browse = QPushButton("Browse…", clicked=self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.root, 1)
        rl.addWidget(browse)

        self.format = QComboBox()
        for key, label in FORMATS:
            self.format.addItem(label, key)
        self.format.setCurrentIndex(max(0, [k for k, _ in FORMATS].index(prefs.audio_format)
                                        if prefs.audio_format in dict(FORMATS) else 0))

        runtimes = ", ".join(js_runtimes()) or "none found — YouTube downloads may fail " \
            "(install Deno or Node.js)"

        form = QFormLayout(self)
        form.addRow("Download folder", row)
        form.addRow("", QLabel("Each playlist gets its own subfolder."))
        form.addRow("Audio format", self.format)
        form.addRow("JavaScript runtime", QLabel(runtimes))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(560, 0)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Download Folder", self.root.text())
        if path:
            self.root.setText(path)

    def _save(self) -> None:
        self.settings.setValue("downloads/root", self.root.text().strip())
        self.settings.setValue("downloads/format", self.format.currentData())
        self.accept()
