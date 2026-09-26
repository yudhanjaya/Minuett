import pytest

from antiphon.core.downloader.naming import clean_title, parse_title, safe_filename


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
