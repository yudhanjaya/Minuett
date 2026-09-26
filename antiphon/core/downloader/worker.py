"""Phase 2 of an import/update: download entries one at a time.

Qt-free. The UI runs :meth:`Downloader.run` in a QThread and receives
per-job updates through the ``on_update`` callback (which it forwards as a
queued Qt signal). Cancel works by raising inside yt-dlp's progress hook.

Each finished file is tagged (artist/title parsed from the video title
unless YouTube Music supplied real metadata), scanned into the library, and
linked to its playlist entry.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from antiphon.core.library.db import DONE, FAILED, Entry, LibraryDB
from antiphon.core.library.scanner import scan_file
from antiphon.core.library.tags import TagError, write_tags

from .naming import parse_title, safe_filename
from .playlist import base_options

log = logging.getLogger(__name__)


class JobStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    TAGGING = "tagging"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    entry: Entry
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0          # 0..1 for the download stage
    error: str | None = None
    track_id: int | None = None

    @property
    def title(self) -> str:
        return self.entry.title or self.entry.youtube_id or "?"


@dataclass
class Preferences:
    music_root: Path
    audio_format: str = "native"   # "native" keeps Opus/M4A as-is; or "mp3"
    sleep_min: float = 2.0
    sleep_max: float = 6.0


class Cancelled(Exception):
    pass


def youtube_url(entry: Entry) -> str:
    return f"https://www.youtube.com/watch?v={entry.youtube_id}"


def ytdlp_options(folder: Path, prefs: Preferences) -> dict:
    if prefs.audio_format == "mp3":
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
    else:
        # "best" copies the source stream (Opus in WebM -> .opus, AAC -> .m4a)
        # instead of transcoding lossy to lossy.
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
    opts = {
        **base_options(),
        "format": "bestaudio/best",
        "noplaylist": True,
        "paths": {"home": str(folder)},
        # No playlist index in the name: order lives in the database and
        # changes when the playlist is edited on YouTube.
        "outtmpl": "%(title).150B [%(id)s].%(ext)s",
        "writethumbnail": True,
        "postprocessors": [
            extract,
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "EmbedThumbnail"},
        ],
        "retries": 5,
        "fragment_retries": 5,
    }
    if prefs.sleep_max > 0:
        opts["sleep_interval"] = prefs.sleep_min
        opts["max_sleep_interval"] = prefs.sleep_max
    return opts


def tags_from_info(info: dict, fallback_title: str | None) -> dict[str, object]:
    """Prefer YouTube Music's structured metadata; else parse the video title."""
    # yt-dlp's FFmpegMetadata writes the *upload* date; that's not a release
    # year, so clear it unless YouTube Music gave us a real one below.
    tags: dict[str, object] = {"year": None}
    if info.get("track") and (info.get("artist") or info.get("artists")):
        artists = info.get("artists") or [info["artist"]]
        tags["title"] = info["track"]
        tags["artist"] = ", ".join(artists) if isinstance(artists, list) else str(artists)
        if info.get("album"):
            tags["album"] = info["album"]
        if info.get("release_year"):
            tags["year"] = info["release_year"]
    else:
        parsed = parse_title(info.get("title") or fallback_title or "",
                             info.get("uploader") or info.get("channel"))
        tags["title"] = parsed.title
        if parsed.artist:
            tags["artist"] = parsed.artist
    return tags


def _final_path(info: dict) -> Path | None:
    for d in info.get("requested_downloads") or []:
        if d.get("filepath"):
            return Path(d["filepath"])
    return Path(info["filepath"]) if info.get("filepath") else None


