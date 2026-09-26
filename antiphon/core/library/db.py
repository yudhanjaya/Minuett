"""SQLite library database: schema and queries."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT, artist TEXT, album TEXT, album_artist TEXT,
  genre TEXT, year INTEGER, track_no INTEGER, disc_no INTEGER,
  duration_ms INTEGER, codec TEXT, bitrate INTEGER,
  youtube_id TEXT, source_playlist TEXT,
  date_added TEXT, play_count INTEGER DEFAULT 0,
  last_played TEXT, rating INTEGER,
  mtime REAL
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);

CREATE INDEX IF NOT EXISTS idx_tracks_youtube_id ON tracks(youtube_id);

CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  source_url TEXT UNIQUE,     -- NULL for local playlists
  youtube_id TEXT,
  folder TEXT,                -- download folder, fixed at import so renames don't split it
  added TEXT,
  last_checked TEXT,          -- last time we fetched the remote listing
  last_updated TEXT           -- last time an update finished downloading
);
-- One row per playlist slot. For YouTube playlists this mirrors the remote
-- listing, including entries not (yet) downloaded, so an update can tell
-- exactly what is new. position is NULL once an entry leaves the remote list.
CREATE TABLE IF NOT EXISTS playlist_entries (
  id INTEGER PRIMARY KEY,
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  position INTEGER,
  youtube_id TEXT,
  title TEXT,
  track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'new',
  error TEXT,
  UNIQUE (playlist_id, youtube_id)
);
CREATE INDEX IF NOT EXISTS idx_entries_playlist ON playlist_entries(playlist_id, position);
"""

# playlist_entries.status values
NEW, DONE, FAILED, UNAVAILABLE, REMOVED = "new", "done", "failed", "unavailable", "removed"
LIVE = "live"  # a live stream in the playlist; never downloaded

# Columns the tag layer owns; scanner upserts exactly these.
TAG_FIELDS = (
    "title", "artist", "album", "album_artist", "genre",
    "year", "track_no", "disc_no",
)


@dataclass
class Track:
    path: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    year: int | None = None
    track_no: int | None = None
    disc_no: int | None = None
    duration_ms: int | None = None
    codec: str | None = None
    bitrate: int | None = None
    youtube_id: str | None = None
    source_playlist: str | None = None
    date_added: str | None = None
    play_count: int = 0
    last_played: str | None = None
    rating: int | None = None
    mtime: float | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Track":
        return cls(**{k: row[k] for k in row.keys()})


TRACK_COLUMNS = tuple(f.name for f in fields(Track) if f.name != "id")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Playlist:
    id: int
    name: str
    source_url: str | None
    youtube_id: str | None
    folder: str | None
    added: str | None
    last_checked: str | None
    last_updated: str | None
    total: int = 0        # entries currently in the remote list
    downloaded: int = 0
    pending: int = 0      # new or failed, i.e. what an update would fetch

    @property
    def is_youtube(self) -> bool:
        return self.source_url is not None


@dataclass
class Entry:
    id: int
    playlist_id: int
    position: int | None
    youtube_id: str | None
    title: str | None
    track_id: int | None
    status: str
    error: str | None


