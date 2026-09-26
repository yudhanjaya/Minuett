"""Spotify links, export files, YouTube Music matching, and the v3 schema."""

import json
import sqlite3
from pathlib import Path

import pytest

from minuett.core.downloader.matching import Candidate, Matcher, Target, score
from minuett.core.downloader.playlist import ListingError, reconcile, source_of_url
from minuett.core.downloader.sources.files import import_key, listing_from_file, parse_export
from minuett.core.downloader.sources.spotify import listing_from_embed, parse_spotify_url
from minuett.core.downloader.worker import (
    Downloader, JobStatus, NO_MATCH_MESSAGE, Preferences, jobs_for,
)
from minuett.core.library.db import DONE, UNAVAILABLE, LibraryDB, Track
from minuett.core.library.tags import read_tags


# --- URLs -----------------------------------------------------------------------

@pytest.mark.parametrize("url, source", [
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", "spotify"),
    ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "spotify"),
    ("https://music.youtube.com/playlist?list=PLexampleMusicList01", "youtube-music"),
    ("https://www.youtube.com/playlist?list=PLabcdefghij", "youtube"),
    ("https://example.com/x", None),
])
def test_source_of_url(url, source):
    assert source_of_url(url) == source


def test_parse_spotify_url():
    assert parse_spotify_url("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M?si=1") == \
        ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert parse_spotify_url("spotify:album:4LH4d3cOWNNsVw41Gqt2kv") == ("album", "4LH4d3cOWNNsVw41Gqt2kv")
    with pytest.raises(ListingError):
        parse_spotify_url("https://open.spotify.com/track/4LH4d3cOWNNsVw41Gqt2kv")


