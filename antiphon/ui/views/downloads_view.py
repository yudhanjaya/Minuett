"""Live view of the running import/update: one row per song, with progress."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStyle,
    QStyledItemDelegate, QStyleOptionProgressBar, QTableView, QVBoxLayout, QWidget,
)

from antiphon.core.downloader.worker import Job, JobStatus
from antiphon.core.library.db import LibraryDB
from antiphon.ui.download_manager import CHECK, IMPORT, RETRY, DownloadManager, OpResult

COLS = ["#", "Title", "Status", "Progress"]
STATUS_TEXT = {
    JobStatus.QUEUED: "Queued", JobStatus.DOWNLOADING: "Downloading",
    JobStatus.CONVERTING: "Converting", JobStatus.TAGGING: "Tagging",
    JobStatus.DONE: "Done", JobStatus.SKIPPED: "Skipped",
    JobStatus.FAILED: "Failed", JobStatus.CANCELLED: "Cancelled",
    JobStatus.UNAVAILABLE: "Unavailable",
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
        if role == self.ProgressRole:
            return job
        return None


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        job: Job | None = index.data(JobsModel.ProgressRole)
        if job is None or index.column() != 3:
            return super().paint(painter, option, index)
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(2, 3, -2, -3)
        bar.minimum, bar.maximum = 0, 100
        if job.status in (JobStatus.CONVERTING, JobStatus.TAGGING, JobStatus.DONE):
            bar.progress = 100
        elif job.status is JobStatus.DOWNLOADING:
            bar.progress = int(job.progress * 100)
        else:
            bar.progress = 0
        bar.textVisible = job.status is JobStatus.DOWNLOADING
        bar.text = f"{bar.progress}%"
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter)


class DownloadsView(QWidget):
    def __init__(self, db: LibraryDB, manager: DownloadManager, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.manager = manager
        self._title = ""

        self.heading = QLabel("No downloads yet. Import or update a playlist from Playlists.")
        self.heading.setWordWrap(True)
        self.cancel_btn = QPushButton("Cancel", clicked=manager.cancel, enabled=False)
        self.retry_btn = QPushButton("Retry Failed", clicked=manager.retry_failed, enabled=False)
        top = QHBoxLayout()
        top.addWidget(self.heading, 1)
        top.addWidget(self.retry_btn)
        top.addWidget(self.cancel_btn)

        self.model = JobsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 44)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.table)

        manager.op_started.connect(self._on_started)
        manager.plan_ready.connect(self._on_plan)
        manager.job_updated.connect(self._on_job)
        manager.op_finished.connect(self._on_finished)

    def _on_started(self, op) -> None:
        self.cancel_btn.setEnabled(op.kind != CHECK)
        self.retry_btn.setEnabled(False)
        if op.kind == IMPORT:
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
        self.heading.setText(f"Updating <b>{self._title}</b> — {done} of {len(self.model.jobs)}")
        if job.status is JobStatus.DOWNLOADING:
            self.table.scrollTo(self.model.index(i, 0))

    def _on_finished(self, r: OpResult) -> None:
        self.cancel_btn.setEnabled(False)
        self.retry_btn.setEnabled(any(
            j.status in (JobStatus.FAILED, JobStatus.CANCELLED) for j in self.model.jobs))
        if r.error:
            self.heading.setText(f"<b>Couldn't read the playlist.</b> {r.error}")
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
