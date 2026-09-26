import pytest

from minuett.core.downloader.naming import clean_title, parse_title, safe_filename


@pytest.mark.parametrize("video, uploader, artist, title", [
    ("Daft Punk - Get Lucky (Official Video)", None, "Daft Punk", "Get Lucky"),
    ("Daft Punk – Get Lucky [Official Music Video] | 4K", None, "Daft Punk", "Get Lucky"),
    ("Jay-Z - Empire State of Mind (Lyrics)", None, "Jay-Z", "Empire State of Mind"),
    ("Artist - Song ft. Other (Official Audio) [HD]", None, "Artist", "Song ft. Other"),
    ("Song Name", "Some Artist - Topic", "Some Artist", "Song Name"),
    ("Song Name (Remastered 2011)", "BandVEVO", "Band", "Song Name"),
    ('Beyoncé "Halo"', None, "Beyoncé", "Halo"),
    ("Re-Wind (Official Video)", None, None, "Re-Wind"),
    ("Song (Live at Wembley)", None, None, "Song (Live at Wembley)"),
])
def test_parse(video, uploader, artist, title):
    p = parse_title(video, uploader)
    assert (p.artist, p.title) == (artist, title)


def test_clean_keeps_meaningful_brackets():
    assert clean_title("Song (Acoustic)") == "Song (Acoustic)"
    assert clean_title("Song (Remix) (Official Video)") == "Song (Remix)"


def test_safe_filename():
    assert safe_filename('AC/DC: "Best" <Of>?') == "AC_DC_ _Best_ _Of__"
    assert safe_filename("...") == "Untitled"
    assert len(safe_filename("x" * 500)) == 120


@pytest.mark.parametrize("video, title, genre", [
    ("Rainy Window 📚 [lofi hip hop]", "Rainy Window 📚", "Lofi Hip Hop"),
    ("Harbour Lights 🍃 [asian lofi]", "Harbour Lights 🍃", "Asian Lofi"),
    ("Artist - Beat [drum and bass]", "Beat", "Drum and Bass"),
    ("Artist - Song [Remix]", "Song [Remix]", None),
    ("Artist - Song [Live at Wembley]", "Song [Live at Wembley]", None),
    ("Artist - Song [Official Video]", "Song", None),
])
def test_genre_tags_move_out_of_titles(video, title, genre):
    p = parse_title(video, "Quiet Hours")
    assert (p.title, p.genre) == (title, genre)


def test_tags_from_info_uses_title_genre_not_youtube_category():
    from minuett.core.downloader.worker import tags_from_info
    tags = tags_from_info({"title": "Paper Lanterns ❄️ [lofi hip hop]", "uploader": "Quiet Hours",
                           "genre": "Music"}, None)
    assert (tags["title"], tags["artist"], tags["genre"]) == ("Paper Lanterns ❄️", "Quiet Hours", "Lofi Hip Hop")
    assert tags_from_info({"title": "A - B"}, None)["genre"] is None


def test_cleanup_moves_existing_genre_tags(tmp_path, tone):
    from minuett.core.library.cleanup import apply_genre_tag_fixes, find_genre_tag_fixes
    from minuett.core.library.db import LibraryDB
    from minuett.core.library.scanner import scan
    from minuett.core.library.tags import read_tags
    tone(tmp_path / "a.opus", title="Slow Tram 💤 [lofi hip hop]", artist="Quiet Hours", genre="Music")
    tone(tmp_path / "b.opus", title="Song [Remix]", artist="X")
    tone(tmp_path / "c.opus", title="Own genre [lofi]", artist="Y", genre="Jazz")
    db = LibraryDB(":memory:")
    scan(db, [tmp_path])
    for t in db.all_tracks():
        db.update_fields(t.id, {"youtube_id": t.path[-7:]})
    fixes = find_genre_tag_fixes(db)
    assert [(f.title, f.genre) for f in fixes] == [("Slow Tram 💤", "Lofi Hip Hop")]
    assert apply_genre_tag_fixes(db, fixes).ok
    t = db.get(fixes[0].track_id)
    assert (t.title, t.genre) == ("Slow Tram 💤", "Lofi Hip Hop")
    assert read_tags(t.path).tags["genre"] == "Lofi Hip Hop"
