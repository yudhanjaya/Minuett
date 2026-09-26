"""Import -> add songs upstream -> per-playlist Check and Update, through the UI layer.

YouTube is replaced by a fake listing whose entries point at local files.
"""

import functools
import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

import minuett.ui.download_manager as dm  # noqa: E402
from minuett.core.downloader.playlist import Listing, RemoteEntry  # noqa: E402
from minuett.core.downloader.worker import Downloader, Preferences  # noqa: E402
from minuett.core.library.db import LibraryDB  # noqa: E402
from minuett.ui.views.playlists_view import ACTIONS_COL, PlaylistsView  # noqa: E402

URL_A = "https://www.youtube.com/playlist?list=PLaaaaaaaaaa"
URL_B = "https://www.youtube.com/playlist?list=PLbbbbbbbbbb"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake_youtube(tmp_path, tone, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    remote = {URL_A: ("Road Trip", []), URL_B: ("Chill", [])}
    files = {}

    def add(url, vid, title):
        # yt-dlp takes a local file's title from its name, as YouTube would from the video.
        opus = tone(src / f"{vid}.opus")
        webm = files[vid] = src / f"{title}.webm"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(opus), "-c", "copy",
                        str(webm)], check=True)
        remote[url][1].append((vid, title))

    def fetch(url):
        name, items = remote[url]
        return Listing(url, url[-12:], name, None,
                       [RemoteEntry(i, v, t) for i, (v, t) in enumerate(items)])

    monkeypatch.setattr(dm, "fetch_listing", fetch)
    monkeypatch.setattr(dm, "Downloader", functools.partial(
        Downloader, url_for=lambda e: files[e.youtube_id].as_uri(),
        extra_opts={"enable_file_urls": True}))
    return add


def wait_for(manager, n_ops=1, timeout=30000):
    results = []
    loop = QEventLoop()

    def done(r):
        results.append(r)
        if len(results) == n_ops:
            loop.quit()
    manager.op_finished.connect(done)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    manager.op_finished.disconnect(done)
    assert len(results) == n_ops, "timed out"
    return results


def row_buttons(view, row):
    cell = view.tree.itemWidget(view.tree.topLevelItem(row), ACTIONS_COL)
    return {b.text(): b for b in cell.findChildren(QPushButton)}


def test_import_then_update_one_playlist_at_a_time(app, tmp_path, fake_youtube):
    db_path = str(tmp_path / "lib.db")
    db = LibraryDB(db_path)
    prefs = Preferences(tmp_path / "Music", sleep_max=0)
    manager = dm.DownloadManager(db_path, lambda: prefs)
    view = PlaylistsView(db, manager)
    try:
        fake_youtube(URL_A, "a1", "Band - First (Official Video)")
        fake_youtube(URL_B, "b1", "Other - Calm")
        manager.import_url(URL_A)
        manager.import_url(URL_B)
        r1, r2 = wait_for(manager, 2)
        assert (r1.downloaded, r2.downloaded) == (1, 1) and not r1.error

        view.refresh()
        names = [view.tree.topLevelItem(i).text(0) for i in range(view.tree.topLevelItemCount())]
        assert names == ["Chill", "Road Trip"]
        assert set(row_buttons(view, 1)) == {"Check", "Update"}

        # Songs get added to BOTH playlists on YouTube...
        fake_youtube(URL_A, "a2", "Band - Second")
        fake_youtube(URL_B, "b2", "Other - Calmer")

        # ...Check on Road Trip only reports, doesn't download.
        row_buttons(view, 1)["Check"].click()
        assert view.tree.itemWidget(view.tree.topLevelItem(1), ACTIONS_COL).findChildren(QPushButton) == []
        (r,) = wait_for(manager)
        assert r.kind == "check" and len(r.plan.to_download) == 1 and r.downloaded == 0
        view.refresh()
        assert view.tree.topLevelItem(1).text(3) == "1"      # New
        assert view.tree.topLevelItem(1).text(2) == "1 / 2"  # Songs
        assert view.tree.topLevelItem(1).text(1) == "YouTube"

        # ...Update on Road Trip fetches only its new song; Chill is untouched.
        row_buttons(view, 1)["Update"].click()
        (r,) = wait_for(manager)
        assert r.downloaded == 1
        road = db.playlist_by_url(URL_A)
        chill = db.playlist_by_url(URL_B)
        assert [t.title for t in db.playlist_tracks(road.id)] == ["First", "Second"]
        assert [t.youtube_id for t in db.playlist_tracks(chill.id)] == ["b1"]
        assert chill.last_checked == db.get_playlist(chill.id).last_checked  # never re-checked

        # Library rows carry the playlist name for sorting/filtering.
        assert {t.source_playlist for t in db.all_tracks()} == {"Road Trip", "Chill"}
        view.grab().save(str(tmp_path.parent / "playlists_view.png"))
    finally:
        manager.shutdown()
