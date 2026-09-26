import sqlite3
import subprocess
from pathlib import Path

import pytest

from antiphon.core.downloader.playlist import (
    Listing, RemoteEntry, listing_from_info, normalise_url, reconcile,
)
from antiphon.core.downloader.worker import (
    Downloader, JobStatus, Preferences, jobs_for, retry_failed, tags_from_info,
)
from antiphon.core.library.db import DONE, FAILED, NEW, REMOVED, UNAVAILABLE, LibraryDB, Track

URL = "https://www.youtube.com/playlist?list=PLtest1234567"


def listing(*items, title="Road Trip"):
    return Listing(URL, "PLtest1234567", title, None,
                   [RemoteEntry(i, vid, t) for i, (vid, t) in enumerate(items)])


@pytest.fixture
def db():
    return LibraryDB(":memory:")


def new_playlist(db, name="Road Trip"):
    return db.add_youtube_playlist(name, URL, "PLtest1234567", f"/music/{name}")


def test_normalise_url():
    assert normalise_url("https://www.youtube.com/watch?v=abc&list=PLtest1234567&index=3") == URL
    assert normalise_url("  https://youtube.com/playlist?list=PLtest1234567 ") == URL
    assert normalise_url("https://example.com/x") == "https://example.com/x"


def test_add_is_idempotent_by_url(db):
    assert new_playlist(db) == new_playlist(db)


def test_first_sync_queues_everything_available(db):
    pid = new_playlist(db)
    plan = reconcile(db, pid, listing(("a", "A - One"), ("b", "[Private video]"), ("c", "C - Three")))
    assert [e.youtube_id for e in plan.to_download] == ["a", "c"]
    assert plan.unavailable == 1
    pl = db.get_playlist(pid)
    assert (pl.total, pl.downloaded, pl.pending) == (3, 0, 2)
    assert pl.last_checked


def mark_downloaded(db, pid, vid, path):
    tid = db.upsert(Track(path=path, title=vid, youtube_id=vid, source_playlist="Road Trip"))
    entry = next(e for e in db.entries(pid) if e.youtube_id == vid)
    db.set_entry(entry.id, track_id=tid, status=DONE)
    db.commit()
    return tid


def test_update_only_fetches_new_and_tracks_removals(db):
    pid = new_playlist(db)
    reconcile(db, pid, listing(("a", "One"), ("b", "Two"), ("c", "Three")))
    for vid in "abc":
        mark_downloaded(db, pid, vid, f"/music/{vid}.opus")

    # On YouTube: "b" removed, "d" added at the top, order changed.
    plan = reconcile(db, pid, listing(("d", "Four"), ("c", "Three"), ("a", "One")))
    assert [e.youtube_id for e in plan.to_download] == ["d"]
    assert (plan.kept, plan.removed) == (2, 1)
    assert [t.youtube_id for t in db.playlist_tracks(pid)] == ["c", "a"]  # d not downloaded yet
    removed = [e for e in db.entries(pid, include_removed=True) if e.status == REMOVED]
    assert [e.youtube_id for e in removed] == ["b"]
    assert db.find_by_youtube_id("b") is not None  # file stays in the library

    # Re-adding a removed video brings it back without downloading again.
    plan = reconcile(db, pid, listing(("d", "Four"), ("b", "Two")))
    assert [e.youtube_id for e in plan.to_download] == ["d"]


def test_video_in_two_playlists_is_linked_not_redownloaded(db):
    p1 = new_playlist(db)
    reconcile(db, p1, listing(("a", "One")))
    mark_downloaded(db, p1, "a", "/music/a.opus")
    p2 = db.add_youtube_playlist("Other", URL + "x", "PLother", "/music/Other")
    plan = reconcile(db, p2, Listing(URL + "x", "PLother", "Other", None, [RemoteEntry(0, "a", "One")]))
    assert plan.to_download == [] and plan.linked == 1
    assert [t.youtube_id for t in db.playlist_tracks(p2)] == ["a"]