class Downloader:
    """Runs jobs for one playlist strictly in sequence."""

    def __init__(
        self,
        db: LibraryDB,
        playlist_id: int,
        prefs: Preferences,
        url_for: Callable[[Entry], str] = youtube_url,
        extra_opts: dict | None = None,
    ) -> None:
        self.db = db
        self.playlist_id = playlist_id
        self.prefs = prefs
        self.url_for = url_for
        self.extra_opts = extra_opts or {}
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def folder(self) -> Path:
        pl = self.db.get_playlist(self.playlist_id)
        assert pl is not None
        return Path(pl.folder) if pl.folder else self.prefs.music_root / safe_filename(pl.name)

    def run(self, jobs: list[Job], on_update: Callable[[int, Job], None]) -> None:
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        for i, job in enumerate(jobs):
            if self.cancelled:
                job.status = JobStatus.CANCELLED
                on_update(i, job)
                continue
            if job.status not in (JobStatus.QUEUED, JobStatus.FAILED):
                continue
            self._run_one(i, job, folder, on_update)
        pl = self.db.get_playlist(self.playlist_id)
        if pl and not self.cancelled:
            self.db.touch_playlist(self.playlist_id, "last_updated")

    def _run_one(self, i: int, job: Job, folder: Path, on_update) -> None:
        import yt_dlp

        job.status, job.progress, job.error = JobStatus.DOWNLOADING, 0.0, None
        on_update(i, job)

        def progress_hook(d: dict) -> None:
            if self.cancelled:
                raise Cancelled()
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    job.progress = min(d.get("downloaded_bytes", 0) / total, 1.0)
                    on_update(i, job)
            elif d.get("status") == "finished":
                job.progress = 1.0
                job.status = JobStatus.CONVERTING
                on_update(i, job)

        def pp_hook(d: dict) -> None:
            if self.cancelled:
                raise Cancelled()

        opts = {**ytdlp_options(folder, self.prefs), **self.extra_opts,
                "progress_hooks": [progress_hook], "postprocessor_hooks": [pp_hook]}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url_for(job.entry), download=True)
                info = ydl.sanitize_info(info)
            path = _final_path(info)
            if path is None or not path.exists():
                raise FileNotFoundError("yt-dlp finished but the audio file is missing")

            job.status = JobStatus.TAGGING
            on_update(i, job)
            try:
                write_tags(path, tags_from_info(info, job.entry.title))
            except TagError as e:
                log.warning("tagging %s: %s", path, e)  # keep the file; tags are fixable later
            track_id = scan_file(self.db, path)
            track = self.db.get(track_id)
            pl = self.db.get_playlist(self.playlist_id)
            self.db.update_fields(track_id, {
                "youtube_id": job.entry.youtube_id,
                "source_playlist": (track.source_playlist if track and track.source_playlist
                                    else pl.name if pl else None),
            })
            self.db.set_entry(job.entry.id, track_id=track_id, status=DONE, error=None)
            self.db.commit()
            job.track_id = track_id
            job.status = JobStatus.DONE
        except Exception as e:  # noqa: BLE001 - one bad video must not stop the queue
            if self.cancelled or isinstance(e, Cancelled) or isinstance(
                    getattr(e, "exc_info", [None, None])[1], Cancelled):
                job.status = JobStatus.CANCELLED
                self._cleanup_partials(folder)
            else:
                msg = str(e).removeprefix("ERROR: ").strip() or type(e).__name__
                job.status, job.error = JobStatus.FAILED, msg
                self.db.set_entry(job.entry.id, status=FAILED, error=msg)
                self.db.commit()
        on_update(i, job)

    @staticmethod
    def _cleanup_partials(folder: Path) -> None:
        for p in folder.glob("*.part"):
            try:
                os.remove(p)
            except OSError:
                pass


def jobs_for(entries: list[Entry]) -> list[Job]:
    return [Job(e) for e in entries]


def retry_failed(jobs: list[Job]) -> list[Job]:
    """Reset failed/cancelled jobs to queued; leave the rest untouched."""
    for j in jobs:
        if j.status in (JobStatus.FAILED, JobStatus.CANCELLED):
            j.status, j.progress, j.error = JobStatus.QUEUED, 0.0, None
    return jobs


__all__ = [
    "Downloader", "Job", "JobStatus", "Preferences", "jobs_for", "retry_failed",
    "tags_from_info", "ytdlp_options",
]
