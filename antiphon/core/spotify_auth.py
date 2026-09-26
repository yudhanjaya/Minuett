"""Spotify sign-in (Authorization Code with PKCE) and the few API calls we need.

Why sign in at all: since February 2026 Spotify's Web API returns a
playlist's tracks only for playlists you own or collaborate on, so signing in
lets Antiphon read *your* playlists in full (including private ones, beyond
the public page's 100-song limit, with album and ISRC for better matching).
Spotify audio is never downloaded; songs are still matched on YouTube Music.

Spotify requires each app to be registered. In development mode (all a
personal app gets) the app owner must have Premium and up to 5 allow-listed
users can sign in. The user registers a free app, adds our redirect URI, and
pastes its Client ID; PKCE needs no client secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from .accounts import SpotifyAccount

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
REDIRECT_PORT = 43819
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"


class SpotifyError(Exception):
    pass


class NotOwnPlaylist(SpotifyError):
    """403: the API only lists tracks of playlists you own or collaborate on."""


# --- PKCE ----------------------------------------------------------------------

def make_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, verifier: str, state: str) -> str:
    q = {"client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
         "code_challenge_method": "S256", "code_challenge": challenge_for(verifier),
         "state": state, "scope": SCOPES}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


# --- the redirect lands here -------------------------------------------------------

_DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Antiphon</title>
<body style="font:16px system-ui;margin:4em;color:#222">
<h2>{title}</h2><p>{body}</p><p>You can close this tab and return to Antiphon.</p></body>"""


class LoopbackReceiver:
    """One-shot HTTP server on 127.0.0.1 that catches Spotify's redirect."""

    def __init__(self, state: str, port: int = REDIRECT_PORT) -> None:
        self.state = state
        self.code: str | None = None
        self.error: str | None = None
        self._got = threading.Event()
        self._closed = False
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ok = False
                if urllib.parse.urlparse(self.path).path != "/callback":
                    self.send_error(404)
                    return
                if q.get("state", [None])[0] != receiver.state:
                    receiver.error = "The sign-in response didn't match this request."
                elif "error" in q:
                    receiver.error = {"access_denied": "Spotify sign-in was cancelled."}.get(
                        q["error"][0], f"Spotify said: {q['error'][0]}")
                else:
                    receiver.code = q.get("code", [None])[0]
                    ok = receiver.code is not None
                page = _DONE_PAGE.format(
                    title="Connected to Spotify" if ok else "Spotify sign-in didn't finish",
                    body="Antiphon can now read your playlists." if ok else (receiver.error or ""))
                data = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                receiver._got.set()

            def log_message(self, *args):
                pass

        try:
            self._server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            raise SpotifyError(f"Couldn't listen on 127.0.0.1:{port} for Spotify's reply: {e}") from e
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = 300) -> str:
        """Block until the redirect arrives; return the authorization code."""
        got = self._got.wait(timeout)
        cancelled = self._closed and self.code is None and self.error is None
        self.close()
        if cancelled:
            raise SpotifyError("Sign-in cancelled.")
        if not got:
            raise SpotifyError("Timed out waiting for Spotify sign-in.")
        if self.error or not self.code:
            raise SpotifyError(self.error or "Spotify didn't send a sign-in code.")
        return self.code

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._got.set()   # release a waiting thread
        self._server.shutdown()
        self._server.server_close()


# --- HTTP ------------------------------------------------------------------------

Fetch = Callable[[urllib.request.Request], tuple[int, dict]]


def _urllib_fetch(req: urllib.request.Request) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        raise SpotifyError(f"Couldn't reach Spotify: {e}") from e


def _token_request(fetch: Fetch, form: dict) -> dict:
    req = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    status, data = fetch(req)
    if status != 200 or "access_token" not in data:
        raise SpotifyError(data.get("error_description") or data.get("error")
                           or f"Spotify sign-in failed ({status}).")
    return data


def _apply_tokens(acct: SpotifyAccount, data: dict, now: float) -> None:
    acct.access_token = data["access_token"]
    acct.expires_at = now + float(data.get("expires_in", 3600)) - 60
    if data.get("refresh_token"):  # Spotify may or may not rotate it
        acct.refresh_token = data["refresh_token"]


def exchange_code(acct: SpotifyAccount, code: str, verifier: str,
                  fetch: Fetch = _urllib_fetch, now: float | None = None) -> None:
    data = _token_request(fetch, {"grant_type": "authorization_code", "code": code,
                                  "redirect_uri": REDIRECT_URI, "client_id": acct.client_id,
                                  "code_verifier": verifier})
    _apply_tokens(acct, data, now if now is not None else time.time())


# --- API client ---------------------------------------------------------------------

@dataclass
class SpotifyPlaylist:
    id: str
    name: str
    owner: str | None
    tracks: int | None
    owned: bool


class SpotifyClient:
    """Calls the Web API with ``acct``'s tokens, refreshing them when needed.
    ``on_tokens`` is told whenever tokens change, so the caller can save them."""

    def __init__(self, acct: SpotifyAccount, fetch: Fetch = _urllib_fetch,
                 on_tokens: Callable[[SpotifyAccount], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if not acct.connected:
            raise SpotifyError("Not connected to Spotify.")
        self.acct = acct
        self.fetch = fetch
        self.on_tokens = on_tokens
        self.clock = clock

    def _token(self) -> str:
        if not self.acct.access_token or self.clock() >= self.acct.expires_at:
            data = _token_request(self.fetch, {"grant_type": "refresh_token",
                                               "refresh_token": self.acct.refresh_token,
                                               "client_id": self.acct.client_id})
            _apply_tokens(self.acct, data, self.clock())
            if self.on_tokens:
                self.on_tokens(self.acct)
        return self.acct.access_token  # type: ignore[return-value]

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token()}"})
        status, data = self.fetch(req)
        if status == 401:  # token revoked or expired early: refresh once
            self.acct.access_token = None
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token()}"})
            status, data = self.fetch(req)
        if status == 403:
            raise NotOwnPlaylist(data.get("error", {}).get("message", "Forbidden"))
        if status != 200:
            msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
            raise SpotifyError(msg or f"Spotify API error {status}")
        return data

    def me(self) -> dict:
        return self.get("/me")

    def my_playlists(self) -> list[SpotifyPlaylist]:
        me = self.acct.user_id or self.me().get("id")
        out, url = [], "/me/playlists?limit=50"
        while url:
            page = self.get(url)
            for p in page.get("items") or []:
                if not p:
                    continue
                owner = (p.get("owner") or {}).get("id")
                total = (p.get("items") or p.get("tracks") or {}).get("total")
                out.append(SpotifyPlaylist(p["id"], p.get("name") or "Untitled",
                                           (p.get("owner") or {}).get("display_name"),
                                           total, owner == me or bool(p.get("collaborative"))))
            url = page.get("next")
        return out

    def playlist(self, playlist_id: str) -> dict:
        return self.get(f"/playlists/{playlist_id}", {"fields": "id,name,owner(display_name,id)"})

    def playlist_items(self, playlist_id: str) -> list[dict]:
        """Every track in a playlist you own or collaborate on (403 otherwise)."""
        out, url = [], f"/playlists/{playlist_id}/items?limit=50&additional_types=track"
        while url:
            page = self.get(url)
            for it in page.get("items") or []:
                track = it.get("item") or it.get("track")
                if track and track.get("type", "track") == "track" and not track.get("is_local"):
                    out.append(track)
            url = page.get("next")
        return out
