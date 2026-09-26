"""Edit track metadata: write the file first, update the database only on success.

This ordering is the invariant that keeps file and library from drifting:
if mutagen fails (read-only file, corrupt tags, unsupported format) the
database is left untouched and the failure is reported per track.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

from .db import TAG_FIELDS, LibraryDB, Track
from .tags import EASY_KEYS, Cover, TagError, write_cover, write_tags

INT_FIELDS = ("year", "track_no", "disc_no")

# Sentinel for "leave cover art alone" (None means remove it).
KEEP = object()


@dataclass
class EditResult:
    updated: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)  # track id -> message

    @property
    def ok(self) -> bool:
        return not self.failed


def normalise(changes: dict[str, object]) -> dict[str, object]:
    """Validate and coerce user input: strip text, "" -> None, ints parsed."""
    out: dict[str, object] = {}
    for key, value in changes.items():
        if key not in EASY_KEYS:
            raise ValueError(f"not an editable tag field: {key}")
        if isinstance(value, str):
            value = value.strip() or None
        if key in INT_FIELDS and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key.replace('_', ' ')} must be a whole number") from None
            if value < 0:
                raise ValueError(f"{key.replace('_', ' ')} can't be negative")
        out[key] = value
    return out


def common_values(tracks: Iterable[Track]) -> dict[str, tuple[bool, object]]:
    """For the batch editor: field -> (all_same, value_if_same)."""
    tracks = list(tracks)
    result: dict[str, tuple[bool, object]] = {}
    for f in TAG_FIELDS:
        values = {getattr(t, f) for t in tracks}
        same = len(values) == 1
        result[f] = (same, next(iter(values)) if same else None)
    return result


def edit_tracks(
    db: LibraryDB,
    track_ids: Iterable[int],
    changes: dict[str, object],
    cover: object = KEEP,
) -> EditResult:
    """Apply ``changes`` (only the fields the user touched) to each track.

    ``cover`` is KEEP (default), ``None`` to remove art, or a :class:`Cover`.
    Raises ValueError for invalid input before touching any file.
    """
    changes = normalise(changes)
    if cover is not KEEP and cover is not None and not isinstance(cover, Cover):
        raise TypeError("cover must be KEEP, None or a Cover")
    result = EditResult()
    for tid in track_ids:
        track = db.get(tid)
        if track is None:
            result.failed[tid] = "track no longer in library"
            continue
        try:
            if changes:
                write_tags(track.path, changes)
            if cover is not KEEP:
                write_cover(track.path, cover)  # type: ignore[arg-type]
            mtime = os.stat(track.path).st_mtime
        except (TagError, OSError) as e:
            result.failed[tid] = str(e)
            continue
        # File write succeeded: now the database. Record the new mtime so the
        # next rescan doesn't re-read a file we already know about.
        db.update_fields(tid, {**changes, "mtime": mtime})
        result.updated.append(tid)
    return result
