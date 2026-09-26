"""One-off tidy-ups for tracks downloaded before a naming rule existed."""

from __future__ import annotations

from dataclasses import dataclass

from antiphon.core.downloader.naming import split_genre_tag

from .db import LibraryDB
from .editor import EditResult, edit_tracks

# Genres that carry no information (YouTube's video category).
_PLACEHOLDER_GENRES = {None, "", "Music"}


@dataclass
class GenreFix:
    track_id: int
    old_title: str
    title: str
    genre: str


def find_genre_tag_fixes(db: LibraryDB) -> list[GenreFix]:
    """Downloaded tracks whose title ends in a genre tag like "[lofi hip hop]"
    and whose genre field is empty or just YouTube's "Music"."""
    fixes = []
    for t in db.all_tracks():
        if not t.youtube_id or not t.title or t.genre not in _PLACEHOLDER_GENRES:
            continue
        title, genre = split_genre_tag(t.title)
        if genre and title:
            fixes.append(GenreFix(t.id, t.title, title, genre))
    return fixes


def apply_genre_tag_fixes(db: LibraryDB, fixes: list[GenreFix]) -> EditResult:
    """Write each fix to its file and the library (file first, as always)."""
    total = EditResult()
    for f in fixes:
        r = edit_tracks(db, [f.track_id], {"title": f.title, "genre": f.genre})
        total.updated += r.updated
        total.failed.update(r.failed)
    return total
