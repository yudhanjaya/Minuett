"""Find the YouTube Music recording of a track known only by its metadata.

Used for Spotify and Pandora playlists (whose own audio is DRM-protected):
search YouTube Music's song results for "artist title", confirm the best
candidates by fetching their details, and score title, artist and length.
If YouTube Music has nothing convincing, fall back to a regular YouTube
search. A candidate must clear MIN_SCORE; otherwise the track is reported
as not found rather than downloading the wrong song.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from .naming import parse_title

MIN_SCORE = 0.62
CONFIDENT = 0.9
_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|hd|4k|mv)[^)\]]*[\)\]]",
    re.IGNORECASE)
_FEAT = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|with)\s+[^)\]]*[\)\]]?", re.IGNORECASE)


@dataclass
class Target:
    title: str
    artist: str | None = None
    album: str | None = None
    duration: float | None = None  # seconds


@dataclass
class Candidate:
    video_id: str
    title: str
    artist: str | None = None
    duration: float | None = None
    source: str = "youtube-music"


@dataclass
class Match:
    candidate: Candidate
    score: float


def normalise(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _NOISE.sub(" ", text)
    text = _FEAT.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str | None, b: str | None) -> float:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(ratio, overlap * 0.95)


def _artists(artist: str | None) -> list[str]:
    return [a.strip() for a in re.split(r",|&| x | and |;|/", artist or "") if a.strip()]


def score(target: Target, cand: Candidate) -> float:
    title_s = similarity(target.title, cand.title)
    names = _artists(target.artist)
    if names and cand.artist:
        artist_s = max(similarity(n, cand.artist) for n in names)
    elif names:
        # No artist field (plain YouTube): look for the name in the video title.
        hay = normalise(cand.title)
        artist_s = 0.9 if any(normalise(n) and normalise(n) in hay for n in names) else 0.3
    else:
        artist_s = 0.5
    if target.duration and cand.duration:
        diff = abs(target.duration - cand.duration)
        dur_s = 1.0 if diff <= 3 else 0.8 if diff <= 8 else 0.4 if diff <= 15 else 0.0
    else:
        dur_s = 0.6
    total = 0.5 * title_s + 0.3 * artist_s + 0.2 * dur_s
    if target.duration and cand.duration and abs(target.duration - cand.duration) > 30:
        total = min(total, 0.4)  # a different edit or a video with a long intro
    return total


class Matcher:
    """``search`` and ``resolve`` are injectable so tests don't need the network."""

    def __init__(self, ydl_opts: dict | None = None,
                 search: Callable[[str, str, int], list[Candidate]] | None = None,
                 resolve: Callable[[str], Candidate | None] | None = None) -> None:
        self.ydl_opts = ydl_opts or {}
        self._search = search or self._yt_search
        self._resolve = resolve or self._yt_resolve

    def find(self, target: Target) -> Match | None:
        query = " ".join(x for x in (target.artist, target.title) if x)
        best: Match | None = None
        # YouTube Music songs: flat results carry only titles, so shortlist by
        # title and confirm the top two with their full details.
        songs = self._search("ytmusic", query, 6)
        songs.sort(key=lambda c: similarity(target.title, c.title), reverse=True)
        for cand in songs[:2]:
            full = self._resolve(cand.video_id) or cand
            m = Match(full, score(target, full))
            if best is None or m.score > best.score:
                best = m
            if best.score >= CONFIDENT:
                break  # clear match; skip confirming the runner-up
        if best and best.score >= MIN_SCORE:
            return best
        for cand in self._search("youtube", f"{query} audio", 5):
            m = Match(cand, score(target, cand))
            if best is None or m.score > best.score:
                best = m
        return best if best and best.score >= MIN_SCORE else None

    # --- yt-dlp backed search -------------------------------------------

    def _yt_search(self, where: str, query: str, n: int) -> list[Candidate]:
        import yt_dlp
        url = (f"https://music.youtube.com/search?q={quote(query)}#songs" if where == "ytmusic"
               else f"ytsearch{n}:{query}")
        opts = {**self.ydl_opts, "quiet": True, "extract_flat": True, "skip_download": True,
                "playlistend": n}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except yt_dlp.utils.DownloadError:
            return []
        out = []
        for e in (info.get("entries") or [])[:n]:
            if not e or not e.get("id") or e.get("live_status") == "is_live":
                continue
            title = e.get("title") or ""
            artist = e.get("channel") or e.get("uploader")
            if where == "youtube":
                parsed = parse_title(title, artist)
                title, artist = parsed.title, parsed.artist or artist
            out.append(Candidate(e["id"], title, artist, e.get("duration"),
                                 "youtube-music" if where == "ytmusic" else "youtube"))
        return out

    def _yt_resolve(self, video_id: str) -> Candidate | None:
        import yt_dlp
        opts = {**self.ydl_opts, "quiet": True, "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}",
                                        download=False) or {}
        except yt_dlp.utils.DownloadError:
            return None
        artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
        return Candidate(video_id, info.get("track") or info.get("title") or "",
                         ", ".join(artists) if artists else (info.get("channel") or None),
                         info.get("duration"))
