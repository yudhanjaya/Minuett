"""Import a folder of music as a playlist named after the folder.

Every audio file under the folder (subfolders included) is scanned into the
library and listed in natural order ("2 - x" before "10 - y"). Importing the
same folder again updates that playlist: new files are added, files that
are gone drop out, and songs you added or removed by hand stay as you left
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import DONE, EXCLUDED, LOCAL_PREFIX, REMOVED, LibraryDB
from .scanner import iter_audio_files, scan_file
from .tags import TagError

FOLDER_PREFIX = "folder:"
FILE_PREFIX = "file:"


@dataclass
class FolderImport:
    playlist_id: int
    name: str
    total: int = 0
    added: int = 0
    removed: int = 0
    errors: int = 0


def folder_key(folder: Path) -> str:
    return f"{FOLDER_PREFIX}{folder}"


def _natural(path: Path, root: Path) -> list:
    rel = str(path.relative_to(root)).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", rel)]


def import_folder(db: LibraryDB, folder: str | Path,
                  progress: Callable[[int, int, Path], None] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> FolderImport:
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} isn't a folder (it may have been moved or deleted).")
    files = sorted(iter_audio_files(root), key=lambda p: _natural(p, root))
    known = db.mtimes()
    pid = db.add_imported_playlist(root.name or str(root), folder_key(root), None, str(root), "folder")
    result = FolderImport(pid, root.name or str(root))
    before = {e.item_id: e for e in db.entries(pid, include_removed=True)}
    seen: set[str] = set()
    pos = 0
    for i, path in enumerate(files):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(files), path)
        key = str(path)
        try:
            existing = db.get_by_path(key)
            if existing is not None and known.get(key) == path.stat().st_mtime:
                tid = existing.id
            else:
                tid = scan_file(db, path)
        except (TagError, OSError):
            result.errors += 1
            continue
        item = f"{FILE_PREFIX}{key}"
        seen.add(item)
        track = db.get(tid)
        entry = db.upsert_entry(pid, item, pos, track.title if track else path.stem)
        pos += 1
        if entry.status == EXCLUDED:
            continue
        if entry.status != DONE or entry.track_id != tid:
            if item not in before or before[item].status != DONE:
                result.added += 1
            db.set_entry(entry.id, track_id=tid, status=DONE, error=None)
        if track and not track.source:
            db.update_fields(tid, {"source": "folder", "source_playlist": track.source_playlist
                                   or result.name})
    for item, e in before.items():
        if (item or "").startswith(LOCAL_PREFIX):
            continue
        if item not in seen and e.position is not None:
            db.set_entry(e.id, position=None, status=REMOVED)
            result.removed += 1
    local = sorted((e for e in before.values() if (e.item_id or "").startswith(LOCAL_PREFIX)
                    and e.position is not None), key=lambda e: e.position)
    for j, e in enumerate(local):
        db.set_entry(e.id, position=pos + j)
    db.commit()
    db.touch_playlist(pid, "last_checked")
    db.touch_playlist(pid, "last_updated")
    result.total = pos
    return result
