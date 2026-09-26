"""Live view of the running import/update: one row per song, with progress."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QHeaderView, QStackedWidget, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget,
)

from minuett.core.downloader.worker import Job, JobStatus
from minuett.core.library.db import LibraryDB
from minuett.ui.download_manager import (
    CHECK, IMPORT, IMPORT_FOLDER, RETRY, DownloadManager, OpResult,
)
from minuett.ui.skin.components import (
    ViewHeader, button, label, space, tune_item_view, view,
)
from minuett.ui.skin.manager import manager

COLS = ["#", "Title", "Status", "Progress"]
STATUS_TEXT = {
    JobStatus.QUEUED: "Queued", JobStatus.MATCHING: "Finding on YouTube Music",
    JobStatus.DOWNLOADING: "Downloading",
    JobStatus.CONVERTING: "Converting", JobStatus.TAGGING: "Tagging",
    JobStatus.DONE: "Done", JobStatus.SKIPPED: "Skipped",
    JobStatus.FAILED: "Failed", JobStatus.CANCELLED: "Cancelled",
    JobStatus.UNAVAILABLE: "Unavailable",
}
STATUS_COLOR = {  # theme variables
    JobStatus.DOWNLOADING: "accent", JobStatus.CONVERTING: "accent", JobStatus.TAGGING: "accent",
    JobStatus.MATCHING: "accent",
    JobStatus.FAILED: "warning", JobStatus.QUEUED: "text-muted", JobStatus.SKIPPED: "text-muted",
    JobStatus.UNAVAILABLE: "text-muted", JobStatus.CANCELLED: "text-muted",
}


class JobsModel(QAbstractTableModel):
    ProgressRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.jobs: list[Job] = []

    def set_jobs(self, jobs: list[Job]) -> None:
        self.beginResetModel()
        self.jobs = list(jobs)
        self.endResetModel()

    def update_job(self, i: int, job: Job) -> None:
        if 0 <= i < len(self.jobs):
            self.jobs[i] = job
            self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLS) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.jobs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        job = self.jobs[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                pos = job.entry.position
                return "" if pos is None else str(pos + 1)
            if col == 1:
                return job.title
            if col == 2:
                return STATUS_TEXT[job.status]
        if role == Qt.ItemDataRole.ToolTipRole and job.error:
            return job.error
        if role == Qt.ItemDataRole.ForegroundRole and col == 2 and job.status in STATUS_COLOR:
            return manager().color(STATUS_COLOR[job.status])
        if role == Qt.ItemDataRole.ForegroundRole and col == 0:
            return manager().color("text-muted")
        if role == self.ProgressRole:
            return job
        return None


class ProgressDelegate(QStyledItemDelegate):
    """A slim rounded bar with the percentage beside it."""

    def paint(self, p: QPainter, option, index) -> None:
        job: Job | None = index.data(JobsModel.ProgressRole)
        if job is None or index.column() != 3:
            return super().paint(p, option, index)
        tm = manager()
        if job.status in (JobStatus.CONVERTING, JobStatus.TAGGING, JobStatus.DONE):
            frac = 1.0
        elif job.status is JobStatus.DOWNLOADING:
            frac = job.progress
        else:
            return  # nothing to show for queued/failed/skipped rows
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(space(2), 0, -space(2), 0)
        pct_w = 40.0 if job.status is JobStatus.DOWNLOADING else 0.0
        bar = QRectF(r.left(), r.center().y() - 3, r.width() - pct_w, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tm.color("groove"))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(tm.color("groove-fill") if job.status is not JobStatus.DONE else tm.color("text-muted"))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()), 3, 3)
        if pct_w:
            p.setPen(tm.color("text-muted"))
            p.drawText(QRectF(bar.right(), r.top(), pct_w, r.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{round(frac * 100)}%")
        p.restore()


class DownloadsView(QWidget):
    def __init__(self, db: LibraryDB, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.manager = manager
        self._title = ""

        self.header = ViewHeader("Downloads", "Nothing downloading")
        self.retry_btn = button("Retry Failed", "retry", "ghost",
                                "Try the failed songs again", slot=manager.retry_failed)
        self.cancel_btn = button("Cancel", "close", "ghost",
                                 "Stop after the current song", slot=manager.cancel)
        self.retry_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.header.add(self.retry_btn, self.cancel_btn)
        self.heading = self.header.subtitle  # status line lives in the header

        self.model = JobsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAccessibleName("Download queue")
        tune_item_view(self.table, stretch_column=1)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 200)

        self.empty = label("Nothing downloading. Import a playlist, or press Update on one "
                           "in Playlists; its songs appear here with their progress.",
                           "Muted", "md")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setContentsMargins(space(6), space(6), space(6), space(6))
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.table)
        self.model.modelReset.connect(
            lambda: self.body.setCurrentWidget(self.table if self.model.jobs else self.empty))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view(self.header, self.body))

        manager.op_started.connect(self._on_started)
        manager.plan_ready.connect(self._on_plan)
        manager.job_updated.connect(self._on_job)
        manager.op_finished.connect(self._on_finished)

    def _on_started(self, op) -> None:
        self.cancel_btn.setEnabled(op.kind != CHECK)
        self.retry_btn.setEnabled(False)
        if op.kind == IMPORT_FOLDER:
            self.heading.setText(f"Importing folder {op.path}…")
        elif op.kind == IMPORT:
            self.heading.setText(f"Reading playlist… {op.url}")
        elif op.kind != RETRY:
            pl = self.db.get_playlist(op.playlist_id)
            self._title = pl.name if pl else "playlist"
            self.heading.setText(f"Checking <b>{self._title}</b> for new songs…")
        else:
            self.heading.setText(f"Retrying failed songs in <b>{self._title}</b>…")

    def _on_plan(self, pid: int, plan, jobs: list) -> None:
        pl = self.db.get_playlist(pid)
        self._title = pl.name if pl else "playlist"
        self.model.set_jobs(jobs)
        extra = []
        if plan.linked:
            extra.append(f"{plan.linked} already in library")
        if plan.unavailable:
            extra.append(f"{plan.unavailable} unavailable")
        if plan.live:
            extra.append(f"{plan.live} live stream{'s' if plan.live != 1 else ''} skipped")
        if plan.truncated:
            extra.append("Spotify's public page shows only the first 100 songs; "
                         "import an export file for the rest")
        if plan.removed:
            extra.append(f"{plan.removed} removed on YouTube")
        tail = f" ({', '.join(extra)})" if extra else ""
        n = len(jobs)
        self.heading.setText(
            f"<b>{self._title}</b>: {n} new song{'s' if n != 1 else ''} to download{tail}"
            if n else f"<b>{self._title}</b> is up to date{tail}")

    def _on_job(self, i: int, job: Job) -> None:
        self.model.update_job(i, job)
        finished = (JobStatus.DONE, JobStatus.FAILED, JobStatus.UNAVAILABLE, JobStatus.SKIPPED)
        done = sum(j.status in finished for j in self.model.jobs)
        self.heading.setText(f"Updating <b>{self._title}</b> · {done} of {len(self.model.jobs)}")
        if job.status is JobStatus.DOWNLOADING:
            self.table.scrollTo(self.model.index(i, 0))

    def _on_finished(self, r: OpResult) -> None:
        self.cancel_btn.setEnabled(False)
        self.retry_btn.setEnabled(any(
            j.status in (JobStatus.FAILED, JobStatus.CANCELLED) for j in self.model.jobs))
        if r.error:
            self.heading.setText(f"<b>Couldn't read the playlist.</b> {r.error}")
        elif r.folder is not None:
            self.heading.setText(f"<b>{r.folder.name}</b>: {r.folder.total} songs from the folder")
        elif r.kind == CHECK:
            pass  # plan heading already says what's new
        elif r.cancelled:
            self.heading.setText(f"<b>{self._title}</b>: cancelled after {r.downloaded} song(s)")
        elif self.model.jobs:
            msg = f"<b>{self._title}</b>: downloaded {r.downloaded}"
            if r.failed:
                msg += f", {r.failed} failed (hover a row for the reason)"
            if r.unavailable:
                msg += f", {r.unavailable} unavailable on YouTube"
            self.heading.setText(msg)
