"""Imported playlists, each updated individually.

Top: one row per playlist with its own Check and Update buttons (there is
deliberately no "update all"). Bottom: the selected playlist's songs in
source order, including ones not downloaded yet and why.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QMenu, QMessageBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from minuett.core.library.db import (
    DONE, EXCLUDED, FAILED, LIVE, LOCAL_PREFIX, NEW, REMOVED, UNAVAILABLE, LibraryDB, Playlist,
    Track,
)
from minuett.core.downloader.playlist import ListingError, source_of_url
from minuett.ui.dialogs.import_playlist import ImportPlaylistDialog
from minuett.ui.download_manager import DownloadManager, OpResult
from minuett.ui.skin.components import (
    ViewHeader, button, label, space, tune_item_view, view,
)
from minuett.ui.skin.manager import manager

ENTRY_STATUS_TEXT = {
    DONE: "", NEW: "Not downloaded yet", FAILED: "Failed",
    UNAVAILABLE: "Unavailable on YouTube", REMOVED: "Removed from playlist",
    LIVE: "Live stream (skipped)", EXCLUDED: "Removed by you",
}
# Status -> theme color variable.
ENTRY_STATUS_COLOR = {
    NEW: "text-muted", FAILED: "warning", UNAVAILABLE: "text-muted",
    REMOVED: "text-muted", LIVE: "text-muted", EXCLUDED: "text-muted",
}
PL_COLS = ["Playlist", "Source", "Songs", "New", "Last checked", "Last updated", ""]
SOURCE_NAMES = {"youtube": "YouTube", "youtube-music": "YouTube Music", "spotify": "Spotify",
                "pandora": "Pandora", "apple-music": "Apple Music", "other": "File import",
                "folder": "Folder"}
ACTIONS_COL = len(PL_COLS) - 1
PLAYLIST_ROW_HEIGHT = 44


def _when(iso: str | None) -> str:
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    today = datetime.now().astimezone().date()
    if dt.date() == today:
        return f"Today {dt:%H:%M}"
    return f"{dt:%Y-%m-%d %H:%M}"


class PlaylistsView(QWidget):
    play_tracks = Signal(list, int)   # [Track], start index
    show_downloads = Signal()
    import_folder_requested = Signal()
    playlists_edited = Signal()

    def __init__(self, db: LibraryDB, manager_: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.manager = manager_

        self.header = ViewHeader("Playlists")
        self.header.add(button("Import Folder", "folder", "secondary",
                               "Import a folder of music as a playlist named after the folder",
                               slot=lambda: self.import_folder_requested.emit()))
        self.header.add(button("Import Playlist", "plus", "primary",
                               "Import from YouTube, YouTube Music, Spotify or an export file",
                               slot=self.import_playlist))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(PL_COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAccessibleName("Imported playlists")
        tune_item_view(self.tree, PLAYLIST_ROW_HEIGHT)
        header = self.tree.header()
        header.setStretchLastSection(False)
        # Every column is draggable. Until the user resizes one, the name column
        # takes whatever width the others leave free (see eventFilter).
        for c in range(ACTIONS_COL):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(40)
        self._fitting = False
        self._user_sized = False
        header.sectionResized.connect(self._on_section_resized)
        self.tree.viewport().installEventFilter(self)
        # Qt sizes columns from text, not from embedded widgets, so give the
        # button column the width its buttons actually need.
        header.setSectionResizeMode(ACTIONS_COL, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(ACTIONS_COL, self._actions_cell(None).sizeHint().width())
        self.tree.currentItemChanged.connect(lambda *_: self._show_entries())
        self.tree.itemDoubleClicked.connect(lambda *_: self._play_selected(0))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for text, slot in (("Play", lambda: self._play_selected(0)),
                           ("Open Folder", self._open_folder),
                           ("Import Newer Export…", lambda: self._reimport()),
                           ("Remove from Minuett…", self._remove)):
            self.tree.addAction(QAction(text, self.tree, triggered=slot))

        # Selected playlist: name, folder and its actions.
        self.detail_name = label("", None, "lg", QFont.Weight.DemiBold, elide=True)
        self.detail_path = label("", "Muted", "sm", elide=True)
        self.play_btn = button("Play", "now-playing", "ghost", "Play this playlist",
                               slot=lambda: self._play_selected(0))
        self.folder_btn = button("Open Folder", "folder", "ghost", slot=self._open_folder)
        detail = QFrame()
        detail.setObjectName("SubHeader")
        dl = QHBoxLayout(detail)
        dl.setContentsMargins(space(4), space(2), space(3), space(2))
        dl.setSpacing(space(2))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(self.detail_name)
        names.addWidget(self.detail_path)
        dl.addLayout(names, 1)
        dl.addWidget(self.play_btn)
        dl.addWidget(self.folder_btn)

        self.entries = QTreeWidget()
        self.entries.setHeaderLabels(["#", "Title", "Artist", "Status"])
        self.entries.setRootIsDecorated(False)
        self.entries.setAccessibleName("Songs in the selected playlist")
        tune_item_view(self.entries, stretch_column=1)
        eh = self.entries.header()
        eh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        eh.resizeSection(0, 52)
        eh.resizeSection(2, 200)
        eh.resizeSection(3, 200)
        self.entries.itemDoubleClicked.connect(self._play_entry)
        self.entries.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.entries.customContextMenuRequested.connect(self._entry_menu)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(detail)
        bl.addWidget(self.entries, 1)

        self.empty = label("No playlists yet. Use Import Playlist to add one from YouTube, "
                           "YouTube Music or Spotify, or from a Spotify or Pandora export "
                           "file. The whole list is shown before anything downloads.",
                           "Muted", "md")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setContentsMargins(space(6), space(6), space(6), space(6))

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(bottom)
        split.setSizes([200, 460])
        self._split = split

        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.addWidget(split)
        body_l.addWidget(self.empty)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(view(self.header, body))

        self.manager.queue_changed.connect(self.refresh)
        self.manager.op_finished.connect(self._on_finished)
        self.manager.job_updated.connect(self._on_job)
        self.refresh()

    # --- data -------------------------------------------------------------

    def current_playlist_id(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def eventFilter(self, obj, event) -> bool:
        if obj is self.tree.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_widths()
        return super().eventFilter(obj, event)

    def _on_section_resized(self, *_) -> None:
        if not self._fitting:
            self._user_sized = True

    def _fit_widths(self) -> None:
        """Size the data columns to their text, give the name column the rest."""
        if self._user_sized:
            return
        header = self.tree.header()
        self._fitting = True
        try:
            others = 0
            for c in range(1, ACTIONS_COL):
                w = max(self.tree.sizeHintForColumn(c), header.sectionSizeHint(c)) + 16
                header.resizeSection(c, w)
                others += w
            free = self.tree.viewport().width() - others - header.sectionSize(ACTIONS_COL)
            header.resizeSection(0, max(free, 240))
        finally:
            self._fitting = False

    def refresh(self) -> None:
        keep = self.current_playlist_id()
        self.tree.clear()
        playlists = self.db.playlists()
        for pl in playlists:
            item = QTreeWidgetItem(self.tree)
            item.setData(0, Qt.ItemDataRole.UserRole, pl.id)
            item.setSizeHint(0, QSize(0, PLAYLIST_ROW_HEIGHT))
            self._fill_row(item, pl)
            if pl.id == keep:
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        has_any = bool(playlists)
        self._split.setVisible(has_any)
        self.empty.setVisible(not has_any)
        yt = [p for p in playlists if p.is_imported]
        if yt:
            done = sum(p.downloaded for p in yt)
            total = sum(p.total for p in yt)
            n = len(yt)
            self.header.set_subtitle(
                f"{n} imported playlist{'s' if n != 1 else ''} · {done} of {total} songs downloaded")
        else:
            self.header.set_subtitle("Import a playlist to get started")
        self._fit_widths()
        self._show_entries()

    def _actions_cell(self, pl: Playlist | None) -> QWidget:
        cell = QWidget()
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(space(2), 0, space(3), 0)
        cl.setSpacing(space(2))
        state = self.manager.state_of(pl.id) if pl else None
        if pl is not None and not pl.is_imported:
            return cell
        if state:
            text = {"checking": "Checking…", "updating": "Updating…", "queued": "Queued"}[state]
            status = label(text, "Muted", "sm")
            status.setMinimumWidth(150)
            cl.addWidget(status, 0, Qt.AlignmentFlag.AlignVCenter)
            return cell
        if pl is not None and pl.is_folder:
            rescan = button("Rescan", "refresh", "compact",
                            "Pick up files added to or removed from the folder")
            rescan.setMinimumHeight(28)
            rescan.clicked.connect(lambda _=False, pid=pl.id: self.manager.update(pid))
            rescan.setAccessibleName(f"Rescan {pl.name}")
            cl.addWidget(rescan, 0, Qt.AlignmentFlag.AlignVCenter)
            return cell
        file_import = pl is not None and pl.is_file_import
        if file_import:
            # An export file can't be re-read from the web: offer a newer file instead.
            check = button("Re-import", "refresh", "compact",
                           "Import a newer export of this playlist")
        else:
            check = button("Check", "refresh", "compact", "See what's new without downloading")
        update = button("Update", "downloads", "compact",
                        "Download songs not downloaded yet" if file_import
                        else "Download songs added since the last update")
        for b in (check, update):
            b.setMinimumHeight(28)
        if pl is not None:
            if file_import:
                check.clicked.connect(lambda _=False, pid=pl.id: self._reimport(pid))
            else:
                check.clicked.connect(lambda _=False, pid=pl.id: self.manager.check(pid))
            update.clicked.connect(lambda _=False, pid=pl.id: self._update(pid))
            check.setAccessibleName(f"{check.text()} {pl.name}")
            update.setAccessibleName(f"Update {pl.name}")
        cl.addWidget(check, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addWidget(update, 0, Qt.AlignmentFlag.AlignVCenter)
        return cell

    def _fill_row(self, item: QTreeWidgetItem, pl: Playlist) -> None:
        f = item.font(0)
        f.setWeight(QFont.Weight.DemiBold)
        item.setFont(0, f)
        item.setText(0, pl.name)
        muted = manager().color("text-muted")
        item.setText(1, SOURCE_NAMES.get(pl.source or "", "Local") if pl.is_imported else "Local")
        if pl.is_file_import:
            item.setToolTip(1, "Imported from an export file")
        item.setForeground(1, muted)
        item.setText(2, f"{pl.downloaded} / {pl.total}" if pl.is_imported else str(pl.total))
        if pl.is_imported:
            # "New" is only as fresh as the last check.
            item.setText(3, str(pl.pending) if pl.pending else "—")
            item.setToolTip(3, "Songs not downloaded yet (as of the last check)")
            if pl.pending:
                item.setForeground(3, manager().color("accent"))
            item.setText(4, _when(pl.last_checked))
            item.setText(5, _when(pl.last_updated))
            for c in (4, 5):
                item.setForeground(c, muted)
        self.tree.setItemWidget(item, ACTIONS_COL, self._actions_cell(pl))

    def _show_entries(self) -> None:
        self.entries.clear()
        pid = self.current_playlist_id()
        pl = self.db.get_playlist(pid) if pid is not None else None
        if pl is None:
            self.detail_name.setText("")
            self.detail_path.setText("")
            return
        tm = manager()
        tracks = {t.id: t for t in self.db.playlist_tracks(pid)}
        for e in self.db.entries(pid, include_removed=True):
            t: Track | None = tracks.get(e.track_id) if e.track_id else None
            if e.status == REMOVED and t is None:
                continue
            item = QTreeWidgetItem(self.entries)
            item.setText(0, "" if e.position is None else str(e.position + 1))
            item.setText(1, (t.title if t else None) or e.title or e.youtube_id or e.item_id or "")
            item.setText(2, (t.artist if t else None) or e.artist or "")
            local = (e.item_id or "").startswith(LOCAL_PREFIX)
            item.setText(3, "Added by you" if local and e.status == DONE
                         else ENTRY_STATUS_TEXT.get(e.status, e.status))
            item.setData(3, Qt.ItemDataRole.UserRole, e.id)
            item.setData(2, Qt.ItemDataRole.UserRole, e.status)
            if e.status in (FAILED, UNAVAILABLE) and e.error:
                item.setToolTip(3, e.error)
            item.setData(0, Qt.ItemDataRole.UserRole, e.track_id if e.position is not None else None)
            item.setForeground(0, tm.color("text-muted"))
            if e.status in ENTRY_STATUS_COLOR:
                item.setForeground(3, tm.color(ENTRY_STATUS_COLOR[e.status]))
            if not t or e.position is None:
                for c in (1, 2):
                    item.setForeground(c, tm.color("text-muted"))
        self.detail_name.setText(pl.name)
        self.detail_path.setText(pl.folder or "")
        self.detail_path.setToolTip(pl.folder or "")
        self.folder_btn.setVisible(bool(pl.folder))
        self.play_btn.setEnabled(bool(tracks))

    # --- actions ----------------------------------------------------------

    def import_playlist(self) -> None:
        dlg = ImportPlaylistDialog(self)
        if dlg.exec() != ImportPlaylistDialog.DialogCode.Accepted:
            return
        if dlg.mode == "file":
            self.manager.import_file(dlg.path, dlg.source, dlg.name)
            self.show_downloads.emit()
            return
        from minuett.core.downloader.playlist import normalise_url
        url = dlg.url
        key = url
        if source_of_url(url) == "spotify":
            from minuett.core.downloader.sources.spotify import canonical_url, parse_spotify_url
            try:
                key = canonical_url(*parse_spotify_url(url))
            except ListingError:
                pass
        else:
            key = normalise_url(url)
        existing = self.db.playlist_by_url(key)
        if existing:
            QMessageBox.information(
                self, "Already Imported",
                f"“{existing.name}” is already in your library. Use its Update button "
                "to fetch songs added since.")
            return
        self.manager.import_url(url)
        self.show_downloads.emit()

    def _reimport(self, pid: int | None = None) -> None:
        """Import a newer export file into an existing file-imported playlist."""
        pl = self.db.get_playlist(pid if pid is not None else self.current_playlist_id() or -1)
        if pl is None or not pl.is_file_import:
            QMessageBox.information(self, "Import Newer Export",
                                    "Only playlists imported from a file can be re-imported. "
                                    "Linked playlists update with Check and Update.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Newer Export of “{pl.name}”", str(Path.home() / "Downloads"),
            "Playlist exports (*.csv *.tsv *.txt);;All files (*)")
        if path:
            self.manager.import_file(path, pl.source or "other", pl.name)
            self.show_downloads.emit()

    def _update(self, pid: int) -> None:
        self.manager.update(pid)
        self.show_downloads.emit()

    def _play_selected(self, start: int) -> None:
        pid = self.current_playlist_id()
        if pid is not None:
            tracks = self.db.playlist_tracks(pid)
            if tracks:
                self.play_tracks.emit(tracks, start)

    def _play_entry(self, item: QTreeWidgetItem) -> None:
        tid = item.data(0, Qt.ItemDataRole.UserRole)
        pid = self.current_playlist_id()
        if tid is None or pid is None:
            return
        tracks = self.db.playlist_tracks(pid)
        start = next((i for i, t in enumerate(tracks) if t.id == tid), 0)
        self.play_tracks.emit(tracks, start)

    def _entry_menu(self, pos) -> None:
        item = self.entries.itemAt(pos)
        pid = self.current_playlist_id()
        if item is None or pid is None:
            return
        entry_id = item.data(3, Qt.ItemDataRole.UserRole)
        status = item.data(2, Qt.ItemDataRole.UserRole)
        track_id = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if track_id is not None:
            menu.addAction("Play", lambda: self._play_entry(item))
            menu.addAction("Remove from Playlist", lambda: self._remove_entry(pid, track_id))
        if status == EXCLUDED:
            menu.addAction("Restore (download again on the next Update)",
                           lambda: self._restore_entry(entry_id))
        if not menu.isEmpty():
            menu.exec(self.entries.viewport().mapToGlobal(pos))

    def _remove_entry(self, pid: int, track_id: int) -> None:
        self.db.remove_from_playlist(pid, [track_id])
        self.refresh()
        self.playlists_edited.emit()

    def _restore_entry(self, entry_id: int) -> None:
        self.db.restore_entry(entry_id)
        self.refresh()
        self.playlists_edited.emit()

    def _open_folder(self) -> None:
        pid = self.current_playlist_id()
        pl = self.db.get_playlist(pid) if pid else None
        if pl and pl.folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(pl.folder))

    def _remove(self) -> None:
        pid = self.current_playlist_id()
        pl = self.db.get_playlist(pid) if pid else None
        if pl is None or self.manager.is_busy(pl.id):
            return
        answer = QMessageBox.question(
            self, "Remove Playlist",
            f"Stop tracking “{pl.name}”?\n\nIts songs and files stay in your library; "
            "only the playlist and its update history are removed.")
        if answer == QMessageBox.StandardButton.Yes:
            self.db.remove_playlist(pl.id)
            self.refresh()

    # --- manager feedback -------------------------------------------------

    def _on_job(self, index: int, job) -> None:
        # Cheap live refresh of the counts while an update runs.
        if job.status.value in ("done", "failed", "unavailable"):
            self.refresh()

    def _on_finished(self, result: OpResult) -> None:
        self.refresh()