def test_failed_entries_are_retried_on_update(db):
    pid = new_playlist(db)
    plan = reconcile(db, pid, listing(("a", "One")))
    db.set_entry(plan.to_download[0].id, status=FAILED, error="boom"); db.commit()
    plan = reconcile(db, pid, listing(("a", "One")))
    assert [e.status for e in plan.to_download] == [FAILED]
    assert db.get_playlist(pid).pending == 1


def test_rename_upstream_carries_playlist_column(db):
    pid = new_playlist(db)
    reconcile(db, pid, listing(("a", "One")))
    tid = mark_downloaded(db, pid, "a", "/music/a.opus")
    plan = reconcile(db, pid, listing(("a", "One"), title="Road Trip 2026"))
    assert plan.renamed_from == "Road Trip"
    assert db.get_playlist(pid).name == "Road Trip 2026"
    assert db.get(tid).source_playlist == "Road Trip 2026"


def test_deleted_track_is_downloaded_again(db):
    pid = new_playlist(db)
    reconcile(db, pid, listing(("a", "One")))
    mark_downloaded(db, pid, "a", "/music/a.opus")
    db.remove_paths(["/music/a.opus"])
    plan = reconcile(db, pid, listing(("a", "One")))
    assert [e.status for e in plan.to_download] == [NEW]


def test_unavailable_then_restored(db):
    pid = new_playlist(db)
    reconcile(db, pid, listing(("a", "[Deleted video]")))
    assert db.entries(pid)[0].status == UNAVAILABLE
    plan = reconcile(db, pid, listing(("a", "Back")))
    assert [e.youtube_id for e in plan.to_download] == ["a"]


def test_listing_from_flat_info():
    info = {"_type": "playlist", "id": "PLx", "title": "T", "uploader": "U",
            "entries": [{"id": "a", "title": "A"}, None, {"id": "b", "title": "B", "channel": "C"}]}
    lst = listing_from_info("https://www.youtube.com/playlist?list=PLxxxxxxxxxx", info)
    assert [(e.position, e.video_id) for e in lst.entries] == [(0, "a"), (1, "b")]
    assert lst.entries[1].uploader == "C"


