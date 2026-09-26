"""Imported playlists, each updated individually.

Top: one row per playlist with its own Check and Update buttons (there is
deliberately no "update all"). Bottom: the selected playlist's songs in
YouTube order, including ones not downloaded yet and why.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from antiphon.core.library.db import (
    DONE, FAILED, LIVE, NEW, REMOVED, UNAVAILABLE, LibraryDB, Playlist, Track,
)
from antiphon.ui.download_manager import DownloadManager, OpResult

ENTRY_STATUS_TEXT = {
    DONE: "", NEW: "Not downloaded yet", FAILED: "Failed",
    UNAVAILABLE: "Unavailable on YouTube", REMOVED: "Removed from playlist",
    LIVE: "Live stream (skipped)",
}
PL_COLS = ["Playlist", "Songs", "New", "Last checked", "Last updated", ""]


def _when(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    return dt.strftime("%Y-%m-%d %H:%M")


class PlaylistsView(QWidget):
    play_tracks = Signal(list, int)   # [Track], start index
    show_downloads = Signal()

    def __init__(self, db: LibraryDB, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.manager = manager
        self._checked_this_session: set[int] = set()

        import_btn = QPushButton("Import YouTube Playlist…", clicked=self.import_playlist)
        self.summary = QLabel()
        top = QHBoxLayout()
        top.addWidget(import_btn)
        top.addStretch(1)
        top.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(PL_COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(PL_COLS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.tree.currentItemChanged.connect(lambda *_: self._show_entries())
        self.tree.itemDoubleClicked.connect(lambda *_: self._play_selected(0))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for text, slot in (("Play", lambda: self._play_selected(0)),
                           ("Open Folder", self._open_folder),
                           ("Remove from Antiphon…", self._remove)):
            self.tree.addAction(QAction(text, self.tree, triggered=slot))

        self.entries = QTreeWidget()
        self.entries.setHeaderLabels(["#", "Title", "Artist", "Status"])
        self.entries.setRootIsDecorated(False)
        self.entries.setUniformRowHeights(True)
        self.entries.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.entries.itemDoubleClicked.connect(self._play_entry)
        self.entries_label = QLabel()

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.entries_label)
        bl.addWidget(self.entries)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(bottom)
        split.setSizes([260, 400])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        manager.queue_changed.connect(self.refresh)
        manager.op_finished.connect(self._on_finished)
        manager.plan_ready.connect(lambda pid, *_: self._checked_this_session.add(pid))
        manager.job_updated.connect(self._on_job)
        self.refresh()

    # --- data -------------------------------------------------------------

    def current_playlist_id(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def refresh(self) -> None:
        keep = self.current_playlist_id()
        self.tree.clear()
        playlists = self.db.playlists()
        for pl in playlists:
            item = QTreeWidgetItem(self.tree)
            item.setData(0, Qt.ItemDataRole.UserRole, pl.id)
            self._fill_row(item, pl)
            if pl.id == keep:
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        n_yt = sum(p.is_youtube for p in playlists)
        self.summary.setText(f"{n_yt} imported playlist{'s' if n_yt != 1 else ''}")
        self._show_entries()

    def _fill_row(self, item: QTreeWidgetItem, pl: Playlist) -> None:
        item.setText(0, pl.name)
        item.setText(1, f"{pl.downloaded} / {pl.total}" if pl.is_youtube else str(pl.total))
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if pl.is_youtube:
            # "New" is only as fresh as the last check; say so until checked.
            item.setText(2, str(pl.pending) if pl.pending else "—")
            item.setToolTip(2, "Songs on YouTube not yet downloaded (as of the last check)")
            item.setText(3, _when(pl.last_checked))
            item.setText(4, _when(pl.last_updated))
        state = self.manager.state_of(pl.id)
        cell = QWidget()
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(2, 0, 2, 0)
        if not pl.is_youtube:
            pass
        elif state:
            cl.addWidget(QLabel({"checking": "Checking…", "updating": "Updating…",
                                 "queued": "Queued"}[state]))
        else:
            check = QPushButton("Check", toolTip="See what's new without downloading")
            update = QPushButton("Update", toolTip="Download songs added since the last update")
            check.clicked.connect(lambda _=False, pid=pl.id: self.manager.check(pid))
            update.clicked.connect(lambda _=False, pid=pl.id: self._update(pid))
            cl.addWidget(check)
            cl.addWidget(update)
        self.tree.setItemWidget(item, len(PL_COLS) - 1, cell)

    def _show_entries(self) -> None:
        self.entries.clear()
        pid = self.current_playlist_id()
        if pid is None:
            self.entries_label.setText("Import a YouTube playlist to get started.")
            return
        pl = self.db.get_playlist(pid)
        if pl is None:
            return
        tracks = {t.id: t for t in self.db.playlist_tracks(pid)}
        for e in self.db.entries(pid, include_removed=True):
            t: Track | None = tracks.get(e.track_id) if e.track_id else None
            if e.status == REMOVED and t is None:
                continue
            item = QTreeWidgetItem(self.entries)
            item.setText(0, "" if e.position is None else str(e.position + 1))
            item.setText(1, (t.title if t else None) or e.title or e.youtube_id or "")
            item.setText(2, (t.artist if t else "") or "")
            status = ENTRY_STATUS_TEXT.get(e.status, e.status)
            if e.status in (FAILED, UNAVAILABLE) and e.error:
                item.setToolTip(3, e.error)
            item.setText(3, status)
            item.setData(0, Qt.ItemDataRole.UserRole, e.track_id if e.position is not None else None)
            if not t or e.position is None:
                for c in range(4):
                    item.setForeground(c, self.palette().placeholderText())
        where = f" — {pl.folder}" if pl.folder else ""
        self.entries_label.setText(f"<b>{pl.name}</b>{where}")

    # --- actions ----------------------------------------------------------

    def import_playlist(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Import YouTube Playlist",
            "Playlist link (the whole list is shown before anything downloads):")
        if not ok or not url.strip():
            return
        from antiphon.core.downloader.playlist import normalise_url
        existing = self.db.playlist_by_url(normalise_url(url))
        if existing:
            QMessageBox.information(
                self, "Already Imported",
                f"“{existing.name}” is already in your library. Use its Update button "
                "to fetch songs added since.")
            return
        self.manager.import_url(url.strip())
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
        if job.status.value in ("done", "failed"):
            self.refresh()

    def _on_finished(self, result: OpResult) -> None:
        self.refresh()
