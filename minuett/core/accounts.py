"""Optional sign-ins: YouTube Music (for Premium's higher-bitrate audio) and
Spotify (to read your own playlists in full). Qt-free.

Stored in ``~/.config/minuett/accounts.json``, readable only by you (0600).
Minuett never sees a password: YouTube sign-in reuses the session of a
browser you're already signed into (or a cookies file you export), and
Spotify uses its official sign-in page.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import config_dir

log = logging.getLogger(__name__)

# yt-dlp browser key -> (label, profile directories relative to $HOME).
# Standard, Flatpak and Snap installs, in that order.
BROWSERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "firefox": ("Firefox", (".mozilla/firefox", ".var/app/org.mozilla.firefox/.mozilla/firefox",
                            "snap/firefox/common/.mozilla/firefox")),
    "chrome": ("Google Chrome", (".config/google-chrome", ".var/app/com.google.Chrome/config/google-chrome")),
    "chromium": ("Chromium", (".config/chromium", ".var/app/org.chromium.Chromium/config/chromium",
                              "snap/chromium/common/chromium")),
    "brave": ("Brave", (".config/BraveSoftware/Brave-Browser",
                        ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser")),
    "edge": ("Microsoft Edge", (".config/microsoft-edge", ".var/app/com.microsoft.Edge/config/microsoft-edge")),
    "vivaldi": ("Vivaldi", (".config/vivaldi", ".var/app/com.vivaldi.Vivaldi/config/vivaldi")),
    "opera": ("Opera", (".config/opera", ".var/app/com.opera.Opera/config/opera")),
}


@dataclass
class YouTubeAccount:
    method: str | None = None          # "browser", "file" or None (signed out)
    browser: str | None = None         # key of BROWSERS
    profile: str | None = None         # profile directory (non-default installs)
    cookie_file: str | None = None     # our private copy of an exported cookies.txt
    premium: bool | None = None        # from the last check
    signed_in: bool | None = None
    checked_at: str | None = None

    @property
    def configured(self) -> bool:
        return self.method in ("browser", "file")


@dataclass
class SpotifyAccount:
    client_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float = 0.0
    user_id: str | None = None
    user_name: str | None = None

    @property
    def connected(self) -> bool:
        return bool(self.client_id and self.refresh_token)


@dataclass
class Accounts:
    youtube: YouTubeAccount = field(default_factory=YouTubeAccount)
    spotify: SpotifyAccount = field(default_factory=SpotifyAccount)


def accounts_path() -> Path:
    return config_dir() / "accounts.json"


def cookie_copy_path() -> Path:
    return config_dir() / "youtube-cookies.txt"


def load(path: Path | None = None) -> Accounts:
    path = path or accounts_path()
    try:
        data = json.loads(path.read_text())
        return Accounts(YouTubeAccount(**data.get("youtube", {})),
                        SpotifyAccount(**data.get("spotify", {})))
    except (OSError, ValueError, TypeError):
        return Accounts()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def save(accounts: Accounts, path: Path | None = None) -> None:
    _write_private(path or accounts_path(), json.dumps(asdict(accounts), indent=2))


def store_cookie_file(source: Path, dest: Path | None = None) -> Path:
    """Keep a private copy of an exported cookies.txt (the original may be
    left in Downloads). Raises ValueError if it isn't a Netscape cookie file
    with YouTube cookies."""
    text = Path(source).read_text(errors="replace")
    if "youtube.com" not in text or "\t" not in text:
        raise ValueError("That doesn't look like a cookies.txt export containing YouTube cookies.")
    dest = dest or cookie_copy_path()
    _write_private(dest, text)
    return dest


CHROMIUM_FAMILY = {"chrome", "chromium", "brave", "edge", "vivaldi", "opera"}


@dataclass
class BrowserInfo:
    key: str
    label: str
    profile: str          # what yt-dlp is pointed at
    visible: bool         # can we read it from here (matters inside the Flatpak)
    grant: str            # the narrowest directory the Flatpak needs to read

    @property
    def variant(self) -> str:
        return "Flatpak" if "/.var/app/" in self.grant else "Snap" if "/snap/" in self.grant else ""


def _cookie_dir(key: str, profile_dir: Path) -> Path:
    """Chromium keeps cookies in <profile>/Default/Network; that folder is all
    yt-dlp needs on Linux (the key comes from the keyring), so it's all we ask
    the Flatpak to expose. Firefox keeps them in its profiles folder."""
    return profile_dir / "Default" / "Network" if key in CHROMIUM_FAMILY else profile_dir


def installed_browsers(home: Path | None = None, sandboxed: bool | None = None) -> list[BrowserInfo]:
    """Supported browsers with a readable profile.

    Inside the Flatpak the profiles aren't visible until the user grants
    access, so there every supported browser is listed (not visible) with the
    folder to grant; after granting, the narrow cookie folder is detected.
    """
    home = home or Path.home()
    sandboxed = in_flatpak() if sandboxed is None else sandboxed
    found: list[BrowserInfo] = []
    for key, (label, dirs) in BROWSERS.items():
        hit = None
        for d in dirs:
            base = home / d
            cookies = _cookie_dir(key, base)
            if cookies.is_dir():            # granted narrowly (or a normal install)
                hit = BrowserInfo(key, label, str(cookies if sandboxed else base), True, str(cookies))
                break
            if base.is_dir():
                hit = BrowserInfo(key, label, str(base), True, str(cookies))
                break
        if hit:
            if hit.variant:
                hit.label += f" ({hit.variant})"
            found.append(hit)
        elif sandboxed:
            # Not visible from inside the sandbox yet: offer it with the standard location.
            std = home / dirs[0]
            found.append(BrowserInfo(key, label, str(_cookie_dir(key, std)), False,
                                     str(_cookie_dir(key, std))))
    return found


def other_install_grants(key: str, home: Path | None = None) -> list[str]:
    """Cookie folders for a browser's Flatpak/Snap installs (for the hint)."""
    home = home or Path.home()
    return [str(_cookie_dir(key, home / d)) for d in BROWSERS[key][1][1:]]


