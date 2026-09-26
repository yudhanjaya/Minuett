"""'Arrange by' browse trees over the library. Pure Python, no Qt.

An arrangement is a list of levels; each level maps a track to a group label.
The UI shows the resulting tree and filters the table by the selected node's
path (e.g. ("Daft Punk", "Discovery")).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .db import Track

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN = "Unknown"
NO_PLAYLIST = "Not from a playlist"

Level = Callable[[Track], str]


def _artist(t: Track) -> str:
    return (t.artist or "").strip() or UNKNOWN_ARTIST


def _album_artist(t: Track) -> str:
    return (t.album_artist or t.artist or "").strip() or UNKNOWN_ARTIST


def _album(t: Track) -> str:
    return (t.album or "").strip() or UNKNOWN_ALBUM


def _genre(t: Track) -> str:
    return (t.genre or "").strip() or UNKNOWN


def _decade(t: Track) -> str:
    return f"{t.year // 10 * 10}s" if t.year else UNKNOWN


def _year(t: Track) -> str:
    return str(t.year) if t.year else UNKNOWN


def _playlist(t: Track) -> str:
    return (t.source_playlist or "").strip() or NO_PLAYLIST


_SOURCE_NAMES = {"youtube": "YouTube", "youtube-music": "YouTube Music", "spotify": "Spotify",
                 "pandora": "Pandora", "apple-music": "Apple Music", "other": "Imported"}


def _source(t: Track) -> str:
    return _SOURCE_NAMES.get(t.source or "", "Local files")


def _month_added(t: Track) -> str:
    return (t.date_added or "")[:7] or UNKNOWN


@dataclass(frozen=True)
class Arrangement:
    name: str
    levels: tuple[Level, ...]
    newest_first: bool = False  # for years and dates


ARRANGEMENTS: dict[str, Arrangement] = {a.name: a for a in (
    Arrangement("Playlist", (_playlist, _artist)),
    Arrangement("Artist / Album", (_artist, _album)),
    Arrangement("Album Artist", (_album_artist, _album)),
    Arrangement("Album", (_album,)),
    Arrangement("Genre", (_genre, _artist)),
    Arrangement("Year", (_decade, _year), newest_first=True),
    Arrangement("Date Added", (_month_added,), newest_first=True),
    Arrangement("Source", (_source, _playlist)),
)}
DEFAULT_ARRANGEMENT = "Playlist"
_UNKNOWNS = {UNKNOWN_ARTIST, UNKNOWN_ALBUM, UNKNOWN, NO_PLAYLIST, "Local files"}


@dataclass
class Node:
    label: str
    path: tuple[str, ...]
    count: int = 0
    children: list["Node"] = field(default_factory=list)


def _sort_key(label: str, newest_first: bool):
    # Unknown/ungrouped buckets always sink to the bottom.
    unknown = label in _UNKNOWNS
    if newest_first:
        return (unknown, [-ord(c) for c in label])
    return (unknown, label.casefold())


def build_tree(tracks: list[Track], arrangement: str) -> list[Node]:
    arr = ARRANGEMENTS[arrangement]

    def build(subset: list[Track], depth: int, prefix: tuple[str, ...]) -> list[Node]:
        if depth == len(arr.levels):
            return []
        groups: dict[str, list[Track]] = {}
        for t in subset:
            groups.setdefault(arr.levels[depth](t), []).append(t)
        nodes = []
        for label in sorted(groups, key=lambda s: _sort_key(s, arr.newest_first)):
            path = (*prefix, label)
            children = build(groups[label], depth + 1, path)
            nodes.append(Node(label, path, len(groups[label]), children))
        return nodes

    return build(tracks, 0, ())


def matcher(arrangement: str, path: tuple[str, ...]) -> Callable[[Track], bool] | None:
    """Predicate for tracks under ``path``; None means everything."""
    if not path:
        return None
    levels = ARRANGEMENTS[arrangement].levels[:len(path)]
    return lambda t: all(level(t) == want for level, want in zip(levels, path))
