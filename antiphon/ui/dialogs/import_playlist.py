"""Import a playlist from a link (YouTube, YouTube Music, Spotify) or from an
exported file (Spotify, Pandora, Apple Music, … via Exportify, TuneMyMusic
or Soundiiz)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QTabWidget, QVBoxLayout, QWidget,
)

from antiphon.core.downloader.playlist import ListingError, source_of_url
from antiphon.core.downloader.sources.files import SOURCES, parse_export
from antiphon.ui.skin.components import button, label, space

LINK_HELP = ("Paste a playlist link from YouTube, YouTube Music or Spotify (playlists and albums). "
             "The whole list is shown before anything downloads.")
SPOTIFY_NOTE = ("Spotify songs are found on YouTube Music by title, artist and length; Spotify's "
                "own audio is protected and never downloaded. Spotify's public page lists only the "
                "first 100 songs; for longer playlists, import an export file instead.")
FILE_HELP = {
    "spotify": "Export with Exportify (exportify.app): sign in, then Export next to the "
               "playlist. Import the CSV here.",
    "pandora": "Export with TuneMyMusic or Soundiiz: pick Pandora as the source and "
               "“file” (CSV or TXT) as the destination.",
    "apple-music": "Export with TuneMyMusic or Soundiiz to a CSV or TXT file.",
    "other": "A CSV export from Exportify, TuneMyMusic or Soundiiz, or a text file with one "
             "“Artist - Title” per line.",
}


class ImportPlaylistDialog(QDialog):
    """After exec(): ``mode`` is "link" or "file"; read url or path/source/name."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Playlist")
        self.mode = "link"
        self.url = ""
        self.path = ""
        self.source = "spotify"
        self.name = ""

        # --- link tab ---
        self.url_edit = QLineEdit(placeholderText="https://…")
        self.url_edit.setAccessibleName("Playlist link")
        self.url_edit.textChanged.connect(self._validate)
        self.link_note = label("", "Muted", "sm")
        self.link_note.setWordWrap(True)
        link = QWidget()
        ll = QVBoxLayout(link)
        ll.setContentsMargins(space(4), space(4), space(4), space(4))
        ll.setSpacing(space(3))
        intro = label(LINK_HELP, None, "md")
        intro.setWordWrap(True)
        ll.addWidget(intro)
        ll.addWidget(self.url_edit)
        ll.addWidget(self.link_note)
        ll.addStretch(1)

        # --- file tab ---
        self.source_box = QComboBox()
        for key, text in SOURCES.items():
            self.source_box.addItem(text, key)
        self.source_box.currentIndexChanged.connect(self._on_source)
        self.file_edit = QLineEdit(placeholderText="Choose a CSV or TXT export…")
        self.file_edit.setReadOnly(True)
        browse = button("Choose File…", "folder", "secondary", slot=self._choose_file)
        file_row = QHBoxLayout()
        file_row.setSpacing(space(2))
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse)
        self.name_edit = QLineEdit(placeholderText="Playlist name")
        self.name_edit.textChanged.connect(self._validate)
        self.file_help = label("", "Muted", "sm")
        self.file_help.setWordWrap(True)
        self.file_status = label("", "Muted", "sm")
        self.file_status.setWordWrap(True)
        file_tab = QWidget()
        form = QFormLayout(file_tab)
        form.setContentsMargins(space(4), space(4), space(4), space(4))
        form.setHorizontalSpacing(space(3))
        form.setVerticalSpacing(space(3))
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for text, w in (("Exported from", self.source_box), ("", self.file_help),
                        ("File", file_row), ("Playlist name", self.name_edit),
                        ("", self.file_status)):
            lab = QLabel(text)
            lab.setObjectName("FormLabel")
            form.addRow(lab, w)

        self.tabs = QTabWidget()
        self.tabs.addTab(link, "Link")
        self.tabs.addTab(file_tab, "File")
        self.tabs.currentChanged.connect(self._validate)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(4), space(4), space(4), space(4))
        layout.setSpacing(space(3))
        layout.addWidget(self.tabs)
        layout.addWidget(self.buttons)
        self.resize(600, 0)
        self._on_source()
        self._validate()

    def _on_source(self, *_) -> None:
        self.source = self.source_box.currentData()
        self.file_help.setText(FILE_HELP[self.source])

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Playlist Export", str(Path.home() / "Downloads"),
            "Playlist exports (*.csv *.tsv *.txt);;All files (*)")
        if not path:
            return
        self.file_edit.setText(path)
        try:
            export = parse_export(path, self.source)
        except (ListingError, OSError) as e:
            self.file_status.setText(str(e))
            self.path = ""
        else:
            self.path = path
            n = len(export.entries)
            self.file_status.setText(f"{n} song{'s' if n != 1 else ''} found.")
            if not self.name_edit.text().strip():
                self.name_edit.setText(export.name or Path(path).stem.replace("_", " "))
        self._validate()

    def _validate(self, *_) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.tabs.currentIndex() == 0:
            url = self.url_edit.text().strip()
            source = source_of_url(url) if url else None
            self.link_note.setText(SPOTIFY_NOTE if source == "spotify" else
                                   ("That doesn't look like a YouTube or Spotify link."
                                    if url and source is None else ""))
            ok.setEnabled(source is not None)
        else:
            ok.setEnabled(bool(self.path and self.name_edit.text().strip()))

    def _accept(self) -> None:
        if self.tabs.currentIndex() == 0:
            self.mode, self.url = "link", self.url_edit.text().strip()
        else:
            self.mode, self.name = "file", self.name_edit.text().strip()
        self.accept()
