"""Preferences: where downloads go and what format they're kept in."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QWidget,
)

from antiphon.core.downloader.playlist import js_runtimes
from antiphon.core.downloader.worker import Preferences, normalise_format
from antiphon.ui.download_manager import default_music_root
from antiphon.ui.skin.components import button, label, space

FORMATS = [("opus", "Opus, best quality (copies Opus streams; converts others at 256 kbps)"),
           ("original", "Original (keep YouTube's format as-is: Opus or M4A)"),
           ("mp3", "MP3 V0 (for older devices; always re-encodes)")]


def load_preferences(settings: QSettings) -> Preferences:
    root = settings.value("downloads/root", str(default_music_root()), type=str)
    fmt = normalise_format(settings.value("downloads/format", "opus", type=str))
    return Preferences(music_root=Path(root).expanduser(), audio_format=fmt)


class PreferencesDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        prefs = load_preferences(settings)

        self.root = QLineEdit(str(prefs.music_root))
        browse = button("Browse…", "folder", "secondary", slot=self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(space(2))
        rl.addWidget(self.root, 1)
        rl.addWidget(browse)

        self.format = QComboBox()
        for key, format_label in FORMATS:
            self.format.addItem(format_label, key)
        self.format.setCurrentIndex(max(0, [k for k, _ in FORMATS].index(prefs.audio_format)
                                        if prefs.audio_format in dict(FORMATS) else 0))

        runtimes = ", ".join(js_runtimes()) or "none found — YouTube downloads may fail " \
            "(install Deno or Node.js)"

        form = QFormLayout(self)
        form.setContentsMargins(space(5), space(5), space(5), space(4))
        form.setHorizontalSpacing(space(3))
        form.setVerticalSpacing(space(3))
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for text, widget in (("Download folder", row), ("", label("Each playlist gets its own subfolder.", "Muted", "sm")),
                             ("Audio format", self.format), ("JavaScript runtime", label(runtimes, "Muted"))):
            lab = QLabel(text)
            lab.setObjectName("FormLabel")
            form.addRow(lab, widget)
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
