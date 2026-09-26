"""Tag reading and writing via mutagen, normalised across formats.

``mutagen.File(path, easy=True)`` gives a uniform dict-like interface for
MP3 (EasyID3), M4A (EasyMP4), FLAC, Ogg Vorbis and Opus, which covers the
text fields. Cover art is format-specific and handled separately below.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, PictureType
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wv",
})

# Our field name -> easy-tag key.
EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "albumartist",
    "genre": "genre",
    "year": "date",
    "track_no": "tracknumber",
    "disc_no": "discnumber",
}

_LEADING_INT = re.compile(r"\s*(\d+)")
_YEAR = re.compile(r"\s*(\d{4})")


class TagError(Exception):
    pass


@dataclass
class TagInfo:
    tags: dict[str, object] = field(default_factory=dict)
    duration_ms: int | None = None
    codec: str | None = None
    bitrate: int | None = None  # bits per second


def is_audio(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def _leading_int(value: str) -> int | None:
    # Handles "2019-04-01" for dates and "3/12" for track numbers.
    m = _LEADING_INT.match(value)
    return int(m.group(1)) if m else None


def _codec_name(audio: mutagen.FileType) -> str:
    name = type(audio).__name__.lower()
    for key, codec in (("mp3", "mp3"), ("mp4", "aac"), ("opus", "opus"),
                       ("flac", "flac"), ("vorbis", "vorbis"), ("wave", "pcm"),
                       ("wavpack", "wavpack")):
        if key in name:
            if codec == "aac":
                # MP4 containers can hold ALAC too.
                info_codec = getattr(audio.info, "codec", "") or ""
                return "alac" if info_codec.startswith("alac") else "aac"
            return codec
    return name.removeprefix("easy")


def read_tags(path: str | Path) -> TagInfo:
    try:
        audio = mutagen.File(str(path), easy=True)
    except mutagen.MutagenError as e:
        raise TagError(f"{path}: {e}") from e
    if audio is None:
        raise TagError(f"{path}: unrecognised audio format")

    tags: dict[str, object] = {}
    easy = audio.tags or {}
    for ours, key in EASY_KEYS.items():
        try:
            values = easy.get(key)
        except (KeyError, ValueError):
            values = None
        if not values:
            continue
        raw = str(values[0]).strip()
        if not raw:
            continue
        if ours == "year":
            # "2019", "2019-04-01" and yt-dlp's "20190401" all start with the year.
            m = _YEAR.match(raw)
            if m:
                tags[ours] = int(m.group(1))
        elif ours in ("track_no", "disc_no"):
            num = _leading_int(raw)
            if num is not None:
                tags[ours] = num
        else:
            tags[ours] = raw

    info = audio.info
    length = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None) or None
    return TagInfo(
        tags=tags,
        duration_ms=int(length * 1000) if length else None,
        codec=_codec_name(audio),
        bitrate=bitrate,
    )


def write_tags(path: str | Path, changes: dict[str, object]) -> None:
    """Write text fields. ``None`` or "" removes a field. Raises TagError.

    Callers update the database only after this returns, so file and DB
    never drift apart.
    """
    unknown = set(changes) - set(EASY_KEYS)
    if unknown:
        raise TagError(f"not a tag field: {sorted(unknown)}")
    try:
        audio = mutagen.File(str(path), easy=True)
        if audio is None:
            raise TagError(f"{path}: unrecognised audio format")
        if audio.tags is None:
            audio.add_tags()
        for ours, value in changes.items():
            key = EASY_KEYS[ours]
            if value is None or value == "":
                if key in audio.tags:
                    del audio.tags[key]
            else:
                audio.tags[key] = [str(value)]
        audio.save()
    except mutagen.MutagenError as e:
        raise TagError(f"{path}: {e}") from e


# --- cover art ------------------------------------------------------------
#
# Cover art isn't in mutagen's "easy" interface, so each container needs its
# own handling: ID3 APIC frames (MP3), the MP4 'covr' atom (M4A), FLAC
# picture blocks, and base64 METADATA_BLOCK_PICTURE comments (Ogg/Opus).

@dataclass
class Cover:
    data: bytes
    mime: str


def sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise TagError("cover must be a JPEG, PNG or WebP image")


def _open_raw(path: str | Path) -> mutagen.FileType:
    try:
        audio = mutagen.File(str(path))
    except mutagen.MutagenError as e:
        raise TagError(f"{path}: {e}") from e
    if audio is None:
        raise TagError(f"{path}: unrecognised audio format")
    return audio


def _flac_picture(cover: Cover) -> Picture:
    pic = Picture()
    pic.type = PictureType.COVER_FRONT
    pic.mime = cover.mime
    pic.data = cover.data
    return pic


def read_cover(path: str | Path) -> Cover | None:
    """Return the front cover (or first picture) embedded in the file."""
    audio = _open_raw(path)
    if isinstance(audio, MP4):
        covers = (audio.tags or {}).get("covr") or []
        if not covers:
            return None
        c = covers[0]
        mime = "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return Cover(bytes(c), mime)
    if isinstance(audio, FLAC):
        pics = audio.pictures
    elif isinstance(audio, (OggVorbis, OggOpus)):
        pics = []
        for b64 in (audio.tags or {}).get("metadata_block_picture", []):
            try:
                pics.append(Picture(base64.b64decode(b64)))
            except (ValueError, mutagen.MutagenError):
                continue
    elif audio.tags is not None and hasattr(audio.tags, "getall"):  # ID3
        pics = audio.tags.getall("APIC")
    else:
        return None
    if not pics:
        return None
    front = next((p for p in pics if p.type == PictureType.COVER_FRONT), pics[0])
    return Cover(bytes(front.data), front.mime or sniff_image_mime(front.data))


def write_cover(path: str | Path, cover: Cover | None) -> None:
    """Replace all embedded pictures with ``cover``; ``None`` removes them."""
    audio = _open_raw(path)
    try:
        if isinstance(audio, MP4):
            if audio.tags is None:
                audio.add_tags()
            if cover is None:
                audio.tags.pop("covr", None)
            else:
                if cover.mime not in ("image/jpeg", "image/png"):
                    raise TagError("M4A covers must be JPEG or PNG")
                fmt = MP4Cover.FORMAT_PNG if cover.mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio.tags["covr"] = [MP4Cover(cover.data, imageformat=fmt)]
        elif isinstance(audio, FLAC):
            audio.clear_pictures()
            if cover is not None:
                audio.add_picture(_flac_picture(cover))
        elif isinstance(audio, (OggVorbis, OggOpus)):
            if audio.tags is None:
                audio.add_tags()
            for key in ("metadata_block_picture", "coverart"):  # coverart: legacy
                if key in audio.tags:
                    del audio.tags[key]
            if cover is not None:
                block = base64.b64encode(_flac_picture(cover).write()).decode("ascii")
                audio.tags["metadata_block_picture"] = [block]
        else:
            # MP3 and anything else ID3-based.
            if audio.tags is None:
                try:
                    audio.add_tags()
                except mutagen.MutagenError:
                    pass
            if not isinstance(audio.tags, ID3):
                raise TagError(f"{path}: cover art not supported for this format")
            audio.tags.delall("APIC")
            if cover is not None:
                audio.tags.add(APIC(encoding=3, mime=cover.mime,
                                    type=PictureType.COVER_FRONT, desc="Cover",
                                    data=cover.data))
        audio.save()
    except (mutagen.MutagenError, ID3NoHeaderError) as e:
        raise TagError(f"{path}: {e}") from e