def ytdlp_cookie_options(acct: YouTubeAccount) -> dict:
    """yt-dlp options that sign requests in, or {} when signed out."""
    if acct.method == "file" and acct.cookie_file and Path(acct.cookie_file).exists():
        return {"cookiefile": acct.cookie_file}
    if acct.method == "browser" and acct.browser in BROWSERS:
        # (browser, profile, keyring, container). A profile path lets yt-dlp
        # find Flatpak/Snap installs it wouldn't look for by default.
        return {"cookiesfrombrowser": (acct.browser, acct.profile, None, None)}
    return {}


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def flatpak_override_hint(grant_dir: str, app_id: str = "io.github.yudhanjaya.Minuett") -> str:
    """The command that lets the Flatpak read a browser's cookie folder (read-
    only) and the keyring holding Chromium browsers' cookie key."""
    home = str(Path.home())
    rel = grant_dir.replace(home, "~", 1)
    return (f"flatpak override --user --filesystem={rel}:ro "
            f"--talk-name=org.freedesktop.secrets {app_id}")


# --- checking a YouTube sign-in --------------------------------------------------

PREMIUM_MARKER = "Detected YouTube Premium subscription"
CHECK_VIDEO = "https://music.youtube.com/watch?v=h-zuG75Ds-w"


@dataclass
class YouTubeStatus:
    signed_in: bool
    premium: bool
    message: str


class _Capture:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, msg: str) -> None:
        self.lines.append(msg)

    info = warning = error = debug


def check_youtube(acct: YouTubeAccount, base_opts: dict | None = None) -> YouTubeStatus:
    """Sign in to YouTube Music as yt-dlp would and report what we got.

    Makes one metadata request; nothing is downloaded.
    """
    import yt_dlp
    opts = ytdlp_cookie_options(acct)
    if not opts:
        return YouTubeStatus(False, False, "Not signed in.")
    capture = _Capture()
    params = {**(base_opts or {}), **opts, "quiet": False, "verbose": True,
              "logger": capture, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            ydl.extract_info(CHECK_VIDEO, download=False)
            names = {c.name for c in ydl.cookiejar if "youtube.com" in c.domain}
    except Exception as e:  # noqa: BLE001 - show whatever went wrong
        text = str(e).removeprefix("ERROR: ")
        if "could not find" in text.lower() or "failed to decrypt" in text.lower() \
                or "permission" in text.lower():
            text += (" Minuett couldn't read the browser's sign-in. If Minuett is a Flatpak, "
                     "it needs permission to read that browser's profile.")
        return YouTubeStatus(False, False, text)
    signed_in = "LOGIN_INFO" in names and bool(
        names & {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})
    premium = any(PREMIUM_MARKER in line for line in capture.lines)
    stale = any("no longer valid" in line for line in capture.lines)
    if not signed_in:
        msg = ("Not signed in. Sign in to music.youtube.com in that browser first."
               if acct.method == "browser" else
               "The cookies file isn't signed in (it may have expired). Export it again.")
    elif stale:
        msg = "Signed in, but YouTube has rotated these cookies. Export them again."
    elif premium:
        msg = "Signed in with YouTube Premium: higher-bitrate audio is available."
    else:
        msg = ("Signed in, but this account doesn't have YouTube Premium, so audio quality "
               "is the same as signed out.")
    return YouTubeStatus(signed_in and not stale, premium, msg)
