"""Phase 1 of an import/update: fetch the playlist listing and reconcile it.

``fetch_listing`` uses yt-dlp's flat extraction, which returns every entry's
ID, title and position in a second or two without touching any media.

``reconcile`` compares that listing with what the library already knows
about the playlist and works out the minimum to download:

* entries already downloaded for this playlist are kept (order refreshed);
* entries whose video is already in the library, e.g. from another playlist,
  are linked to the existing track instead of downloaded twice;
* deleted/private videos are marked unavailable;
* live streams (24/7 "radio" streams and the like) are skipped;
* entries that vanished from YouTube are marked removed. Their files stay in
  the library; they just drop out of the playlist's order;
* everything else is "new" and goes into the download queue.

Our database is the record of what's been fetched, not yt-dlp's
``download_archive``: the archive is keyed only by video ID, so a video that
appears in two playlists would be skipped for the second one instead of
being linked into it.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from antiphon.core.library.db import (
    DONE, FAILED, LIVE, NEW, REMOVED, UNAVAILABLE, Entry, LibraryDB,
)

# Flat extraction reports these titles for entries you can't download.
_UNAVAILABLE_TITLES = {"[deleted video]", "[private video]", "[unavailable video]"}
_LIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class ListingError(Exception):
    pass


@dataclass
class RemoteEntry:
    """One slot in a source listing.

    ``item_id`` is the source's key (a YouTube video id, a Spotify track URI,
    an ISRC or artist|title for file imports). For YouTube it's also the video
    to download; other sources set ``needs_match`` and are matched on YouTube
    Music at download time using title/artist/album/duration.
    """
    position: int
    item_id: str
    title: str | None
    uploader: str | None = None
    duration: float | None = None      # seconds
    live_status: str | None = None
    artist: str | None = None
    album: str | None = None
    isrc: str | None = None
    url: str | None = None             # the item's page on its service
    needs_match: bool = False

    @property
    def youtube_id(self) -> str | None:
        return None if self.needs_match else self.item_id

    @property
    def video_id(self) -> str:  # older name, used by YouTube-only code and tests
        return self.item_id

    @property
    def is_live(self) -> bool:
        return self.live_status in ("is_live", "is_upcoming", "post_live")

    @property
    def available(self) -> bool:
        return (self.title or "").strip().lower() not in _UNAVAILABLE_TITLES


@dataclass
class Listing:
    url: str
    playlist_id: str | None
    title: str
    uploader: str | None
    entries: list[RemoteEntry]
    source: str = "youtube"            # youtube, youtube-music, spotify, pandora, other
    truncated: bool = False            # the source only showed part of the list


@dataclass
class SyncPlan:
    playlist_id: int
    to_download: list[Entry] = field(default_factory=list)
    linked: int = 0          # already in library via another playlist
    kept: int = 0            # already downloaded for this playlist
    unavailable: int = 0
    live: int = 0            # live streams, which are skipped
    removed: int = 0
    renamed_from: str | None = None
    truncated: bool = False


def normalise_url(url: str) -> str:
    """Canonical playlist URL, so the same list pasted two ways is one playlist.

    ``watch?v=…&list=PL…`` becomes ``playlist?list=PL…``; anything we don't
    recognise is returned trimmed.
    """
    url = url.strip()
    parsed = urlparse(url)
    if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
        list_id = parse_qs(parsed.query).get("list", [None])[0]
        if list_id and _LIST_ID.match(list_id):
            return f"https://www.youtube.com/playlist?list={list_id}"
    return url


def js_runtimes() -> dict[str, dict]:
    """JavaScript runtimes yt-dlp may use for YouTube. It enables only Deno
    by default; we offer whichever supported runtimes are installed."""
    found = {name: {} for name in ("deno", "node", "quickjs", "bun") if shutil.which(name)}
    if not found and shutil.which("qjs"):
        found["quickjs"] = {"path": shutil.which("qjs")}
    return found


def base_options() -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True}
    runtimes = js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def source_of_url(url: str) -> str | None:
    """'youtube', 'youtube-music', 'spotify', or None if we can't read it."""
    host = urlparse(url.strip()).netloc.lower()
    if url.strip().startswith("spotify:") or host.endswith("spotify.com"):
        return "spotify"
    if host == "music.youtube.com":
        return "youtube-music"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return None


