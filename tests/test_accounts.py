"""Optional sign-ins: account storage, YouTube cookie options, Spotify PKCE + API."""

import json
import os
import stat
import threading
import urllib.request
from pathlib import Path

import pytest

from antiphon.core import accounts
from antiphon.core.accounts import (
    Accounts, SpotifyAccount, YouTubeAccount, installed_browsers, ytdlp_cookie_options,
)
from antiphon.core.spotify_auth import (
    LoopbackReceiver, NotOwnPlaylist, SpotifyClient, SpotifyError, authorize_url, challenge_for,
    exchange_code,
)


# --- storage ----------------------------------------------------------------------

def test_accounts_roundtrip_is_private(tmp_path):
    p = tmp_path / "accounts.json"
    a = Accounts(YouTubeAccount(method="browser", browser="brave", premium=True, signed_in=True),
                 SpotifyAccount(client_id="cid", refresh_token="rt", user_name="me"))
    accounts.save(a, p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    b = accounts.load(p)
    assert b == a and b.youtube.configured and b.spotify.connected
    assert accounts.load(tmp_path / "missing.json") == Accounts()


def test_cookie_file_copy_validated_and_private(tmp_path):
    good = tmp_path / "c.txt"
    good.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n")
    dest = accounts.store_cookie_file(good, tmp_path / "copy.txt")
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600
    bad = tmp_path / "b.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        accounts.store_cookie_file(bad, tmp_path / "x.txt")


def test_ytdlp_cookie_options(tmp_path):
    assert ytdlp_cookie_options(YouTubeAccount()) == {}
    assert ytdlp_cookie_options(YouTubeAccount(method="browser", browser="brave", profile="/p")) == \
        {"cookiesfrombrowser": ("brave", "/p", None, None)}
    f = tmp_path / "c.txt"
    f.write_text("x")
    assert ytdlp_cookie_options(YouTubeAccount(method="file", cookie_file=str(f))) == {"cookiefile": str(f)}
    # A vanished cookies file means signed out, not a crash.
    assert ytdlp_cookie_options(YouTubeAccount(method="file", cookie_file=str(tmp_path / "gone"))) == {}


def test_base_options_carry_the_sign_in(monkeypatch, tmp_path):
    from antiphon.core.downloader.playlist import base_options
    monkeypatch.setattr(accounts, "accounts_path", lambda: tmp_path / "a.json")
    assert "cookiesfrombrowser" not in base_options()
    accounts.save(Accounts(YouTubeAccount(method="browser", browser="firefox")))
    assert base_options()["cookiesfrombrowser"][0] == "firefox"
    assert "cookiesfrombrowser" not in base_options(signed_in=False)


def test_installed_browsers_finds_standard_and_flatpak(tmp_path):
    (tmp_path / ".config/BraveSoftware/Brave-Browser").mkdir(parents=True)
    (tmp_path / ".var/app/org.mozilla.firefox/.mozilla/firefox").mkdir(parents=True)
    found = {b.key: b for b in installed_browsers(tmp_path, sandboxed=False)}
    assert found["brave"].label == "Brave" and found["brave"].visible
    assert found["brave"].grant.endswith("Brave-Browser/Default/Network")
    assert found["firefox"].label == "Firefox (Flatpak)"
    assert "chrome" not in found   # outside the sandbox, only what's installed


def test_sandbox_lists_every_browser_until_granted(tmp_path):
    found = {b.key: b for b in installed_browsers(tmp_path, sandboxed=True)}
    assert set(found) == set(accounts.BROWSERS) and not any(b.visible for b in found.values())
    assert found["brave"].grant == str(tmp_path / ".config/BraveSoftware/Brave-Browser/Default/Network")
    # After the user grants just the cookie folder, it's detected and used as the profile.
    (tmp_path / ".config/BraveSoftware/Brave-Browser/Default/Network").mkdir(parents=True)
    brave = {b.key: b for b in installed_browsers(tmp_path, sandboxed=True)}["brave"]
    assert brave.visible and brave.profile == brave.grant
    assert accounts.other_install_grants("brave", tmp_path) == [
        str(tmp_path / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Network")]


def test_flatpak_override_hint():
    hint = accounts.flatpak_override_hint(str(Path.home() / ".config/BraveSoftware/Brave-Browser/Default/Network"))
    assert hint.startswith("flatpak override --user --filesystem="
                           "~/.config/BraveSoftware/Brave-Browser/Default/Network:ro")
    assert "org.freedesktop.secrets" in hint


def test_check_youtube_without_sign_in_makes_no_request():
    status = accounts.check_youtube(YouTubeAccount())
    assert (status.signed_in, status.premium) == (False, False)


# --- Spotify: PKCE and the redirect --------------------------------------------------

def test_pkce_challenge_matches_rfc7636_example():
    assert challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"   # RFC 7636, appendix B


def test_authorize_url_has_pkce_and_loopback_redirect():
    url = authorize_url("cid", "verifier", "st8")
    assert "code_challenge_method=S256" in url and "state=st8" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A" in url
    assert "playlist-read-private" in url and "client_secret" not in url


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_loopback_receiver_gets_code_and_checks_state():
    port = _free_port()
    r = LoopbackReceiver("good", port)
    threading.Thread(target=lambda: urllib.request.urlopen(
        f"http://127.0.0.1:{port}/callback?code=abc&state=good").read(), daemon=True).start()
    assert r.wait(5) == "abc"

    port = _free_port()
    r = LoopbackReceiver("good", port)
    threading.Thread(target=lambda: urllib.request.urlopen(
        f"http://127.0.0.1:{port}/callback?code=abc&state=evil").read(), daemon=True).start()
    with pytest.raises(SpotifyError, match="didn't match"):
        r.wait(5)


def test_loopback_receiver_cancel_and_denied():
    port = _free_port()
    r = LoopbackReceiver("s", port)
    threading.Timer(0.2, r.close).start()
    with pytest.raises(SpotifyError, match="cancelled"):
        r.wait(5)
    port = _free_port()
    r = LoopbackReceiver("s", port)
    threading.Thread(target=lambda: urllib.request.urlopen(
        f"http://127.0.0.1:{port}/callback?error=access_denied&state=s").read(), daemon=True).start()
    with pytest.raises(SpotifyError, match="cancelled"):
        r.wait(5)


def test_port_in_use_is_a_clear_error():
    port = _free_port()
    first = LoopbackReceiver("a", port)
    try:
        with pytest.raises(SpotifyError, match="Couldn't listen"):
            LoopbackReceiver("b", port)
    finally:
        first.close()


# --- Spotify: tokens and API ------------------------------------------------------------

class FakeSpotify:
    """Stands in for accounts.spotify.com and api.spotify.com."""

    def __init__(self, owner="me", tracks=120):
        self.calls = []
        self.owner = owner
        self.tracks = [{"uri": f"spotify:track:{i:022d}", "name": f"Song {i}", "type": "track",
                        "artists": [{"name": "A"}, {"name": "B"}], "album": {"name": "LP"},
                        "duration_ms": 200000 + i, "external_ids": {"isrc": f"ISRC{i}"}}
                       for i in range(tracks)]

    def __call__(self, req):
        url = req.full_url
        self.calls.append((req.get_method(), url))
        if url.endswith("/api/token"):
            form = dict(p.split("=") for p in req.data.decode().split("&"))
            if form["grant_type"] in ("authorization_code", "refresh_token"):
                return 200, {"access_token": f"tok{len(self.calls)}", "expires_in": 3600,
                             "refresh_token": "rt2"}
        auth = req.get_header("Authorization") or ""
        assert auth.startswith("Bearer tok")
        if "/me/playlists" in url:
            return 200, {"items": [{"id": "P1", "name": "Mine", "owner": {"id": "me", "display_name": "Me"},
                                    "items": {"total": 120}},
                                   {"id": "P2", "name": "Theirs", "owner": {"id": "x", "display_name": "X"},
                                    "items": {"total": 50}}], "next": None}
        if url.endswith("/me"):
            return 200, {"id": "me", "display_name": "Me"}
        if "/items" in url:
            if self.owner != "me":
                return 403, {"error": {"status": 403, "message": "Forbidden"}}
            off = int(url.split("offset=")[1].split("&")[0]) if "offset=" in url else 0
            page = self.tracks[off:off + 50]
            nxt = (f"https://api.spotify.com/v1/playlists/P1/items?offset={off + 50}&limit=50"
                   if off + 50 < len(self.tracks) else None)
            return 200, {"items": [{"item": t} for t in page], "next": nxt, "total": len(self.tracks)}
        if "/playlists/" in url:
            return 200, {"id": "P1", "name": "Road Trip", "owner": {"display_name": "Me", "id": "me"}}
        return 404, {}


def test_exchange_code_and_refresh_when_expired():
    fake = FakeSpotify()
    acct = SpotifyAccount(client_id="cid")
    exchange_code(acct, "code", "verifier", fetch=fake, now=1000)
    assert acct.connected and acct.expires_at == 1000 + 3600 - 60
    saved = []
    now = [1000.0]
    client = SpotifyClient(acct, fetch=fake, on_tokens=saved.append, clock=lambda: now[0])
    client.me()
    assert saved == []                       # token still fresh
    now[0] += 4000
    client.me()
    assert saved and acct.refresh_token == "rt2"


def test_playlists_pagination_and_listing():
    from antiphon.core.downloader.sources.spotify import listing_from_api
    fake = FakeSpotify()
    client = SpotifyClient(SpotifyAccount(client_id="c", refresh_token="r", user_id="me"),
                           fetch=fake, clock=lambda: 0)
    mine = client.my_playlists()
    assert [(p.name, p.owned, p.tracks) for p in mine] == [("Mine", True, 120), ("Theirs", False, 50)]
    tracks = client.playlist_items("P1")
    assert len(tracks) == 120                 # three pages of 50
    lst = listing_from_api("P1", client.playlist("P1"), tracks)
    e = lst.entries[0]
    assert (lst.title, lst.truncated, len(lst.entries)) == ("Road Trip", False, 120)
    assert (e.title, e.artist, e.album, e.isrc, e.needs_match) == ("Song 0", "A, B", "LP", "ISRC0", True)


def test_someone_elses_playlist_is_403():
    client = SpotifyClient(SpotifyAccount(client_id="c", refresh_token="r"),
                           fetch=FakeSpotify(owner="x"), clock=lambda: 0)
    with pytest.raises(NotOwnPlaylist):
        client.playlist_items("P2")


def test_spotify_link_uses_api_when_connected_and_falls_back(monkeypatch, tmp_path):
    from antiphon.core.downloader.sources import spotify
    monkeypatch.setattr(accounts, "accounts_path", lambda: tmp_path / "a.json")
    accounts.save(Accounts(spotify=SpotifyAccount(client_id="c", refresh_token="r")))
    fake = FakeSpotify()
    monkeypatch.setattr("antiphon.core.spotify_auth._urllib_fetch", fake)
    monkeypatch.setattr("antiphon.core.spotify_auth.SpotifyClient.__init__.__defaults__",
                        (fake, None, __import__("time").time))
    lst = spotify._api_listing("P1")
    assert lst is not None and len(lst.entries) == 120 and not lst.truncated
    assert json.loads((tmp_path / "a.json").read_text())["spotify"]["access_token"]  # tokens saved
    fake.owner = "x"
    assert spotify._api_listing("P1") is None   # not yours: use the public page instead


# --- UI -----------------------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_accounts_panel_and_dialogs_open(app, monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "accounts_path", lambda: tmp_path / "a.json")
    from antiphon.ui.dialogs.accounts import SpotifyConnectDialog, YouTubeSignInDialog
    from antiphon.ui.views.accounts_panel import AccountsPanel
    panel = AccountsPanel()
    assert panel.yt_btn.text() == "Sign In" and panel.sp_btn.text() == "Connect"
    accounts.save(Accounts(YouTubeAccount(method="browser", browser="brave", signed_in=True, premium=True),
                           SpotifyAccount(client_id="c", refresh_token="r", user_name="Me")))
    panel.refresh()
    assert "Premium" in panel.yt_status.text() and "Me" in panel.sp_status.text()
    yt = YouTubeSignInDialog()        # restores the saved choice without crashing
    assert yt.use_browser.isChecked()
    sp = SpotifyConnectDialog()
    assert sp.client_id.text() == "c" and sp.disconnect_btn.isVisibleTo(sp)
    sp._disconnect()
    assert not accounts.load().spotify.connected and accounts.load().spotify.client_id == "c"
