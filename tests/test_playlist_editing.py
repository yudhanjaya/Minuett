"""Editing playlists by hand, folder playlists, and how updates respect both."""

import os
import time
from pathlib import Path

import pytest

from minuett.core.downloader.playlist import Listing, RemoteEntry, reconcile
from minuett.core.downloader.worker import Downloader, Preferences
from minuett.core.library.browse import build_tree, matcher
from minuett.core.library.db import DONE, EXCLUDED, NEW, REMOVED, LibraryDB, Track
from minuett.core.library.folders import import_folder
from minuett.core.library.scanner import scan
from minuett.core.library.trash import move_to_trash

URL = "https://www.youtube.com/playlist?list=PLtest1234567"


def listing(*vids):
    return Listing(URL, "PLtest1234567", "Mix", None,
                   [RemoteEntry(i, v, v.upper()) for i, v in enumerate(vids)])


@pytest.fixture
def db():
    return LibraryDB(":memory:")


def downloaded(db, pid, *vids):
    for v in vids:
        tid = db.upsert(Track(path=f"/m/{v}.opus", title=v.upper(), youtube_id=v))
        e = next(e for e in db.entries(pid) if e.item_id == v)
        db.set_entry(e.id, track_id=tid, status=DONE)
    db.commit()


def titles(db, pid):
    return [t.title for t in db.playlist_tracks(pid)]


def test_added_songs_survive_updates_after_the_source_songs(db):
    pid = db.add_youtube_playlist("Mix", URL, "x", "/m/Mix")
    reconcile(db, pid, listing("a", "b"))
    downloaded(db, pid, "a", "b")
    extra = [db.upsert(Track(path=f"/other/{n}.flac", title=n)) for n in ("X", "Y")]
    assert db.add_to_playlist(pid, extra) == 2
    assert db.add_to_playlist(pid, extra[:1]) == 0          # already there
    assert titles(db, pid) == ["A", "B", "X", "Y"]
    # The source gains a song and loses one; your additions stay, after it.
    plan = reconcile(db, pid, listing("c", "a"))
    assert [e.item_id for e in plan.to_download] == ["c"] and plan.removed == 1
    downloaded(db, pid, "c")
    assert titles(db, pid) == ["C", "A", "X", "Y"]
    assert db.get_playlist(pid).total == 4


def test_removed_songs_stay_removed_until_restored(db):
    pid = db.add_youtube_playlist("Mix", URL, "x", "/m/Mix")
    reconcile(db, pid, listing("a", "b"))
    downloaded(db, pid, "a", "b")
    b = db.find_by_youtube_id("b").id
    extra = db.upsert(Track(path="/other/x.flac", title="X"))
    db.add_to_playlist(pid, [extra])
    db.remove_from_playlist(pid, [b, extra])
    assert titles(db, pid) == ["A"]
    assert not any(e.item_id == f"local:{extra}" for e in db.entries(pid, include_removed=True))
    plan = reconcile(db, pid, listing("a", "b"))
    assert plan.to_download == [] and plan.excluded == 1
    assert titles(db, pid) == ["A"]
    excluded = next(e for e in db.entries(pid) if e.status == EXCLUDED)
    db.restore_entry(excluded.id)
    reconcile(db, pid, listing("a", "b"))
    assert titles(db, pid) == ["A", "B"]      # relinked: the song is still in the library


def test_deleted_songs_are_not_downloaded_again(db):
    pid = db.add_youtube_playlist("Mix", URL, "x", "/m/Mix")
    reconcile(db, pid, listing("a", "b"))
    downloaded(db, pid, "a", "b")
    paths = db.delete_tracks([db.find_by_youtube_id("a").id])
    assert paths == ["/m/a.opus"] and db.find_by_youtube_id("a") is None
    plan = reconcile(db, pid, listing("a", "b"))
    assert plan.to_download == [] and titles(db, pid) == ["B"]


def test_playlist_arrangement_follows_membership(db):
    p1 = db.add_youtube_playlist("Mix", URL, "x", "/m/Mix")
    reconcile(db, p1, listing("a"))
    downloaded(db, p1, "a")
    p2 = db.create_playlist("Favourites")
    a = db.find_by_youtube_id("a")
    db.add_to_playlist(p2, [a.id])
    m = db.memberships()
    assert m[a.id] == ["Favourites", "Mix"]
    tree = build_tree(db.all_tracks(), "Playlist", m)
    assert [(n.label, n.count) for n in tree] == [("Favourites", 1), ("Mix", 1)]
    assert tree[0].children[0].tracks[0].id == a.id     # track leaves under the deepest group
    assert matcher("Playlist", ("Favourites",), m)(a)
    db.remove_from_playlist(p2, [a.id])
    assert db.memberships()[a.id] == ["Mix"]


# --- folders --------------------------------------------------------------------