def fetch_listing(url: str) -> Listing:
    """Read a playlist's track list. Network call; run it off the UI thread."""
    source = source_of_url(url)
    if source == "spotify":
        from .sources.spotify import fetch_spotify_listing
        return fetch_spotify_listing(url)
    if url.startswith("import:"):
        raise ListingError("This playlist was imported from a file; import a newer export to update it.")
    import yt_dlp  # imported lazily: slow to import, and optional for tests

    opts = {**base_options(), "extract_flat": "in_playlist", "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(normalise_url(url), download=False)
    except yt_dlp.utils.DownloadError as e:
        raise ListingError(str(e).removeprefix("ERROR: ")) from e
    if not info or info.get("_type") != "playlist":
        raise ListingError("That link isn't a playlist.")
    listing = listing_from_info(url, info)
    listing.source = source or "youtube"
    return listing


def listing_from_info(url: str, info: dict) -> Listing:
    entries = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        entries.append(RemoteEntry(
            position=len(entries),
            item_id=e["id"],
            title=e.get("title"),
            url=f"https://www.youtube.com/watch?v={e['id']}",
            uploader=e.get("uploader") or e.get("channel"),
            duration=e.get("duration"),
            live_status=e.get("live_status"),
        ))
    return Listing(
        url=normalise_url(url),
        playlist_id=info.get("id"),
        title=info.get("title") or "Untitled playlist",
        uploader=info.get("uploader") or info.get("channel"),
        entries=entries,
    )


def reconcile(db: LibraryDB, playlist_id: int, listing: Listing) -> SyncPlan:
    """Bring the stored entries in line with ``listing``; return what to fetch."""
    plan = SyncPlan(playlist_id, truncated=listing.truncated)
    pl = db.get_playlist(playlist_id)
    if pl is None:
        raise ValueError(f"no playlist {playlist_id}")
    if listing.title and listing.title != pl.name:
        plan.renamed_from = pl.name
        db.rename_playlist(playlist_id, listing.title)

    before = {e.item_id: e for e in db.entries(playlist_id, include_removed=True)}
    seen: set[str] = set()
    for remote in listing.entries:
        if remote.item_id in seen:  # duplicate slot in the same playlist
            continue
        seen.add(remote.item_id)
        entry = db.upsert_entry(
            playlist_id, remote.item_id, remote.position,
            remote.title if remote.available else None,
            youtube_id=remote.youtube_id, artist=remote.artist, album=remote.album,
            duration_ms=round(remote.duration * 1000) if remote.duration else None,
            isrc=remote.isrc, source_url=remote.url)

        track = db.get(entry.track_id) if entry.track_id else None
        if track is not None:
            if entry.status != DONE:
                db.set_entry(entry.id, status=DONE, error=None)
            plan.kept += 1
            continue
        existing = (db.find_by_youtube_id(entry.youtube_id) if entry.youtube_id else None) \
            or (db.find_by_source_url(remote.url) if remote.url else None)
        if existing is not None:
            db.set_entry(entry.id, track_id=existing.id, status=DONE, error=None)
            plan.linked += 1
            continue
        if not remote.available:
            db.set_entry(entry.id, status=UNAVAILABLE, track_id=None)
            plan.unavailable += 1
            continue
        if remote.is_live:
            db.set_entry(entry.id, status=LIVE, track_id=None, error=None)
            plan.live += 1
            continue
        if entry.status not in (NEW, FAILED):
            db.set_entry(entry.id, status=NEW, track_id=None, error=None)
            entry.status = NEW
        plan.to_download.append(entry)

    for key, entry in before.items():
        if key not in seen and entry.position is not None:
            db.set_entry(entry.id, position=None, status=REMOVED)
            plan.removed += 1
    db.commit()
    db.touch_playlist(playlist_id, "last_checked")
    # Re-read so callers see fresh positions/titles.
    fresh = {e.id: e for e in db.entries(playlist_id)}
    plan.to_download = [fresh[e.id] for e in plan.to_download]
    return plan
