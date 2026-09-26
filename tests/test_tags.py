import pytest

from minuett.core.library.tags import TagError, read_tags, write_tags

FORMATS = ["mp3", "opus", "flac", "m4a", "ogg"]


@pytest.mark.parametrize("ext", FORMATS)
def test_roundtrip(tmp_path, tone, ext):
    f = tone(tmp_path / f"a.{ext}")
    write_tags(f, {"title": "Song", "artist": "Band", "album": "LP",
                   "album_artist": "Band", "genre": "Rock",
                   "year": 1999, "track_no": 3, "disc_no": 1})
    info = read_tags(f)
    assert info.tags == {"title": "Song", "artist": "Band", "album": "LP",
                         "album_artist": "Band", "genre": "Rock",
                         "year": 1999, "track_no": 3, "disc_no": 1}
    assert 900 <= info.duration_ms <= 1200
    assert info.codec


@pytest.mark.parametrize("ext", FORMATS)
def test_clear_field(tmp_path, tone, ext):
    f = tone(tmp_path / f"a.{ext}")
    write_tags(f, {"title": "X", "genre": "Pop"})
    write_tags(f, {"genre": None})
    assert "genre" not in read_tags(f).tags


def test_codec_names(tmp_path, tone):
    expect = {"mp3": "mp3", "opus": "opus", "flac": "flac", "m4a": "aac", "ogg": "vorbis"}
    for ext, codec in expect.items():
        assert read_tags(tone(tmp_path / f"a.{ext}")).codec == codec


def test_track_number_with_total(tmp_path, tone):
    f = tone(tmp_path / "a.flac", track="4/12", date="2020-05-01")
    tags = read_tags(f).tags
    assert tags["track_no"] == 4 and tags["year"] == 2020


def test_not_audio(tmp_path):
    p = tmp_path / "x.mp3"
    p.write_text("not audio")
    with pytest.raises(TagError):
        read_tags(p)


def test_unknown_field(tmp_path, tone):
    with pytest.raises(TagError):
        write_tags(tone(tmp_path / "a.mp3"), {"bogus": 1})
