"""Walk music folders, read tags, and upsert into the library.

Unchanged files (same mtime as stored) are skipped, so rescans are cheap.
Files that vanished from a scanned root are removed from the database.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .db import LibraryDB, Track
from .tags import TagError, is_audio, read_tags

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    added_or_updated: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def iter_audio_files(root: str | Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not name.startswith(".") and is_audio(name):
                yield Path(dirpath, name)


def track_from_file(path: Path) -> Track:
    info = read_tags(path)
    t = Track(
        path=str(path),
        duration_ms=info.duration_ms,
        codec=info.codec,
        bitrate=info.bitrate,
        mtime=path.stat().st_mtime,
        **info.tags,  # type: ignore[arg-type]
    )
    if not t.title:
        t.title = path.stem
    return t


def scan_file(db: LibraryDB, path: str | Path) -> int:
    """Read one file and upsert it. Returns the track id. Raises TagError."""
    return db.upsert(track_from_file(Path(path).resolve()))


def scan(
    db: LibraryDB,
    roots: Iterable[str | Path],
    progress: Callable[[int, Path], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanResult:
    result = ScanResult()
    known = db.mtimes()
    seen: set[str] = set()
    root_prefixes = []

    for root in roots:
        root = Path(root).expanduser().resolve()
        root_prefixes.append(str(root) + os.sep)
        for i, path in enumerate(iter_audio_files(root)):
            if should_stop and should_stop():
                return result
            key = str(path)
            seen.add(key)
            if progress:
                progress(i, path)
            try:
                mtime = path.stat().st_mtime
            except OSError as e:
                result.errors.append(f"{path}: {e}")
                continue
            if known.get(key) == mtime:
                result.unchanged += 1
                continue
            try:
                scan_file(db, path)
                result.added_or_updated += 1
            except (TagError, OSError) as e:
                log.warning("scan: %s", e)
                result.errors.append(str(e))

    gone = [p for p in known
            if p not in seen and any(p.startswith(r) for r in root_prefixes)]
    if gone:
        db.remove_paths(gone)
        result.removed = len(gone)
    return result