class LibraryDB:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_v1_items = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'playlist_items'").fetchone()
        if version == 1:
            # v1 playlists lacked the YouTube bookkeeping columns.
            for col in ("youtube_id", "folder", "added", "last_checked", "last_updated"):
                try:
                    self.conn.execute(f"ALTER TABLE playlists ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
        self.conn.executescript(SCHEMA)
        if has_v1_items:
            self.conn.execute(
                "INSERT OR IGNORE INTO playlist_entries (playlist_id, position, track_id, status) "
                "SELECT playlist_id, position, track_id, 'done' FROM playlist_items")
            self.conn.execute("DROP TABLE playlist_items")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- tracks -----------------------------------------------------------

    def mtimes(self) -> dict[str, float]:
        """Map path -> stored mtime, so the scanner can skip unchanged files."""
        rows = self.conn.execute("SELECT path, mtime FROM tracks")
        return {r["path"]: r["mtime"] for r in rows}

    def upsert(self, track: Track) -> int:
        """Insert or update by path. Preserves play stats and date_added."""
        data = asdict(track)
        data.pop("id")
        data["date_added"] = data["date_added"] or _now()
        # Fields a rescan must not clobber.
        keep = {"date_added", "play_count", "last_played", "rating",
                "youtube_id", "source_playlist"}
        cols = ", ".join(data)
        params = ", ".join(f":{k}" for k in data)
        updates = ", ".join(
            f"{k} = excluded.{k}" if k not in keep
            else f"{k} = COALESCE({k}, excluded.{k})"
            for k in data if k != "path"
        )
        cur = self.conn.execute(
            f"INSERT INTO tracks ({cols}) VALUES ({params}) "
            f"ON CONFLICT(path) DO UPDATE SET {updates} RETURNING id",
            data,
        )
        track_id = cur.fetchone()[0]
        self.conn.commit()
        return track_id

    def get(self, track_id: int) -> Track | None:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return Track.from_row(row) if row else None

    def get_by_path(self, path: str) -> Track | None:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return Track.from_row(row) if row else None

    def all_tracks(self, order_by: str = "artist, album, disc_no, track_no, title") -> list[Track]:
        rows = self.conn.execute(f"SELECT * FROM tracks ORDER BY {order_by}")
        return [Track.from_row(r) for r in rows]

    def update_fields(self, track_id: int, changes: dict[str, object]) -> None:
        bad = set(changes) - set(TRACK_COLUMNS)
        if bad:
            raise ValueError(f"unknown track columns: {sorted(bad)}")
        if not changes:
            return
        sets = ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(
            f"UPDATE tracks SET {sets} WHERE id = ?", (*changes.values(), track_id))
        self.conn.commit()

    def remove_paths(self, paths: list[str]) -> None:
        self.conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in paths])
        self.conn.commit()

    def record_play(self, track_id: int) -> None:
        self.conn.execute(
            "UPDATE tracks SET play_count = play_count + 1, last_played = ? WHERE id = ?",
            (_now(), track_id))
        self.conn.commit()

    def find_by_youtube_id(self, youtube_id: str) -> Track | None:
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE youtube_id = ? ORDER BY id LIMIT 1", (youtube_id,)).fetchone()
        return Track.from_row(row) if row else None

    # --- playlists --------------------------------------------------------

    _PLAYLIST_SELECT = """
        SELECT p.*,
          COUNT(e.id) FILTER (WHERE e.position IS NOT NULL) AS total,
          COUNT(e.id) FILTER (WHERE e.position IS NOT NULL AND e.status = 'done') AS downloaded,
          COUNT(e.id) FILTER (WHERE e.position IS NOT NULL AND e.status IN ('new', 'failed')) AS pending
        FROM playlists p LEFT JOIN playlist_entries e ON e.playlist_id = p.id
    """

    def playlists(self) -> list[Playlist]:
        rows = self.conn.execute(
            self._PLAYLIST_SELECT + " GROUP BY p.id ORDER BY p.name COLLATE NOCASE")
        return [Playlist(**dict(r)) for r in rows]

    def get_playlist(self, playlist_id: int) -> Playlist | None:
        row = self.conn.execute(
            self._PLAYLIST_SELECT + " WHERE p.id = ? GROUP BY p.id", (playlist_id,)).fetchone()
        return Playlist(**dict(row)) if row else None

    def playlist_by_url(self, url: str) -> Playlist | None:
        row = self.conn.execute("SELECT id FROM playlists WHERE source_url = ?", (url,)).fetchone()
        return self.get_playlist(row[0]) if row else None

    def add_youtube_playlist(self, name: str, url: str, youtube_id: str | None, folder: str) -> int:
        existing = self.playlist_by_url(url)
        if existing:
            return existing.id
        pid = self.conn.execute(
            "INSERT INTO playlists (name, source_url, youtube_id, folder, added) "
            "VALUES (?, ?, ?, ?, ?)", (name, url, youtube_id, folder, _now())).lastrowid
        self.conn.commit()
        return pid

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        """Rename, carrying the Playlist column of tracks imported from it."""
        old = self.get_playlist(playlist_id)
        if old is None or old.name == name:
            return
        with self.conn:
            self.conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
            self.conn.execute(
                "UPDATE tracks SET source_playlist = ? WHERE source_playlist = ? AND id IN "
                "(SELECT track_id FROM playlist_entries WHERE playlist_id = ?)",
                (name, old.name, playlist_id))

    def touch_playlist(self, playlist_id: int, column: str) -> None:
        if column not in ("last_checked", "last_updated"):
            raise ValueError(column)
        self.conn.execute(f"UPDATE playlists SET {column} = ? WHERE id = ?", (_now(), playlist_id))
        self.conn.commit()

    def remove_playlist(self, playlist_id: int) -> None:
        """Forget the playlist. Tracks and files stay in the library."""
        self.conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self.conn.commit()

    def save_playlist(self, name: str, track_ids: list[int]) -> int:
        """Create a local (non-YouTube) playlist."""
        with self.conn:
            pid = self.conn.execute(
                "INSERT INTO playlists (name, added) VALUES (?, ?)", (name, _now())).lastrowid
            self.conn.executemany(
                "INSERT INTO playlist_entries (playlist_id, position, track_id, status) "
                "VALUES (?, ?, ?, 'done')",
                [(pid, i, tid) for i, tid in enumerate(track_ids)])
        return pid

    def entries(self, playlist_id: int, include_removed: bool = False) -> list[Entry]:
        where = "" if include_removed else " AND position IS NOT NULL"
        rows = self.conn.execute(
            f"SELECT * FROM playlist_entries WHERE playlist_id = ?{where} "
            "ORDER BY position IS NULL, position, id", (playlist_id,))
        return [Entry(**dict(r)) for r in rows]

    def upsert_entry(self, playlist_id: int, youtube_id: str, position: int | None,
                     title: str | None) -> Entry:
        self.conn.execute(
            "INSERT INTO playlist_entries (playlist_id, youtube_id, position, title) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(playlist_id, youtube_id) DO UPDATE SET "
            "position = excluded.position, title = COALESCE(excluded.title, title)",
            (playlist_id, youtube_id, position, title))
        row = self.conn.execute(
            "SELECT * FROM playlist_entries WHERE playlist_id = ? AND youtube_id = ?",
            (playlist_id, youtube_id)).fetchone()
        return Entry(**dict(row))

    def set_entry(self, entry_id: int, **changes: object) -> None:
        allowed = {"position", "title", "track_id", "status", "error"}
        if not changes or set(changes) - allowed:
            raise ValueError(sorted(set(changes) - allowed))
        sets = ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(f"UPDATE playlist_entries SET {sets} WHERE id = ?",
                          (*changes.values(), entry_id))

    def commit(self) -> None:
        self.conn.commit()

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Downloaded tracks in the playlist's current order."""
        rows = self.conn.execute(
            "SELECT t.* FROM playlist_entries e JOIN tracks t ON t.id = e.track_id "
            "WHERE e.playlist_id = ? AND e.position IS NOT NULL ORDER BY e.position",
            (playlist_id,))
        return [Track.from_row(r) for r in rows]

    def distinct(self, column: str) -> list[str]:
        """Existing values of a text column, for editor autocompletion."""
        if column not in TRACK_COLUMNS:
            raise ValueError(column)
        rows = self.conn.execute(
            f"SELECT DISTINCT {column} FROM tracks WHERE {column} IS NOT NULL "
            f"AND {column} != '' ORDER BY {column} COLLATE NOCASE")
        return [str(r[0]) for r in rows]