def test_v1_database_migrates(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT,
          artist TEXT, album TEXT, album_artist TEXT, genre TEXT, year INTEGER, track_no INTEGER,
          disc_no INTEGER, duration_ms INTEGER, codec TEXT, bitrate INTEGER, youtube_id TEXT,
          source_playlist TEXT, date_added TEXT, play_count INTEGER DEFAULT 0, last_played TEXT,
          rating INTEGER, mtime REAL);
        CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_url TEXT UNIQUE);
        CREATE TABLE playlist_items (playlist_id INTEGER, position INTEGER, track_id INTEGER);
        INSERT INTO tracks (id, path, title) VALUES (1, '/a.mp3', 'A');
        INSERT INTO playlists VALUES (1, 'Old', NULL);
        INSERT INTO playlist_items VALUES (1, 0, 1);
        PRAGMA user_version = 1;
    """)
    conn.close()
    db = LibraryDB(path)
    assert [t.title for t in db.playlist_tracks(1)] == ["A"]
    assert db.get_playlist(1).folder is None


# --- downloader against local files (no network) ---------------------------

def test_tags_from_info_prefers_youtube_music():
    info = {"title": "whatever", "track": "Song", "artists": ["A", "B"], "album": "LP",
            "release_year": 2019}
    assert tags_from_info(info, None) == {"year": 2019, "title": "Song", "artist": "A, B",
                                          "album": "LP", "genre": None}
    assert tags_from_info({"title": "X - Y (Official Video)"}, None) == {
        "year": None, "genre": None, "artist": "X", "title": "Y"}


def test_downloader_end_to_end_local(tmp_path, tone, db):
    src = tmp_path / "src"
    src.mkdir()
    tone(src / "a.webm".replace(".webm", ".opus"))  # make an opus, then wrap as webm
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src / "a.opus"), "-c", "copy",
                    str(src / "Band - Song (Official Video).webm")], check=True)
    pid = db.add_youtube_playlist("Mix", URL, "PLtest1234567", str(tmp_path / "out" / "Mix"))
    plan = reconcile(db, pid, listing(("ok", "Band - Song (Official Video)"), ("bad", "Missing"),
                                      title="Mix"))
    urls = {"ok": (src / "Band - Song (Official Video).webm").as_uri(),
            "bad": (src / "nope.webm").as_uri()}
    dl = Downloader(db, pid, Preferences(tmp_path / "out", sleep_max=0),
                    url_for=lambda e: urls[e.youtube_id], extra_opts={"enable_file_urls": True})
    jobs = jobs_for(plan.to_download)
    seen = []
    dl.run(jobs, lambda i, j: seen.append((i, j.status)))

    assert [j.status for j in jobs] == [JobStatus.DONE, JobStatus.FAILED]
    assert (0, JobStatus.TAGGING) in seen
    t = db.get(jobs[0].track_id)
    assert (t.artist, t.title, t.codec, t.youtube_id, t.source_playlist) == (
        "Band", "Song", "opus", "ok", "Mix")
    assert t.year is None
    assert Path(t.path).parent == tmp_path / "out" / "Mix"
    pl = db.get_playlist(pid)
    assert (pl.downloaded, pl.pending) == (1, 1) and pl.last_updated
    assert jobs[1].error

    # Retry Failed re-runs only the failed row.
    urls["bad"] = urls["ok"]
    retry_failed(jobs)
    assert [j.status for j in jobs] == [JobStatus.DONE, JobStatus.QUEUED]


def test_cancel_stops_queue(tmp_path, db):
    pid = db.add_youtube_playlist("Mix", URL, "PLtest1234567", str(tmp_path / "Mix"))
    plan = reconcile(db, pid, listing(("a", "A"), ("b", "B"), title="Mix"))
    dl = Downloader(db, pid, Preferences(tmp_path, sleep_max=0), url_for=lambda e: "file:///nope")
    dl.cancel()
    jobs = jobs_for(plan.to_download)
    dl.run(jobs, lambda i, j: None)
    assert all(j.status is JobStatus.CANCELLED for j in jobs)
    assert db.get_playlist(pid).pending == 2  # nothing marked failed


# --- live streams, retries, unavailable videos --------------------------------

from antiphon.core.downloader.worker import classify_error  # noqa: E402
from antiphon.core.library.db import LIVE  # noqa: E402


def test_live_streams_are_skipped_not_queued(db):
    pid = new_playlist(db)
    lst = listing(("a", "Song"), ("radio", "lofi radio 24/7"))
    lst.entries[1].live_status = "is_live"
    plan = reconcile(db, pid, lst)
    assert [e.youtube_id for e in plan.to_download] == ["a"] and plan.live == 1
    assert {e.youtube_id: e.status for e in db.entries(pid)}["radio"] == LIVE
    assert db.get_playlist(pid).pending == 1


def test_listing_reads_live_status():
    info = {"_type": "playlist", "id": "PLx", "title": "T",
            "entries": [{"id": "r", "title": "radio", "live_status": "is_live"}]}
    assert listing_from_info(URL, info).entries[0].is_live


@pytest.mark.parametrize("msg, kind", [
    ("unable to download video data: HTTP Error 403: Forbidden", "retry"),
    ("[youtube] abc: Video unavailable", "unavailable"),
    ("[youtube] abc: Video unavailable. This content isn't available in your country", "unavailable"),
    ("Postprocessing: something odd", "failed"),
])
def test_classify_error(msg, kind):
    assert classify_error(msg) == kind


def _downloader_with_script(db, tmp_path, script):
    """A Downloader whose attempts return scripted results instead of hitting YouTube."""
    pid = db.add_youtube_playlist("Mix", URL, "PLtest1234567", str(tmp_path / "Mix"))
    plan = reconcile(db, pid, listing(("a", "A"), title="Mix"))
    dl = Downloader(db, pid, Preferences(tmp_path, sleep_max=0))
    dl.retry_delays = (0.0, 0.0)
    calls = []

    def fake_attempt(i, job, folder, on_update):
        calls.append(1)
        outcome, err = script[len(calls) - 1]
        (tmp_path / "Mix").mkdir(exist_ok=True)
        (tmp_path / "Mix" / f"A [{job.entry.youtube_id}].jpg").write_bytes(b"x")  # leftover
        if outcome == "done":
            job.status = JobStatus.DONE
        return outcome, err

    dl._attempt = fake_attempt
    return dl, jobs_for(plan.to_download), calls, pid


def test_403_is_retried_then_succeeds(db, tmp_path):
    dl, jobs, calls, _ = _downloader_with_script(
        db, tmp_path, [("error", "HTTP Error 403: Forbidden"), ("done", "")])
    dl.run(jobs, lambda i, j: None)
    assert len(calls) == 2 and jobs[0].status is JobStatus.DONE


def test_403_gives_up_after_retries(db, tmp_path):
    dl, jobs, calls, pid = _downloader_with_script(
        db, tmp_path, [("error", "HTTP Error 403: Forbidden")] * 3)
    dl.run(jobs, lambda i, j: None)
    assert len(calls) == 3 and jobs[0].status is JobStatus.FAILED
    assert db.entries(pid)[0].status == FAILED
    assert not list((tmp_path / "Mix").glob("*[[]a[]].*"))  # leftovers cleaned up


def test_unavailable_is_not_retried_or_counted_pending(db, tmp_path):
    dl, jobs, calls, pid = _downloader_with_script(
        db, tmp_path, [("error", "[youtube] a: Video unavailable")])
    dl.run(jobs, lambda i, j: None)
    assert len(calls) == 1 and jobs[0].status is JobStatus.UNAVAILABLE
    assert db.entries(pid)[0].status == UNAVAILABLE and db.get_playlist(pid).pending == 0
    retry_failed(jobs)
    assert jobs[0].status is JobStatus.UNAVAILABLE  # Retry Failed leaves it alone
    # ...but the next Update checks it again.
    plan = reconcile(db, pid, listing(("a", "A"), title="Mix"))
    assert [e.youtube_id for e in plan.to_download] == ["a"]


# --- output format ------------------------------------------------------------

from antiphon.core.downloader.worker import normalise_format, ytdlp_options  # noqa: E402


def test_format_preferences():
    assert normalise_format(None) == "opus"
    assert normalise_format("native") == "original"   # old setting name
    assert normalise_format("bogus") == "opus"
    o = ytdlp_options(Path("/x"), Preferences(Path("/x"), audio_format="opus"))
    assert o["format"].startswith("bestaudio[acodec=opus]")
    pp = o["postprocessors"][0]
    assert (pp["preferredcodec"], pp["preferredquality"]) == ("opus", "256")


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_name,bit_rate:format=bit_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    import json
    d = json.loads(out)
    return d["streams"][0]["codec_name"], int(d["streams"][0].get("bit_rate") or d["format"]["bit_rate"])


@pytest.mark.parametrize("src_codec, src_ext, expect_copy", [
    (["-c:a", "libopus", "-b:a", "96k"], "webm", True),
    (["-c:a", "aac", "-b:a", "128k"], "m4a", False),
])
def test_opus_default_copies_opus_and_converts_others_at_high_bitrate(
        tmp_path, db, src_codec, src_ext, expect_copy):
    import subprocess as sp
    src = tmp_path / f"Artist - Song.{src_ext}"
    sp.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=4:c=pink",
            *src_codec, str(src)], check=True)
    pid = db.add_youtube_playlist("Mix", URL, "PLtest1234567", str(tmp_path / "out"))
    plan = reconcile(db, pid, listing(("v", "Artist - Song"), title="Mix"))
    dl = Downloader(db, pid, Preferences(tmp_path, sleep_max=0), url_for=lambda e: src.as_uri(),
                    extra_opts={"enable_file_urls": True})
    jobs = jobs_for(plan.to_download)
    dl.run(jobs, lambda i, j: None)
    assert jobs[0].status is JobStatus.DONE, jobs[0].error
    out = Path(db.get(jobs[0].track_id).path)
    codec, bitrate = _probe(out)
    assert out.suffix == ".opus" and codec == "opus"
    if expect_copy:
        assert _probe(src)[1] * 0.8 < bitrate < _probe(src)[1] * 1.25   # untouched
    else:
        assert bitrate > 200_000   # converted at the high bitrate, not a default ~96k
