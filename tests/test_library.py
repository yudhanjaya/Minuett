import os

from minuett.core.library.db import LibraryDB, Track
from minuett.core.library.scanner import scan


def test_scan_add_skip_update_remove(tmp_path, tone):
    music = tmp_path / "music"
    (music / "sub").mkdir(parents=True)
    a = tone(music / "a.mp3", title="Alpha", artist="X")
    b = tone(music / "sub" / "b.opus", title="Beta", artist="Y")
    (music / "cover.jpg").write_bytes(b"\xff\xd8")  # ignored
    db = LibraryDB(":memory:")

    r = scan(db, [music])
    assert (r.added_or_updated, r.unchanged, r.removed) == (2, 0, 0)
    assert {t.title for t in db.all_tracks()} == {"Alpha", "Beta"}

    r = scan(db, [music])
    assert (r.added_or_updated, r.unchanged) == (0, 2)

    tone(music / "a.mp3", title="Alpha 2", artist="X")
    os.utime(a, (1, 1))  # force a distinct mtime
    r = scan(db, [music])
    assert r.added_or_updated == 1
    assert db.get_by_path(str(a.resolve())).title == "Alpha 2"

    b.unlink()
    r = scan(db, [music])
    assert r.removed == 1
    assert len(db.all_tracks()) == 1


def test_scan_leaves_other_roots_alone(tmp_path, tone):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir(); two.mkdir()
    tone(one / "a.mp3"); tone(two / "b.mp3")
    db = LibraryDB(":memory:")
    scan(db, [one, two])
    r = scan(db, [one])
    assert r.removed == 0 and len(db.all_tracks()) == 2


def test_untagged_file_uses_stem(tmp_path, tone):
    tone(tmp_path / "Some Name.flac")
    db = LibraryDB(":memory:")
    scan(db, [tmp_path])
    assert db.all_tracks()[0].title == "Some Name"


def test_upsert_preserves_play_stats():
    db = LibraryDB(":memory:")
    tid = db.upsert(Track(path="/x.mp3", title="A", youtube_id="abc"))
    db.record_play(tid)
    added = db.get(tid).date_added
    db.upsert(Track(path="/x.mp3", title="B"))
    t = db.get(tid)
    assert t.title == "B" and t.play_count == 1 and t.youtube_id == "abc"
    assert t.date_added == added and t.last_played


def test_local_playlist_order():
    db = LibraryDB(":memory:")
    ids = [db.upsert(Track(path=f"/{i}.mp3", title=str(i))) for i in range(3)]
    pid = db.save_playlist("Mix", [ids[2], ids[0]])
    assert [t.title for t in db.playlist_tracks(pid)] == ["2", "0"]
    assert not db.get_playlist(pid).is_youtube


def test_update_fields_rejects_unknown():
    import pytest
    db = LibraryDB(":memory:")
    tid = db.upsert(Track(path="/x.mp3"))
    with pytest.raises(ValueError):
        db.update_fields(tid, {"path; DROP TABLE tracks": 1})
