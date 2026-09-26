"""Queue of playlist operations, run one at a time on a worker QThread.

Operations are per playlist, never "all playlists":
  import  - new URL: fetch listing, create the playlist, download everything
  check   - fetch listing and reconcile only (shows how many songs are new)
  update  - check, then download just the new/failed entries
  retry   - rerun the failed rows of the last job list, without re-fetching

The worker thread opens its own LibraryDB connection (sqlite connections are
per-thread; WAL lets the UI's connection see its writes).
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from antiphon.core.downloader.naming import safe_filename
from antiphon.core.downloader.playlist import ListingError, SyncPlan, fetch_listing, reconcile
from antiphon.core.downloader.worker import (
    Downloader, Job, JobStatus, Preferences, jobs_for, retry_failed,
)
from antiphon.core.library.db import LibraryDB

IMPORT, CHECK, UPDATE, RETRY = "import", "check", "update", "retry"


@dataclass
class Operation:
    kind: str
    playlist_id: int | None = None
    url: str | None = None


@dataclass
class OpResult:
    kind: str
    playlist_id: int | None
    plan: SyncPlan | None = None
    downloaded: int = 0
    failed: int = 0
    unavailable: int = 0
    cancelled: bool = False
    error: str | None = None


class _Worker(QObject):
    """Lives on the worker thread; executes one Operation per call."""

    op_started = Signal(object)          # Operation
    plan_ready = Signal(int, object, list)  # playlist id, SyncPlan, [Job]
    job_updated = Signal(int, object)    # index, Job (copy)
    op_finished = Signal(object)         # OpResult

    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.db_path = db_path
        self.db: LibraryDB | None = None
        self.downloader: Downloader | None = None
        self.last_jobs: list[Job] = []
        self.last_playlist: int | None = None
        self.prefs: Preferences | None = None

    @Slot(object, object)
    def execute(self, op: Operation, prefs: Preferences) -> None:
        if self.db is None:
            self.db = LibraryDB(self.db_path)
        self.prefs = prefs
        self.op_started.emit(op)
        result = OpResult(op.kind, op.playlist_id)
        try:
            if op.kind == RETRY:
                jobs = retry_failed(self.last_jobs)
                result.playlist_id = self.last_playlist
            else:
                pid, plan = self._sync(op, result)
                result.playlist_id, result.plan = pid, plan
                jobs = jobs_for(plan.to_download)
                self.last_jobs, self.last_playlist = jobs, pid
                self.plan_ready.emit(pid, plan, [dataclasses.replace(j) for j in jobs])
                if op.kind == CHECK:
                    self.op_finished.emit(result)
                    return
            if result.playlist_id is not None and jobs:
                self.downloader = Downloader(self.db, result.playlist_id, prefs)
                self.downloader.run(
                    jobs, lambda i, j: self.job_updated.emit(i, dataclasses.replace(j)))
                result.cancelled = self.downloader.cancelled
            result.downloaded = sum(j.status is JobStatus.DONE for j in jobs)
            result.failed = sum(j.status is JobStatus.FAILED for j in jobs)
            result.unavailable = sum(j.status is JobStatus.UNAVAILABLE for j in jobs)
        except ListingError as e:
            result.error = str(e)
        except Exception as e:  # noqa: BLE001 - surface anything to the user
            result.error = f"{type(e).__name__}: {e}"
        finally:
            self.downloader = None
        self.op_finished.emit(result)

    def _sync(self, op: Operation, result: OpResult) -> tuple[int, SyncPlan]:
        assert self.db is not None and self.prefs is not None
        if op.kind == IMPORT:
            listing = fetch_listing(op.url or "")
            existing = self.db.playlist_by_url(listing.url)
            if existing:
                pid = existing.id
            else:
                folder = self.prefs.music_root / safe_filename(listing.title)
                pid = self.db.add_youtube_playlist(
                    listing.title, listing.url, listing.playlist_id, str(folder))
        else:
            pl = self.db.get_playlist(op.playlist_id or -1)
            if pl is None or not pl.source_url:
                raise ListingError("That playlist no longer exists or isn't from YouTube.")
            pid = pl.id
            listing = fetch_listing(pl.source_url)
        return pid, reconcile(self.db, pid, listing)

    def cancel(self) -> None:
        # Called from the UI thread; Downloader.cancel is a threading.Event.
        if self.downloader is not None:
            self.downloader.cancel()


class DownloadManager(QObject):
    """UI-side facade: queue operations, relay progress."""

    _execute = Signal(object, object)

    queue_changed = Signal()             # busy/queued state changed
    op_started = Signal(object)
    plan_ready = Signal(int, object, list)
    job_updated = Signal(int, object)
    op_finished = Signal(object)

    def __init__(self, db_path: str, prefs_provider, parent=None) -> None:
        super().__init__(parent)
        self._prefs_provider = prefs_provider  # () -> Preferences, read per op
        self._queue: deque[Operation] = deque()
        self.current: Operation | None = None

        self._thread = QThread(self)
        self._worker = _Worker(db_path)
        self._worker.moveToThread(self._thread)
        self._execute.connect(self._worker.execute)
        self._worker.op_started.connect(self.op_started)
        self._worker.plan_ready.connect(self.plan_ready)
        self._worker.job_updated.connect(self.job_updated)
        self._worker.op_finished.connect(self._on_finished)
        self._thread.start()

    # --- queueing ---------------------------------------------------------

    def submit(self, op: Operation) -> bool:
        """Queue an operation. Returns False if it's already queued/running."""
        if self.is_busy(op.playlist_id, op.url) and op.kind != RETRY:
            return False
        self._queue.append(op)
        self.queue_changed.emit()
        self._pump()
        return True

    def import_url(self, url: str) -> bool:
        return self.submit(Operation(IMPORT, url=url))

    def check(self, playlist_id: int) -> bool:
        return self.submit(Operation(CHECK, playlist_id))

    def update(self, playlist_id: int) -> bool:
        return self.submit(Operation(UPDATE, playlist_id))

    def retry_failed(self) -> bool:
        if self.current is not None:
            return False
        return self.submit(Operation(RETRY))

    def cancel(self) -> None:
        self._worker.cancel()

    def state_of(self, playlist_id: int) -> str | None:
        """'checking' / 'updating' / 'queued' / None, for the playlists view."""
        if self.current and self.current.playlist_id == playlist_id:
            return "checking" if self.current.kind == CHECK else "updating"
        if any(op.playlist_id == playlist_id for op in self._queue):
            return "queued"
        return None

    def is_busy(self, playlist_id: int | None = None, url: str | None = None) -> bool:
        ops = ([self.current] if self.current else []) + list(self._queue)
        return any((playlist_id is not None and op.playlist_id == playlist_id)
                   or (url is not None and op.url == url) for op in ops)

    @property
    def running(self) -> bool:
        return self.current is not None

    def _pump(self) -> None:
        if self.current is None and self._queue:
            self.current = self._queue.popleft()
            self.queue_changed.emit()
            self._execute.emit(self.current, self._prefs_provider())

    def _on_finished(self, result: OpResult) -> None:
        self.current = None
        self.op_finished.emit(result)
        self.queue_changed.emit()
        self._pump()

    def shutdown(self) -> None:
        self._queue.clear()
        self.cancel()
        self._thread.quit()
        self._thread.wait(5000)


def default_music_root() -> Path:
    """<the desktop's Music folder>/Antiphon (localised names like ~/Musik work too)."""
    from PySide6.QtCore import QStandardPaths
    music = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)
    return (Path(music) if music else Path.home() / "Music") / "Antiphon"