def _embed(tracks, name="Road Trip", n=None):
    entity = {"name": name, "subtitle": "someone", "trackList": [
        {"uri": f"spotify:track:{i:022d}", "title": t, "subtitle": a, "duration": d * 1000}
        for i, (t, a, d) in enumerate(tracks)]}
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_spotify_embed_listing():
    lst = listing_from_embed(_embed([("Nicole Kidman", "ADÉLA", 181), ("Loser", "Tame Impala", 223)]),
                             "playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert (lst.title, lst.source, lst.truncated) == ("Road Trip", "spotify", False)
    e = lst.entries[1]
    assert (e.title, e.artist, e.duration, e.needs_match, e.youtube_id) == ("Loser", "Tame Impala", 223, True, None)
    assert e.url == "https://open.spotify.com/track/0000000000000000000001"
    many = listing_from_embed(_embed([(f"t{i}", "a", 100) for i in range(100)]), "playlist", "x" * 22)
    assert many.truncated


def test_spotify_embed_without_data_is_a_clear_error():
    with pytest.raises(ListingError):
        listing_from_embed("<html>nothing</html>", "playlist", "x" * 22)


# --- export files ---------------------------------------------------------------

EXPORTIFY = '''"Track URI","Track Name","Album Name","Artist Name(s)","Release Date","Duration (ms)","ISRC"
"spotify:track:70cHKK8bHAfJrOGVnfRG9J","Nicole Kidman","Nicole Kidman","ADÉLA","2026-01-01","181270","USUM1"
"spotify:track:5CQ30WqJwcep0pYcV4AMNc","Stairway to Heaven - Remaster","Led Zeppelin IV","Led Zeppelin;Jimmy Page","1971","482830","GBAAA"
'''
TUNEMYMUSIC = '''Track name,Artist name,Album,Playlist name,Type,ISRC
Sweater Weather,The Neighbourhood,I Love You.,Pandora Thumbs,Playlist,USUM71213000
Riptide,Vance Joy,Dream Your Life Away,Pandora Thumbs,Playlist,
'''
SOUNDIIZ = '''title;artist;album;isrc
Blinding Lights;The Weeknd;After Hours;USUG11904206
'''
TEXT = '''1. Daft Punk - One More Time
Air - La Femme d'Argent

# a comment
'''


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_exportify_csv(tmp_path):
    ex = parse_export(_write(tmp_path, "road.csv", EXPORTIFY), "spotify")
    a, b = ex.entries
    assert (a.title, a.artist, a.album, round(a.duration), a.isrc) == ("Nicole Kidman", "ADÉLA", "Nicole Kidman", 181, "USUM1")
    assert a.item_id == "spotify:track:70cHKK8bHAfJrOGVnfRG9J"
    assert a.url == "https://open.spotify.com/track/70cHKK8bHAfJrOGVnfRG9J"
    assert b.artist == "Led Zeppelin, Jimmy Page" and a.needs_match


def test_tunemymusic_csv_with_playlist_name(tmp_path):
    ex = parse_export(_write(tmp_path, "export.csv", TUNEMYMUSIC), "pandora")
    assert ex.name == "Pandora Thumbs"
    assert [e.item_id for e in ex.entries] == ["isrc:USUM71213000", "vance joy|riptide"]


def test_soundiiz_semicolon_csv(tmp_path):
    ex = parse_export(_write(tmp_path, "s.csv", SOUNDIIZ), "other")
    assert (ex.entries[0].title, ex.entries[0].artist) == ("Blinding Lights", "The Weeknd")


def test_text_list(tmp_path):
    ex = parse_export(_write(tmp_path, "list.txt", TEXT), "pandora")
    assert [(e.artist, e.title) for e in ex.entries] == [("Daft Punk", "One More Time"),
                                                          ("Air", "La Femme d'Argent")]


def test_bad_files(tmp_path):
    with pytest.raises(ListingError):
        parse_export(_write(tmp_path, "e.csv", ""), "other")
    with pytest.raises(ListingError):
        parse_export(_write(tmp_path, "x.csv", "foo,bar\n1,2\n"), "other")


def test_reimporting_a_newer_export_updates_the_playlist(tmp_path):
    db = LibraryDB(":memory:")
    first = listing_from_file(_write(tmp_path, "a.csv", EXPORTIFY), "spotify", "Road Trip")
    assert first.url == import_key("spotify", "Road Trip") == "import:spotify:road-trip"
    pid = db.add_imported_playlist("Road Trip", first.url, None, "/m/Road Trip", "spotify")
    plan = reconcile(db, pid, first)
    assert len(plan.to_download) == 2 and all(e.youtube_id is None for e in plan.to_download)
    newer = EXPORTIFY.splitlines()[0] + "\n" + EXPORTIFY.splitlines()[2] + \
        '\n"spotify:track:0000000000000000000009","New Song","A","Someone","2026","200000",""\n'
    plan = reconcile(db, pid, listing_from_file(_write(tmp_path, "b.csv", newer), "spotify", "Road Trip"))
    assert plan.removed == 1
    assert sorted(e.title for e in plan.to_download) == ["New Song", "Stairway to Heaven - Remaster"]
    assert db.get_playlist(pid).is_file_import


# --- matching -------------------------------------------------------------------

def test_score_prefers_right_song_and_rejects_wrong_length():
    t = Target("Loser", "Tame Impala", duration=223)
    assert score(t, Candidate("a", "Loser", "Tame Impala", 223)) > 0.95
    assert score(t, Candidate("b", "Loser (Official Video)", "Tame Impala", 224)) > 0.9
    assert score(t, Candidate("c", "Loser", "Beck", 235)) < 0.8
    assert score(t, Candidate("d", "Loser", "Tame Impala", 400)) <= 0.4   # extended mix


def _fake_matcher(ytm, resolved=None, yt=()):
    calls = []

    def search(where, query, n):
        calls.append(where)
        return list(ytm if where == "ytmusic" else yt)

    def resolve(vid):
        return (resolved or {}).get(vid)
    m = Matcher(search=search, resolve=resolve)
    m.calls = calls
    return m


def test_matcher_confirms_ytmusic_candidate():
    m = _fake_matcher([Candidate("x", "Karaoke Loser"), Candidate("ok", "Loser")],
                      {"ok": Candidate("ok", "Loser", "Tame Impala", 223),
                       "x": Candidate("x", "Karaoke Loser", "Sing Along", 223)})
    r = m.find(Target("Loser", "Tame Impala", duration=223))
    assert r.candidate.video_id == "ok" and m.calls == ["ytmusic"]


def test_matcher_falls_back_to_youtube_then_gives_up():
    m = _fake_matcher([], yt=[Candidate("yt", "Riptide", "Vance Joy", 204, "youtube")])
    assert m.find(Target("Riptide", "Vance Joy", duration=204)).candidate.video_id == "yt"
    assert _fake_matcher([], yt=[Candidate("z", "Something Else", "Nobody", 30)]).find(
        Target("Riptide", "Vance Joy", duration=204)) is None


# --- matched downloads end to end -------------------------------------------------

def test_matched_download_uses_catalog_tags_and_records_source(tmp_path, tone):
    import subprocess
    src = tmp_path / "src.webm"
    opus = tone(tmp_path / "t.opus", seconds=2)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(opus), "-c", "copy", str(src)], check=True)
    db = LibraryDB(":memory:")
    lst = listing_from_file(_write(tmp_path, "a.csv", EXPORTIFY), "spotify", "Road Trip")
    pid = db.add_imported_playlist("Road Trip", lst.url, None, str(tmp_path / "out"), "spotify")
    plan = reconcile(db, pid, lst)
    first, second = plan.to_download
    # Second song is already in the library under the matched video id.
    db.upsert(Track(path="/lib/stairway.opus", title="Stairway", youtube_id="vid-b"))
    matcher = _fake_matcher([Candidate("vid-a", "Nicole Kidman"), Candidate("vid-b", "Stairway to Heaven")],
                            {"vid-a": Candidate("vid-a", "Nicole Kidman", "ADÉLA", 181),
                             "vid-b": Candidate("vid-b", "Stairway to Heaven (Remaster)", "Led Zeppelin", 483)})
    matcher._search = lambda where, q, n: [c for c in (Candidate("vid-a", "Nicole Kidman"),
                                                       Candidate("vid-b", "Stairway to Heaven"))
                                           if where == "ytmusic" and c.title.split()[0] in q]
    dl = Downloader(db, pid, Preferences(tmp_path, sleep_max=0), url_for=lambda e: src.as_uri(),
                    extra_opts={"enable_file_urls": True}, matcher=matcher)
    seen = []
    jobs = jobs_for(plan.to_download)
    dl.run(jobs, lambda i, j: seen.append(j.status))
    assert JobStatus.MATCHING in seen
    assert [j.status for j in jobs] == [JobStatus.DONE, JobStatus.DONE]
    t = db.get(jobs[0].track_id)
    assert (t.title, t.artist, t.album, t.youtube_id) == ("Nicole Kidman", "ADÉLA", "Nicole Kidman", "vid-a")
    assert (t.source, t.source_url) == ("spotify", "https://open.spotify.com/track/70cHKK8bHAfJrOGVnfRG9J")
    assert read_tags(t.path).tags["artist"] == "ADÉLA"
    assert db.get(jobs[1].track_id).path == "/lib/stairway.opus"   # linked, not downloaded
    assert {e.status for e in db.entries(pid)} == {DONE}