def test_import_folder_as_playlist(tmp_path, tone):
    root = tmp_path / "Road Trip Mix"
    (root / "Disc 2").mkdir(parents=True)
    tone(root / "10 - Ten.opus", title="Ten")
    tone(root / "2 - Two.opus", title="Two")
    tone(root / "Disc 2" / "1 - Bonus.mp3", title="Bonus")
    (root / "cover.jpg").write_bytes(b"\xff\xd8")
    db = LibraryDB(":memory:")
    r = import_folder(db, root)
    pl = db.get_playlist(r.playlist_id)
    assert (pl.name, pl.source, pl.is_folder, r.total, r.added) == ("Road Trip Mix", "folder", True, 3, 3)
    assert titles(db, pl.id) == ["Two", "Ten", "Bonus"]       # natural order
    assert {t.source for t in db.all_tracks()} == {"folder"}

    # Changes on disk and by hand; importing again updates the same playlist.
    extra = db.upsert(Track(path="/elsewhere/x.flac", title="Extra"))
    db.add_to_playlist(pl.id, [extra])
    db.remove_from_playlist(pl.id, [db.get_by_path(str((root / "2 - Two.opus").resolve())).id])
    (root / "10 - Ten.opus").unlink()
    tone(root / "3 - Three.opus", title="Three")
    r2 = import_folder(db, root)
    assert r2.playlist_id == pl.id and r2.added == 1 and r2.removed == 1
    assert titles(db, pl.id) == ["Three", "Bonus", "Extra"]

    with pytest.raises(FileNotFoundError):
        import_folder(db, tmp_path / "nope")


def test_rescan_drops_deleted_folders_but_not_unplugged_drives(tmp_path, tone):
    db = LibraryDB(":memory:")
    kept = tmp_path / "Music" / "kept"
    gone = tmp_path / "Music" / "gone"
    kept.mkdir(parents=True)
    gone.mkdir()
    tone(kept / "a.mp3")
    tone(gone / "b.mp3")
    drive = tmp_path / "media" / "usb" / "music"
    db.upsert(Track(path=str(drive / "c.mp3"), title="on a drive"))
    scan(db, [kept, gone])
    import shutil
    shutil.rmtree(gone)
    r = scan(db, [kept, gone, drive])
    assert r.removed == 1
    assert sorted(Path(t.path).name for t in db.all_tracks()) == ["a.mp3", "c.mp3"]


def test_downloads_avoid_recreating_a_deleted_folder(tmp_path):
    db = LibraryDB(":memory:")
    pid = db.add_youtube_playlist("Mix", URL, "x", str(tmp_path / "deleted-root" / "Mix"))
    dl = Downloader(db, pid, Preferences(tmp_path / "Music"))
    assert dl.folder() == tmp_path / "Music" / "Mix"
    assert db.get_playlist(pid).folder == str(tmp_path / "Music" / "Mix")
    (tmp_path / "Music").mkdir()
    pid2 = db.add_youtube_playlist("Kept", URL + "2", "y", str(tmp_path / "Music" / "Kept"))
    assert Downloader(db, pid2, Preferences(tmp_path / "Other")).folder() == tmp_path / "Music" / "Kept"


def test_move_to_trash_is_recoverable():
    # conftest points XDG_DATA_HOME at a sandbox, so this uses a test Trash.
    home = Path(os.environ["XDG_DATA_HOME"])
    f = home / "victim.opus"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    assert move_to_trash([str(f), str(home / "already-gone.opus")]) == {}
    assert not f.exists()
    assert (home / "Trash" / "files" / "victim.opus").exists()


# --- UI: double-click toggles, menus ------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_double_click_plays_then_pauses_then_resumes(app, tmp_path, tone, monkeypatch):
    from minuett.core.player import Gst, State
    from minuett.ui.main_window import MainWindow
    from minuett.ui.views.library_model import COLUMNS
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    w = MainWindow()
    try:
        sink = Gst.ElementFactory.make("fakesink")
        sink.set_property("sync", True)
        w.player.playbin.set_property("audio-sink", sink)
        tone(tmp_path / "song.opus", seconds=5, title="Song")
        scan(w.db, [tmp_path])
        w.library_model.sync()
        title_col = [a for a, _ in COLUMNS].index("title")
        idx = w.proxy.index(0, title_col)
        w._on_table_double_click(idx)                       # double-click the Title cell
        end = time.time() + 5
        while w.player.state is not State.PLAYING and time.time() < end:
            w._tick(); app.processEvents(); time.sleep(0.02)
        assert w.player.state is State.PLAYING and w.table.state() != w.table.State.EditingState
        w._on_table_double_click(idx)
        while w.player.state is not State.PAUSED and time.time() < end:
            w._tick(); app.processEvents(); time.sleep(0.02)
        assert w.player.state is State.PAUSED
        w.activate_track(w.library_model.tracks[0].id)
        while w.player.state is not State.PLAYING and time.time() < end:
            w._tick(); app.processEvents(); time.sleep(0.02)
        assert w.player.state is State.PLAYING
        # Edit stays available on F2 and in the menu, not on double-click.
        assert w.table.editTriggers() == w.table.EditTrigger.EditKeyPressed
    finally:
        w.player.stop()
        w.downloads.shutdown()
