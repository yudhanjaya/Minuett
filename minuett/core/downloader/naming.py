"""Turn YouTube video titles into artist / title tags.

Handles the common shapes:
  "Artist - Song (Official Video)"      -> Artist / Song
  "Artist – Song [Lyrics] | 4K"          -> Artist / Song
  "Song" uploaded by "Artist - Topic"    -> Artist / Song
  "Artist “Song”"                        -> Artist / Song
Featured-artist credits ("ft. X") are kept in the title, where most taggers
put them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bracketed chunks that are noise rather than part of the song name.
_NOISE_WORDS = (
    r"official\s*(music\s*|lyrics?\s*|audio\s*|hd\s*|4k\s*)?(video|audio|visuali[sz]er|mv|clip)?",
    r"(music|lyrics?|lyric|audio|hd|hq|4k|8k|mv|m/v|visuali[sz]er)\s*(video)?",
    r"video\s*(oficial|officiel|ufficiale)",
    r"(video|audio)\s*clip",
    r"remaster(ed)?(\s*\d{4})?", r"\d{4}\s*remaster(ed)?",
    r"explicit", r"clean", r"with\s*lyrics", r"full\s*(song|audio)",
    r"out\s*now", r"premiere", r"free\s*download",
)
_NOISE = re.compile(
    r"\s*[\(\[\{]\s*(?:" + "|".join(_NOISE_WORDS) + r")\s*[\)\]\}]", re.IGNORECASE)
# Trailing "| anything" or "// anything" segments ("| 4K", "| Vevo Live").
_TRAILING_PIPE = re.compile(r"\s+(\||//)\s+.*$")
_SEPARATORS = re.compile(r"\s+[-–—~]\s+|\s+[-–—]\s*|\s*[-–—]\s+")
_QUOTED = re.compile(r'^(?P<artist>[^"“”]+?)\s*[\"“](?P<title>[^"“”]+)[\"”]\s*$')
_TOPIC = re.compile(r"\s+-\s+Topic$", re.IGNORECASE)
_CHANNEL_NOISE = re.compile(r"(VEVO|\s*Official|\s*Music)$", re.IGNORECASE)


# Words that mark a trailing "[...]" tag as a genre label ("[lofi hip hop]",
# "[jazz lofi]", "[synthwave]") rather than part of the song name. Tags like
# "[Remix]" or "[Live]" don't match and stay in the title.
_GENRE_WORDS = (
    "lofi", "lo-fi", "hip hop", "hiphop", "jazz", "synthwave", "chillwave", "vaporwave",
    "chillhop", "ambient", "house", "techno", "trance", "edm", "dubstep", "drum and bass",
    "dnb", "trap", "phonk", "rock", "metal", "punk", "pop", "k-pop", "kpop", "j-pop", "r&b",
    "rnb", "soul", "funk", "disco", "reggae", "country", "folk", "blues", "classical",
    "piano", "orchestral", "soundtrack", "ost", "instrumental", "beats", "downtempo",
    "trip hop", "indie", "electronic", "chill", "sleep", "study", "relax", "meditation",
    "bossa nova", "latin", "afrobeat", "city pop", "shoegaze", "dream pop", "grunge",
)
_TRAILING_TAG = re.compile(r"\s*\[(?P<tag>[^\[\]]{2,40})\]\s*$")


@dataclass
class ParsedTitle:
    artist: str | None
    title: str
    genre: str | None = None


def split_genre_tag(title: str) -> tuple[str, str | None]:
    """"Late Bus 🌙 [lofi hip hop]" -> ("Late Bus 🌙", "Lofi Hip Hop")."""
    m = _TRAILING_TAG.search(title)
    if not m:
        return title, None
    tag = m["tag"].strip()
    low = tag.lower()
    if not any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low) for w in _GENRE_WORDS):
        return title, None
    small = {"and", "of", "the", "n", "&", "to"}
    words = tag.split()
    genre = " ".join(
        w if w.isupper() else (w.lower() if i and w.lower() in small else w[:1].upper() + w[1:])
        for i, w in enumerate(words))
    return title[:m.start()].rstrip(" -–—|"), genre


def clean_title(text: str) -> str:
    text = _TRAILING_PIPE.sub("", text)
    prev = None
    while prev != text:  # "(Official Video) [HD]" needs two passes
        prev = text
        text = _NOISE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -–—|")


def _channel_artist(uploader: str | None) -> str | None:
    if not uploader:
        return None
    if _TOPIC.search(uploader):
        return _TOPIC.sub("", uploader).strip()
    name = _CHANNEL_NOISE.sub("", uploader).strip()
    return name or None


def parse_title(video_title: str, uploader: str | None = None) -> ParsedTitle:
    """Best-effort artist/title split. Falls back to the channel name."""
    text = clean_title(video_title)

    text, genre = split_genre_tag(text)

    m = _QUOTED.match(text)
    if m:
        return ParsedTitle(m["artist"].strip(), m["title"].strip(), genre)

    parts = _SEPARATORS.split(text, maxsplit=1)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return ParsedTitle(parts[0].strip(), clean_title(parts[1]), genre)

    # "Artist - Topic" channels are YouTube Music auto-uploads: title is the song.
    return ParsedTitle(_channel_artist(uploader), text, genre)


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, limit: int = 120) -> str:
    """Filesystem-safe folder/file name, preserving readable punctuation."""
    name = _UNSAFE.sub("_", name).strip(" .")
    return (name[:limit].rstrip(" .") or "Untitled")
