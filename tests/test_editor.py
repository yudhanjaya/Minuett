import os
import stat

import pytest

from antiphon.core.library.db import LibraryDB
from antiphon.core.library.editor import KEEP, common_values, edit_tracks, normalise
from antiphon.core.library.scanner import scan
from antiphon.core.library.tags import Cover, read_cover, read_tags, sniff_image_mime, write_cover

FORMATS = ["mp3", "opus", "flac", "m4a", "ogg"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def lib(tmp_path, tone):
    for i, ext in enumerate(FORMATS):
        tone(tmp_path / f"{i}.{ext}", title=f"T{i}", artist="Same", album=f"A{i}")
    db = LibraryDB(":memory:")
    scan(db, [tmp_path])
    return db


@pytest.mark.parametrize("ext", FORMATS)
def test_cover_roundtrip(tmp_path, tone, ext):
    f = tone(tmp_path / f"a.{ext}")
    assert read_cover(f) is None
    write_cover(f, Cover(PNG, "image/png"))
    assert read_cover(f) == Cover(PNG, "image/png")
    write_cover(f, Cover(JPEG, "image/jpeg"))  # replaces, doesn't append
    assert read_cover(f) == Cover(JPEG, "image/jpeg")
    write_cover(f, None)
    assert read_cover(f) is None
    assert read_tags(f).duration_ms  # file still decodes


def test_sniff():
    assert sniff_image_mime(PNG) == "image/png"
    assert sniff_image_mime(JPEG) == "image/jpeg"
    with pytest.raises(Exception):
        sniff_image_mime(b"GIF89a")


def test_batch_edit_writes_file_then_db(lib):
    tracks = lib.all_tracks()
    ids = [t.id for t in tracks]
    res = edit_tracks(lib, ids, {"album": "  Compilation ", "year": "2001"})
    assert res.ok and sorted(res.updated) == sorted(ids)
    for t in lib.all_tracks():
        assert (t.album, t.year) == ("Compilation", 2001)
        assert read_tags(t.path).tags["album"] == "Compilation"
        assert t.title.startswith("T")  # untouched field kept per-track
        assert t.mtime == os.stat(t.path).st_mtime
    # Rescan sees nothing to do.
    r = scan(lib, [os.path.dirname(tracks[0].path)])
    assert r.added_or_updated == 0


def test_failed_file_write_leaves_db_alone(lib):
    t = lib.all_tracks()[0]
    os.chmod(t.path, stat.S_IRUSR)
    try:
        if os.access(t.path, os.W_OK):
            pytest.skip("running as root")
        res = edit_tracks(lib, [t.id], {"title": "Changed"})
    finally:
        os.chmod(t.path, stat.S_IRUSR | stat.S_IWUSR)
    assert t.id in res.failed
    assert lib.get(t.id).title == t.title


def test_cover_via_editor(lib):
    ids = [t.id for t in lib.all_tracks()]
    assert edit_tracks(lib, ids, {}, cover=Cover(JPEG, "image/jpeg")).ok
    assert all(read_cover(lib.get(i).path) for i in ids)
    assert edit_tracks(lib, ids, {}, cover=None).ok
    assert not any(read_cover(lib.get(i).path) for i in ids)
    assert edit_tracks(lib, ids, {"genre": "X"}, cover=KEEP).ok


def test_clearing_a_field(lib):
    t = lib.all_tracks()[0]
    assert edit_tracks(lib, [t.id], {"album": ""}).ok
    assert lib.get(t.id).album is None
    assert "album" not in read_tags(t.path).tags


def test_normalise_rejects_bad_input():
    with pytest.raises(ValueError):
        normalise({"year": "nineteen"})
    with pytest.raises(ValueError):
        normalise({"track_no": -1})
    with pytest.raises(ValueError):
        normalise({"path": "/etc/passwd"})


def test_common_values(lib):
    cv = common_values(lib.all_tracks())
    assert cv["artist"] == (True, "Same")
    assert cv["album"][0] is False
