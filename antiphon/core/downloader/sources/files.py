"""Read playlists exported from other services as CSV or text files.

Handles the common exporters by their column names rather than by tool:
Exportify (Spotify), TuneMyMusic and Soundiiz (Pandora, Spotify, Apple
Music and others), and plain text lists of "Artist - Title" lines. Each
track becomes an entry to match on YouTube Music.

Entries are keyed by the service's track link/URI when the export has one,
else by ISRC, else by artist+title. So importing a newer export of the same
playlist updates it: new songs are added and removed ones drop out.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from ..naming import parse_title
from ..playlist import Listing, ListingError, RemoteEntry

SOURCES = {"spotify": "Spotify", "pandora": "Pandora", "apple-music": "Apple Music",
           "other": "Other service"}

# Accepted header names (lower-cased) for each field.
_COLUMNS = {
    "title": ("track name", "title", "name", "song", "song name", "track", "track title"),
    "artist": ("artist name(s)", "artist name", "artist", "artists", "artist(s)"),
    "album": ("album name", "album", "album title"),
    "duration_ms": ("track duration (ms)", "duration (ms)", "duration_ms", "duration ms"),
    "duration": ("duration", "length", "time"),
    "isrc": ("isrc",),
    "uri": ("track uri", "spotify uri", "uri", "spotify - id", "spotify id", "track id",
            "url", "link", "track url"),
    "playlist": ("playlist name", "playlist"),
}


@dataclass
class ExportFile:
    name: str | None          # playlist name found in the file, if any
    entries: list[RemoteEntry]


def import_key(source: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "playlist"
    return f"import:{source}:{slug}"


def _duration_seconds(ms: str | None, text: str | None) -> float | None:
    if ms:
        try:
            return float(ms) / 1000
        except ValueError:
            pass
    if text:
        text = text.strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            v = float(text)
            return v / 1000 if v > 10_000 else v   # ms or seconds
        parts = text.split(":")
        if all(p.isdigit() for p in parts) and 2 <= len(parts) <= 3:
            secs = 0
            for p in parts:
                secs = secs * 60 + int(p)
            return float(secs)
    return None


def _track_url(uri: str | None, source: str) -> str | None:
    if not uri:
        return None
    uri = uri.strip()
    m = re.match(r"spotify:track:([A-Za-z0-9]{22})$", uri)
    if m:
        return f"https://open.spotify.com/track/{m.group(1)}"
    if re.fullmatch(r"[A-Za-z0-9]{22}", uri) and source == "spotify":
        return f"https://open.spotify.com/track/{uri}"
    return uri if uri.startswith("http") else None


def _entry_key(uri: str | None, isrc: str | None, artist: str | None, title: str) -> str:
    if uri:
        return uri.strip()
    if isrc:
        return f"isrc:{isrc.strip().upper()}"
    return f"{(artist or '').strip().lower()}|{title.strip().lower()}"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ListingError("Couldn't read the file's text encoding.")


def parse_export(path: str | Path, source: str) -> ExportFile:
    path = Path(path)
    text = _read_text(path)
    if not text.strip():
        raise ListingError("The file is empty.")
    first = text.splitlines()[0]
    is_csv = path.suffix.lower() in (".csv", ".tsv") or first.count(",") >= 1 and any(
        h in first.lower() for cols in _COLUMNS.values() for h in cols)
    return _parse_csv(text, source) if is_csv else _parse_lines(text, source)


def _parse_csv(text: str, source: str) -> ExportFile:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}
    col = {f: next((headers[a] for a in aliases if a in headers), None)
           for f, aliases in _COLUMNS.items()}
    if not col["title"]:
        raise ListingError("Couldn't find a track name column. Expected an export from "
                           "Exportify, TuneMyMusic or Soundiiz.")
    entries: list[RemoteEntry] = []
    name = None
    for row in reader:
        get = lambda f: (row.get(col[f]) or "").strip() or None if col[f] else None  # noqa: E731
        title = get("title")
        if not title:
            continue
        name = name or get("playlist")
        artist = get("artist")
        if artist:
            artist = artist.replace(";", ", ")
        uri, isrc = get("uri"), get("isrc")
        entries.append(RemoteEntry(
            position=len(entries),
            item_id=_entry_key(uri, isrc, artist, title),
            title=title, artist=artist, album=get("album"), isrc=isrc,
            duration=_duration_seconds(get("duration_ms"), get("duration")),
            url=_track_url(uri, source), needs_match=True))
    if not entries:
        raise ListingError("The file has no tracks.")
    return ExportFile(name, entries)


def _parse_lines(text: str, source: str) -> ExportFile:
    entries: list[RemoteEntry] = []
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\d+[.)]\s+", "", line)   # "1. Artist - Title"
        parsed = parse_title(line)
        entries.append(RemoteEntry(
            position=len(entries),
            item_id=_entry_key(None, None, parsed.artist, parsed.title),
            title=parsed.title, artist=parsed.artist, needs_match=True))
    if not entries:
        raise ListingError("The file has no tracks.")
    return ExportFile(None, entries)


def listing_from_file(path: str | Path, source: str, name: str) -> Listing:
    export = parse_export(path, source)
    return Listing(url=import_key(source, name), playlist_id=None, title=name, uploader=None,
                   entries=export.entries, source=source)
