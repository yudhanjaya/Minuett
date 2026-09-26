"""Tag reading and writing via mutagen, normalised across formats.

``mutagen.File(path, easy=True)`` gives a uniform dict-like interface for
MP3 (EasyID3), M4A (EasyMP4), FLAC, Ogg Vorbis and Opus, which covers the
text fields. Cover art is format-specific and lives in later phases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import mutagen

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
        if ours in ("year", "track_no", "disc_no"):
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
