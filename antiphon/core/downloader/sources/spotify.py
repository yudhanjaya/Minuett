"""Read a Spotify playlist or album's track list from its public embed page.

Spotify's Web API (since February 2026) only returns the tracks of playlists
the signed-in user owns, so for pasted links we read the same public page
that websites embed. That page lists at most 100 tracks and carries title,
artists and duration (no album or ISRC); anything longer should be imported
from an export file instead. It's an unofficial source and may change.

Spotify's audio itself is DRM-protected and never downloaded: each track is
matched on YouTube Music (see matching.py).
"""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlparse

from ..playlist import Listing, ListingError, RemoteEntry

EMBED_LIMIT = 100
_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/148.0 Safari/537.36")


def parse_spotify_url(url: str) -> tuple[str, str]:
    """-> (kind, id) for playlist/album links and spotify: URIs."""
    url = url.strip()
    if url.startswith("spotify:"):
        parts = url.split(":")
        if len(parts) >= 3 and parts[1] in ("playlist", "album") and _ID.match(parts[2]):
            return parts[1], parts[2]
    else:
        path = [p for p in urlparse(url).path.split("/") if p and not p.startswith("intl-")]
        for i, p in enumerate(path[:-1]):
            if p in ("playlist", "album") and _ID.match(path[i + 1]):
                return p, path[i + 1]
    raise ListingError("That Spotify link isn't a playlist or album.")


def canonical_url(kind: str, sid: str) -> str:
    return f"https://open.spotify.com/{kind}/{sid}"


def listing_from_embed(html: str, kind: str, sid: str) -> Listing:
    m = _NEXT_DATA.search(html)
    if not m:
        raise ListingError("Spotify's page didn't include a track list (it may be private).")
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, ValueError) as e:
        raise ListingError("Spotify's page has changed; import an export file instead.") from e
    entries = []
    for t in entity.get("trackList") or []:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue  # podcast episodes and local files
        track_id = uri.rsplit(":", 1)[-1]
        entries.append(RemoteEntry(
            position=len(entries),
            item_id=uri,
            title=t.get("title"),
            artist=(t.get("subtitle") or "").replace(" ", " ").strip() or None,
            album=entity.get("name") if kind == "album" else None,
            duration=(t.get("duration") or 0) / 1000 or None,
            url=f"https://open.spotify.com/track/{track_id}",
            needs_match=True,
        ))
    title = entity.get("name") or entity.get("title") or "Spotify playlist"
    owner = entity.get("subtitle")
    return Listing(url=canonical_url(kind, sid), playlist_id=sid, title=title, uploader=owner,
                   entries=entries, source="spotify", truncated=len(entries) >= EMBED_LIMIT)


def listing_from_api(sid: str, meta: dict, tracks: list[dict]) -> Listing:
    """A full listing from the Web API (your own/collaborative playlists)."""
    entries = []
    for t in tracks:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue
        entries.append(RemoteEntry(
            position=len(entries), item_id=uri, title=t.get("name"),
            artist=", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name")) or None,
            album=(t.get("album") or {}).get("name"),
            isrc=(t.get("external_ids") or {}).get("isrc"),
            duration=(t.get("duration_ms") or 0) / 1000 or None,
            url=f"https://open.spotify.com/track/{uri.rsplit(':', 1)[-1]}",
            needs_match=True))
    return Listing(url=canonical_url("playlist", sid), playlist_id=sid,
                   title=meta.get("name") or "Spotify playlist",
                   uploader=(meta.get("owner") or {}).get("display_name"),
                   entries=entries, source="spotify", truncated=False)


def _api_listing(sid: str) -> Listing | None:
    """Full listing through the signed-in API, or None to use the public page."""
    from antiphon.core import accounts
    from antiphon.core.spotify_auth import NotOwnPlaylist, SpotifyClient, SpotifyError
    accts = accounts.load()
    if not accts.spotify.connected:
        return None

    def save_tokens(acct):
        accts.spotify = acct
        accounts.save(accts)
    try:
        client = SpotifyClient(accts.spotify, on_tokens=save_tokens)
        return listing_from_api(sid, client.playlist(sid), client.playlist_items(sid))
    except NotOwnPlaylist:
        return None  # someone else's playlist: the API won't list it
    except SpotifyError:
        return None  # fall back rather than fail the import


def fetch_spotify_listing(url: str, timeout: float = 20) -> Listing:
    kind, sid = parse_spotify_url(url)
    if kind == "playlist":
        listing = _api_listing(sid)
        if listing is not None:
            return listing
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}",
                                 headers={"User-Agent": _UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except OSError as e:
        raise ListingError(f"Couldn't reach Spotify: {e}") from e
    return listing_from_embed(html, kind, sid)