def test_no_match_is_reported_not_guessed(tmp_path):
    db = LibraryDB(":memory:")
    lst = listing_from_file(_write(tmp_path, "a.txt", "Nobody - Nothing\n"), "pandora", "P")
    pid = db.add_imported_playlist("P", lst.url, None, str(tmp_path / "out"), "pandora")
    plan = reconcile(db, pid, lst)
    dl = Downloader(db, pid, Preferences(tmp_path, sleep_max=0), matcher=_fake_matcher([]))
    jobs = jobs_for(plan.to_download)
    dl.run(jobs, lambda i, j: None)
    assert jobs[0].status is JobStatus.UNAVAILABLE and jobs[0].error == NO_MATCH_MESSAGE
    assert db.entries(pid)[0].status == UNAVAILABLE


# --- schema v3 migration ------------------------------------------------------------

def test_v2_database_migrates_to_sources(tmp_path):
    path = tmp_path / "v2.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT, artist TEXT,
          album TEXT, album_artist TEXT, genre TEXT, year INTEGER, track_no INTEGER, disc_no INTEGER,
          duration_ms INTEGER, codec TEXT, bitrate INTEGER, youtube_id TEXT, source_playlist TEXT,
          date_added TEXT, play_count INTEGER DEFAULT 0, last_played TEXT, rating INTEGER, mtime REAL);
        CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_url TEXT UNIQUE,
          youtube_id TEXT, folder TEXT, added TEXT, last_checked TEXT, last_updated TEXT);
        CREATE TABLE playlist_entries (id INTEGER PRIMARY KEY, playlist_id INTEGER NOT NULL,
          position INTEGER, youtube_id TEXT, title TEXT, track_id INTEGER, status TEXT NOT NULL DEFAULT 'new',
          error TEXT, UNIQUE (playlist_id, youtube_id));
        INSERT INTO tracks (id, path, title, youtube_id) VALUES (1, '/a.opus', 'A', 'vidA');
        INSERT INTO playlists (id, name, source_url) VALUES (1, 'Road Trip', 'https://www.youtube.com/playlist?list=PLx');
        INSERT INTO playlist_entries (playlist_id, position, youtube_id, title, track_id, status)
          VALUES (1, 0, 'vidA', 'A', 1, 'done'), (1, 1, 'vidB', 'B', NULL, 'failed');
        PRAGMA user_version = 2;
    """)
    c.close()
    db = LibraryDB(path)
    es = db.entries(1)
    assert [(e.item_id, e.youtube_id, e.status) for e in es] == [("vidA", "vidA", "done"), ("vidB", "vidB", "failed")]
    assert db.get_playlist(1).source == "youtube"
    t = db.get(1)
    assert (t.source, t.source_url) == ("youtube", "https://www.youtube.com/watch?v=vidA")
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    LibraryDB(path)  # re-opening a v3 database is a no-op
