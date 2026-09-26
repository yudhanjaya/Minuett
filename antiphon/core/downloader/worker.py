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

from antiphon.core.library.db import DONE, FAILED, LIVE, UNAVAILABLE, Entry, LibraryDB
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
    UNAVAILABLE = "unavailable"   # YouTube refuses it here; no point retrying now
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
    audio_format: str = "opus"     # "opus", "original" (no conversion) or "mp3"
    sleep_min: float = 2.0
    sleep_max: float = 6.0


class Cancelled(Exception):
    pass


class LiveStream(Exception):
    """The entry is (or became) a live stream, which can't be saved as a song."""


LIVE_STATES = frozenset({"is_live", "is_upcoming", "post_live"})

# Errors worth retrying with fresh stream URLs: YouTube intermittently
# answers 403 on long downloads, and rate-limits or times out now and then.
_RETRYABLE = ("HTTP Error 403", "HTTP Error 429", "HTTP Error 5", "timed out",
              "Connection reset", "IncompleteRead", "Remote end closed")
# Errors that mean YouTube won't serve this video to us at all.
_UNAVAILABLE = ("Video unavailable", "This video is not available", "Private video",
                "has been removed", "not available in your country",
                "blocked it in your country", "members-only", "Join this channel",
                "Sign in to confirm your age", "This video has been removed")
UNAVAILABLE_MESSAGE = ("YouTube won't play this video here: it's blocked in your region, "
                       "removed, private or restricted. Update will check it again.")


def classify_error(message: str) -> str:
    """'retry', 'unavailable' or 'failed'."""
    if any(p in message for p in _UNAVAILABLE):
        return "unavailable"
    if any(p in message for p in _RETRYABLE):
        return "retry"
    return "failed"


def youtube_url(entry: Entry) -> str:
    return f"https://www.youtube.com/watch?v={entry.youtube_id}"


# Bitrate for converting a non-Opus source to Opus. YouTube's own Opus streams
# are ~130-160 kbps and are copied untouched; 256 kbps keeps a second lossy
# generation (e.g. AAC -> Opus) comfortably transparent.
OPUS_TRANSCODE_KBPS = 256

AUDIO_FORMATS = ("opus", "original", "mp3")


def normalise_format(value: str | None) -> str:
    """Map a stored preference (including the old "native") onto AUDIO_FORMATS."""
    value = (value or "opus").lower()
    value = {"native": "original", "best": "original"}.get(value, value)
    return value if value in AUDIO_FORMATS else "opus"


def ytdlp_options(folder: Path, prefs: Preferences) -> dict:
    fmt = normalise_format(prefs.audio_format)
    if fmt == "opus":
        # Prefer an Opus stream so it can be copied without re-encoding; only
        # when none exists is the best other stream converted at a high bitrate.
        selector = "bestaudio[acodec=opus]/bestaudio/best"
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "opus",
                   "preferredquality": str(OPUS_TRANSCODE_KBPS)}
    elif fmt == "mp3":
        selector = "bestaudio/best"
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
    else:
        # Keep whatever YouTube serves (Opus -> .opus, AAC -> .m4a), no conversion.
        selector = "bestaudio/best"
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
    opts = {
        **base_options(),
        "format": selector,
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
        # Fetch in 10 MiB ranges: YouTube tends to cut off (403) single long
        # requests for hour-long mixes, but serves chunked ranges reliably.
        "http_chunk_size": 10 * 1024 * 1024,
    }
    if prefs.sleep_max > 0:
        opts["sleep_interval"] = prefs.sleep_min
        opts["max_sleep_interval"] = prefs.sleep_max
    return opts


def tags_from_info(info: dict, fallback_title: str | None) -> dict[str, object]:
    """Prefer YouTube Music's structured metadata; else parse the video title."""
    # yt-dlp's FFmpegMetadata writes the *upload* date; that's not a release
    # year, so clear it unless YouTube Music gave us a real one below.
    tags: dict[str, object] = {"year": None, "genre": None}
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
    # yt-dlp writes YouTube's video category ("Music") as the genre, which says
    # nothing; use a genre tag from the title if there is one, else leave it empty.
    tags["genre"] = (parse_title(info.get("title") or fallback_title or "").genre
                     if not info.get("genre") or info.get("genre") == "Music" else info["genre"])
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
        self.retry_delays: tuple[float, ...] = (5.0, 20.0)  # seconds; one retry per entry

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
        attempts = len(self.retry_delays) + 1
        for attempt in range(attempts):
            outcome, error = self._attempt(i, job, folder, on_update)
            if outcome in ("done", "cancelled", "live"):
                break
            self._cleanup_video_files(folder, job.entry.youtube_id)
            kind = classify_error(error)
            if kind == "retry" and attempt < attempts - 1:
                job.status, job.error = JobStatus.QUEUED, f"Retrying: {error}"
                on_update(i, job)
                if self._cancel.wait(self.retry_delays[attempt]):
                    job.status, job.error = JobStatus.CANCELLED, None
                    break
                continue
            if kind == "unavailable":
                job.status, job.error = JobStatus.UNAVAILABLE, UNAVAILABLE_MESSAGE
                self.db.set_entry(job.entry.id, status=UNAVAILABLE, error=error)
            else:
                job.status, job.error = JobStatus.FAILED, error
                self.db.set_entry(job.entry.id, status=FAILED, error=error)
            self.db.commit()
            break
        on_update(i, job)

    def _attempt(self, i: int, job: Job, folder: Path, on_update) -> tuple[str, str]:
        """One download attempt: ('done'|'cancelled'|'live'|'error', message)."""
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
                # Resolve first, so a live stream is refused before we start
                # recording something that never ends.
                info = ydl.extract_info(self.url_for(job.entry), download=False)
                if info.get("live_status") in LIVE_STATES or info.get("is_live"):
                    raise LiveStream()
                info = ydl.process_ie_result(info, download=True)
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
            return "done", ""
        except LiveStream:
            job.status, job.error = JobStatus.SKIPPED, "Live stream: skipped"
            self.db.set_entry(job.entry.id, status=LIVE, error=None)
            self.db.commit()
            return "live", ""
        except Exception as e:  # noqa: BLE001 - one bad video must not stop the queue
            if self.cancelled or isinstance(e, Cancelled) or isinstance(
                    getattr(e, "exc_info", [None, None])[1], Cancelled):
                job.status = JobStatus.CANCELLED
                self._cleanup_video_files(folder, job.entry.youtube_id)
                return "cancelled", ""
            return "error", str(e).removeprefix("ERROR: ").strip() or type(e).__name__

    @staticmethod
    def _cleanup_video_files(folder: Path, video_id: str | None) -> None:
        """Remove leftovers of one video: partial media, thumbnails, temp files."""
        if not video_id:
            return
        for p in folder.glob(f"*[[]{video_id}[]].*"):
            try:
                p.unlink()
            except OSError:
                pass

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
