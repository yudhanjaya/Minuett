"""SQLite library database: schema and queries."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

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

CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  source_url TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS playlist_items (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  PRIMARY KEY (playlist_id, position)
);
"""

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


class LibraryDB:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
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

    # --- playlists --------------------------------------------------------

    def save_playlist(self, name: str, track_ids: list[int], source_url: str | None = None) -> int:
        """Create or replace a playlist. Keyed by source_url when given."""
        with self.conn:
            if source_url:
                row = self.conn.execute(
                    "SELECT id FROM playlists WHERE source_url = ?", (source_url,)).fetchone()
            else:
                row = None
            if row:
                pid = row[0]
                self.conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, pid))
                self.conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
            else:
                pid = self.conn.execute(
                    "INSERT INTO playlists (name, source_url) VALUES (?, ?)",
                    (name, source_url)).lastrowid
            self.conn.executemany(
                "INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
                [(pid, i, tid) for i, tid in enumerate(track_ids)])
        return pid

    def playlists(self) -> list[tuple[int, str]]:
        return [(r["id"], r["name"]) for r in
                self.conn.execute("SELECT id, name FROM playlists ORDER BY name")]

    def playlist_tracks(self, playlist_id: int) -> list[Track]:
        rows = self.conn.execute(
            "SELECT t.* FROM playlist_items p JOIN tracks t ON t.id = p.track_id "
            "WHERE p.playlist_id = ? ORDER BY p.position", (playlist_id,))
        return [Track.from_row(r) for r in rows]
