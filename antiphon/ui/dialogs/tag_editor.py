"""Batch tag editor: one or many tracks, only touched fields are written.

Fields whose values differ across the selection start empty with a
"(multiple values)" placeholder. A field counts as changed once the user
edits it (``textEdited``), so leaving a mixed field alone never flattens it,
while typing and then clearing it deliberately blanks it on every track.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from antiphon.core.library.db import LibraryDB, Track
from antiphon.core.library.editor import KEEP, EditResult, common_values, edit_tracks
from antiphon.core.library.tags import Cover, TagError, read_cover, sniff_image_mime
from antiphon.ui.skin.components import button, label, space

MULTIPLE = "(multiple values)"
FIELDS = [
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("album_artist", "Album artist"),
    ("genre", "Genre"),
    ("year", "Year"),
    ("track_no", "Track"),
    ("disc_no", "Disc"),
]
COMPLETE_FROM_LIBRARY = ("artist", "album", "album_artist", "genre")
COVER_SIZE = 220


class TagEditorDialog(QDialog):
    def __init__(self, db: LibraryDB, tracks: list[Track], parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.tracks = tracks
        self.result_: EditResult | None = None
        self._dirty: set[str] = set()
        self._cover: object = KEEP

        n = len(tracks)
        self.setWindowTitle("Edit Tags" if n == 1 else f"Edit Tags — {n} tracks")

        form = QFormLayout()
        form.setHorizontalSpacing(space(3))
        form.setVerticalSpacing(space(2))
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.edits: dict[str, QLineEdit] = {}
        common = common_values(tracks)
        for field, field_label in FIELDS:
            edit = QLineEdit()
            same, value = common[field]
            if same:
                edit.setText("" if value is None else str(value))
            else:
                edit.setPlaceholderText(MULTIPLE)
            if field in COMPLETE_FROM_LIBRARY:
                completer = QCompleter(db.distinct(field), edit)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                edit.setCompleter(completer)
            if field in ("year", "track_no", "disc_no"):
                edit.setMaximumWidth(140)
            edit.textEdited.connect(lambda _t, f=field: self._dirty.add(f))
            self.edits[field] = edit
            row_label = QLabel(field_label)
            row_label.setObjectName("FormLabel")
            row_label.setBuddy(edit)
            form.addRow(row_label, edit)
        if n > 1:
            # Per-track titles and numbers are rarely meant to be flattened.
            for f in ("title", "track_no"):
                if not common[f][0]:
                    self.edits[f].setToolTip("Typing here overwrites this field on every selected track")

        # --- cover art ---
        self.cover_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setFixedSize(QSize(COVER_SIZE, COVER_SIZE))
        self.cover_label.setFrameShape(QLabel.Shape.StyledPanel)
        self.cover_note = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.cover_note.setObjectName("Muted")
        replace = button("Replace…", "image", "secondary", "Choose a JPEG or PNG",
                         slot=self._replace_cover)
        remove = button("Remove", "trash", "ghost", "Remove the artwork", slot=self._remove_cover)
        cover_btns = QHBoxLayout()
        cover_btns.setSpacing(space(2))
        cover_btns.addWidget(replace)
        cover_btns.addWidget(remove)
        cover_col = QVBoxLayout()
        cover_col.setSpacing(space(2))
        cover_col.addWidget(self.cover_label)
        cover_col.addWidget(self.cover_note)
        cover_col.addLayout(cover_btns)
        cover_col.addStretch(1)
        self._load_existing_cover()

        body = QHBoxLayout()
        body.setSpacing(space(5))
        body.addLayout(cover_col)
        body.addLayout(form, 1)

        path_label = label(tracks[0].path if n == 1 else f"{n} files selected", "Muted", "sm")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(5), space(5), space(5), space(4))
        layout.setSpacing(space(4))
        layout.addLayout(body)
        layout.addWidget(path_label)
        layout.addWidget(buttons)
        self.resize(680, 0)

    # --- cover ------------------------------------------------------------

    def _load_existing_cover(self) -> None:
        covers = []
        for t in self.tracks[:200]:  # enough to know whether they agree
            try:
                covers.append(read_cover(t.path))
            except TagError:
                covers.append(None)
        first = covers[0]
        same = all(c == first for c in covers) and len(self.tracks) <= 200
        self._show_cover(first if same else None,
                         "" if same else "Selected tracks have different artwork")
        if same and first is None:
            self.cover_note.setText("No artwork")

    def _show_cover(self, cover: Cover | None, note: str = "") -> None:
        pix = QPixmap()
        if cover is not None and pix.loadFromData(cover.data):
            self.cover_label.setPixmap(pix.scaled(
                COVER_SIZE, COVER_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover_label.clear()
            self.cover_label.setText("♪")
        self.cover_note.setText(note)

    def _replace_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Art", str(Path(self.tracks[0].path).parent),
            "Images (*.jpg *.jpeg *.png)")
        if not path:
            return
        data = Path(path).read_bytes()
        try:
            mime = sniff_image_mime(data)
        except TagError as e:
            QMessageBox.warning(self, "Cover Art", str(e))
            return
        if mime not in ("image/jpeg", "image/png"):
            QMessageBox.warning(self, "Cover Art", "Please choose a JPEG or PNG image.")
            return
        self._cover = Cover(data, mime)
        self._show_cover(self._cover, "New artwork (saved on Save)")

    def _remove_cover(self) -> None:
        self._cover = None
        self._show_cover(None, "Artwork will be removed")

    # --- save -------------------------------------------------------------

    def changes(self) -> dict[str, object]:
        return {f: self.edits[f].text() for f in self._dirty}

    def _save(self) -> None:
        changes = self.changes()
        if not changes and self._cover is KEEP:
            self.reject()
            return
        try:
            self.result_ = edit_tracks(self.db, [t.id for t in self.tracks], changes, self._cover)
        except ValueError as e:
            QMessageBox.warning(self, "Edit Tags", str(e))
            return
        if self.result_.failed:
            lines = [f"• {msg}" for msg in list(self.result_.failed.values())[:10]]
            more = len(self.result_.failed) - len(lines)
            if more > 0:
                lines.append(f"…and {more} more")
            QMessageBox.warning(
                self, "Edit Tags",
                f"{len(self.result_.failed)} file(s) could not be written and were left "
                f"unchanged:\n\n" + "\n".join(lines))
        self.accept()
