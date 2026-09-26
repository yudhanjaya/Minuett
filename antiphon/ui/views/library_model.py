"""Table model over the library's tracks, plus a multi-column search proxy."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal

from antiphon.core.library.db import LibraryDB, Track
from antiphon.core.library.editor import edit_tracks

# (attribute, header)
COLUMNS: list[tuple[str, str]] = [
    ("track_no", "#"),
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("source_playlist", "Playlist"),
    ("duration_ms", "Time"),
    ("genre", "Genre"),
    ("year", "Year"),
    ("codec", "Format"),
    ("bitrate", "Bitrate"),
    ("play_count", "Plays"),
    ("date_added", "Added"),
]
SEARCH_ATTRS = ("title", "artist", "album", "album_artist", "genre", "source_playlist")
# Columns that map to file tags and can be edited inline.
EDITABLE_ATTRS = frozenset({"track_no", "title", "artist", "album", "genre", "year"})


def format_ms(ms: int | None) -> str:
    if not ms:
        return ""
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class LibraryModel(QAbstractTableModel):
    TrackRole = Qt.ItemDataRole.UserRole + 1
    SortRole = Qt.ItemDataRole.UserRole + 2

    edit_failed = Signal(str)       # message for the user
    tracks_edited = Signal(list)    # track ids whose metadata changed

    def __init__(self, db: LibraryDB, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.tracks: list[Track] = []
        self._rows: dict[int, int] = {}  # track id -> row
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.tracks = self.db.all_tracks()
        self._rows = {t.id: i for i, t in enumerate(self.tracks)}
        self.endResetModel()

    def refresh_tracks(self, track_ids: list[int]) -> None:
        """Re-read edited tracks from the DB without resetting the view."""
        for tid in track_ids:
            row = self._rows.get(tid)
            fresh = self.db.get(tid)
            if row is None or fresh is None:
                continue
            self.tracks[row] = fresh
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))
        self.tracks_edited.emit(list(track_ids))

    def flags(self, index: QModelIndex):
        f = super().flags(index)
        if index.isValid() and COLUMNS[index.column()][0] in EDITABLE_ATTRS:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        attr = COLUMNS[index.column()][0]
        track = self.tracks[index.row()]
        current = "" if getattr(track, attr) is None else str(getattr(track, attr))
        if str(value).strip() == current:
            return False
        try:
            result = edit_tracks(self.db, [track.id], {attr: value})
        except ValueError as e:
            self.edit_failed.emit(str(e))
            return False
        if not result.ok:
            self.edit_failed.emit(next(iter(result.failed.values())))
            return False
        self.refresh_tracks([track.id])
        return True

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self.tracks[index.row()]
        attr = COLUMNS[index.column()][0]
        value = getattr(track, attr)
        if role == Qt.ItemDataRole.DisplayRole:
            if attr == "duration_ms":
                return format_ms(value)
            if attr == "bitrate":
                return f"{round(value / 1000)} kbps" if value else ""
            if attr == "date_added":
                return (value or "")[:10]
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.EditRole:
            return "" if value is None else str(value)
        if role == self.SortRole:
            # Raw value so numbers sort numerically.
            return value if value is not None else ""
        if role == Qt.ItemDataRole.TextAlignmentRole and attr in (
                "track_no", "duration_ms", "year", "bitrate", "play_count"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == self.TrackRole:
            return track
        return None


class LibraryFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self.setSortRole(LibraryModel.SortRole)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_search(self, text: str) -> None:
        self._needle = text.casefold().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        if not self._needle:
            return True
        track: Track = self.sourceModel().tracks[row]
        hay = " ".join(str(getattr(track, a) or "") for a in SEARCH_ATTRS).casefold()
        return all(word in hay for word in self._needle.split())

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a, b = left.data(LibraryModel.SortRole), right.data(LibraryModel.SortRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        # Blanks sort last regardless of direction would be nicer; keep simple.
        return str(a).casefold() < str(b).casefold()
