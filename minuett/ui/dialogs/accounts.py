"""Optional sign-in dialogs: YouTube Music (Premium audio) and Spotify (your playlists)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLineEdit,
    QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)

from minuett.core import accounts
from minuett.core.accounts import YouTubeAccount
from minuett.core.downloader.playlist import base_options
from minuett.ui.skin.components import button, label, space
from minuett.ui.tasks import run_in_thread

YOUTUBE_RISK = ("YouTube can restrict accounts it sees used by download tools, "
                "temporarily or permanently. Minuett paces its requests, but the risk "
                "isn't zero. Sign-in is optional; everything works without it.")
COOKIES_HELP = ("Export cookies from a private window so they don't go stale: open a private "
                "window, sign in to music.youtube.com, export cookies.txt with a browser "
                "extension, then close the window.")


DIALOG_WIDTH = 620


def _note(text: str, role: str = "Muted"):
    lab = label(text, role, "sm")
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    # Wrapped labels need a real width for Qt to work out their height.
    lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lab


def _fit(dialog: QDialog) -> None:
    """Fix the width and size the height to the wrapped text at that width."""
    dialog.setFixedWidth(DIALOG_WIDTH)
    height = dialog.layout().totalHeightForWidth(DIALOG_WIDTH)
    dialog.setMinimumHeight(height)
    dialog.resize(DIALOG_WIDTH, height)


def _warning(text: str):
    lab = _note(text, "EqPeak")   # warning colour role
    return lab


class YouTubeSignInDialog(QDialog):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to YouTube Music")
        self.accts = accounts.load()
        yt = self.accts.youtube
        self._checked: accounts.YouTubeStatus | None = None

        intro = _note(
            "With a YouTube Premium account, Minuett downloads Premium's higher-bitrate audio "
            "(Opus up to about 256 kbps, instead of about 130–160 kbps). Without Premium, "
            "signing in doesn't change the quality. Minuett never sees your password: it "
            "reuses a sign-in you already have.", None)

        self.use_browser = QRadioButton("Use my browser's sign-in")
        self.use_file = QRadioButton("Use a cookies file")
        group = QButtonGroup(self)
        for b in (self.use_browser, self.use_file):
            group.addButton(b)
            b.toggled.connect(self._update)

        self.browsers = accounts.installed_browsers()
        self.browser_box = QComboBox()
        for b in self.browsers:
            self.browser_box.addItem(b.label if b.visible else f"{b.label} (needs permission)", b)
        if not self.browsers:
            self.browser_box.addItem("No supported browser found", None)
            self.browser_box.setEnabled(False)
        open_yt = button("Open YouTube Music", "music", "ghost",
                         "Sign in there first, in the browser chosen here",
                         slot=lambda: QDesktopServices.openUrl(QUrl("https://music.youtube.com")))
        browser_row = QHBoxLayout()
        browser_row.setSpacing(space(2))
        browser_row.addWidget(self.browser_box, 1)
        browser_row.addWidget(open_yt)
        self.browser_hint = _note("Sign in to music.youtube.com in that browser, then press Check.")
        self.flatpak_hint = _note("")
        self.flatpak_copy = button("Copy Command", None, "ghost", slot=self._copy_override)
        self.browser_box.currentIndexChanged.connect(self._update)

        self.file_edit = QLineEdit(placeholderText="cookies.txt")
        self.file_edit.setReadOnly(True)
        choose = button("Choose…", "folder", "secondary", slot=self._choose_file)
        file_row = QHBoxLayout()
        file_row.setSpacing(space(2))
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(choose)
        self._file_source: Path | None = None
        if yt.method == "file" and yt.cookie_file:
            self.file_edit.setText(yt.cookie_file)

        self.status = _note(self._describe(yt), None)
        self.check_btn = button("Check", "refresh", "secondary", "Test the sign-in (no download)",
                                slot=self._check)
        self.signout_btn = button("Sign Out", "close", "ghost", slot=self._sign_out)
        self.save_btn = button("Save", "save", "primary", slot=self._save)
        cancel = button("Cancel", None, "ghost", slot=self.reject)

        browser_box = QWidget()
        bl = QVBoxLayout(browser_box)
        bl.setContentsMargins(space(5), 0, 0, 0)
        bl.setSpacing(space(1))
        bl.addLayout(browser_row)
        bl.addWidget(self.browser_hint)
        fp = QHBoxLayout()
        fp.addWidget(self.flatpak_hint, 1)
        fp.addWidget(self.flatpak_copy, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(fp)
        self._browser_box = browser_box
        file_box = QWidget()
        fl = QVBoxLayout(file_box)
        fl.setContentsMargins(space(5), 0, 0, 0)
        fl.setSpacing(space(1))
        fl.addLayout(file_row)
        fl.addWidget(_note(COOKIES_HELP))
        self._file_box = file_box

        status_frame = QFrame()
        status_frame.setObjectName("SubHeader")
        sl = QHBoxLayout(status_frame)
        sl.setContentsMargins(space(3), space(2), space(3), space(2))
        sl.addWidget(self.status, 1)
        sl.addWidget(self.check_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        buttons = QHBoxLayout()
        buttons.addWidget(self.signout_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(5), space(5), space(5), space(4))
        layout.setSpacing(space(3))
        layout.addWidget(intro)
        layout.addWidget(self.use_browser)
        layout.addWidget(browser_box)
        layout.addWidget(self.use_file)
        layout.addWidget(file_box)
        layout.addWidget(status_frame)
        layout.addWidget(_warning(YOUTUBE_RISK))
        layout.addLayout(buttons)
        _fit(self)
        # Restore the saved choice last: these fire _update, which needs the widgets above.
        if yt.method == "file":
            self.use_file.setChecked(True)
        else:
            self.use_browser.setChecked(True)
            for i in range(self.browser_box.count()):
                b = self.browser_box.itemData(i)
                if b and b.key == yt.browser:
                    self.browser_box.setCurrentIndex(i)
        self._update()

    @staticmethod
    def _describe(yt: YouTubeAccount) -> str:
        if not yt.configured:
            return "Not signed in: standard quality."
        if yt.signed_in and yt.premium:
            return "Signed in with Premium: higher-bitrate audio."
        if yt.signed_in:
            return "Signed in without Premium: standard quality."
        return "Sign-in saved but not checked yet. Press Check."

    def _candidate(self) -> YouTubeAccount:
        yt = YouTubeAccount()
        if self.use_file.isChecked():
            yt.method = "file"
            yt.cookie_file = str(self._file_source) if self._file_source else self.accts.youtube.cookie_file
        else:
            b = self.browser_box.currentData()
            if b:
                yt.method, yt.browser, yt.profile = "browser", b.key, b.profile
        return yt

    def _update(self, *_) -> None:
        browser = self.use_browser.isChecked()
        self._browser_box.setEnabled(browser)
        self._file_box.setEnabled(not browser)
        b = self.browser_box.currentData()
        show_fp = browser and b is not None and not b.visible
        if show_fp:
            others = accounts.other_install_grants(b.key)
            extra = ""
            if others:
                extra = ("\nIf your browser is a Flatpak or Snap, use its folder instead: "
                         + ", ".join(o.replace(str(Path.home()), "~", 1) for o in others))
            if b.key in accounts.CHROMIUM_FAMILY:
                extra += ("\nIf there is no Default/Network folder (older profiles), grant "
                          "Default/Cookies instead.")
            self.flatpak_hint.setText(
                "Minuett runs as a Flatpak and can't read your browser's sign-in until you "
                "allow it. This grants read-only access to the browser's cookie folder only "
                "(not your history or passwords) and to the keyring. Run it once in a "
                "terminal, then restart Minuett:\n" + accounts.flatpak_override_hint(b.grant)
                + extra)
        self.flatpak_hint.setVisible(show_fp)
        self.flatpak_copy.setVisible(show_fp)
        self.browser_hint.setVisible(not show_fp)
        self.signout_btn.setVisible(self.accts.youtube.configured)
        self.check_btn.setEnabled(self._candidate().configured)
        self.save_btn.setEnabled(self._candidate().configured)

    def _copy_override(self) -> None:
        b = self.browser_box.currentData()
        if b:
            QGuiApplication.clipboard().setText(accounts.flatpak_override_hint(b.grant))

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose cookies.txt", str(Path.home() / "Downloads"),
                                              "Cookie files (*.txt);;All files (*)")
        if path:
            self._file_source = Path(path)
            self.file_edit.setText(path)
            self._update()

    def _store_file(self, yt: YouTubeAccount) -> YouTubeAccount:
        if yt.method == "file" and self._file_source:
            yt.cookie_file = str(accounts.store_cookie_file(self._file_source))
            self._file_source = None
        return yt

    def _check(self) -> None:
        try:
            yt = self._store_file(self._candidate())
        except (OSError, ValueError) as e:
            self.status.setText(str(e))
            return
        self.check_btn.setEnabled(False)
        self.status.setText("Checking with YouTube Music…")

        def done(result, err):
            self.check_btn.setEnabled(True)
            if err:
                self.status.setText(f"Couldn't check: {err}")
                return
            self._checked = result
            self.status.setText(result.message)
        run_in_thread(self, lambda: accounts.check_youtube(yt, base_options(signed_in=False)), done)

    def _save(self) -> None:
        try:
            yt = self._store_file(self._candidate())
        except (OSError, ValueError) as e:
            self.status.setText(str(e))
            return
        if self._checked is not None:
            yt.signed_in, yt.premium = self._checked.signed_in, self._checked.premium
            yt.checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.accts.youtube = yt
        accounts.save(self.accts)
        self.changed.emit()
        self.accept()

    def _sign_out(self) -> None:
        if self.accts.youtube.cookie_file:
            Path(self.accts.youtube.cookie_file).unlink(missing_ok=True)
        self.accts.youtube = YouTubeAccount()
        accounts.save(self.accts)
        self.changed.emit()
        self.accept()


class SpotifyConnectDialog(QDialog):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from minuett.core.spotify_auth import REDIRECT_URI
        self.setWindowTitle("Connect Spotify")
        self.accts = accounts.load()
        sp = self.accts.spotify

        intro = _note(
            "Connecting lets Minuett read your own and collaborative Spotify playlists in full, "
            "including private ones and those over 100 songs, with album names for better "
            "matching. It doesn't change audio quality: Spotify's audio is protected, and songs "
            "are always found on YouTube Music.", None)
        steps = _note(
            "Spotify only lets registered apps sign in, so this needs a free app of your own "
            "(once):\n"
            "1. Open the Spotify Developer Dashboard and create an app. The account that owns "
            "it needs Spotify Premium.\n"
            f"2. Add this Redirect URI: {REDIRECT_URI}\n"
            "3. Tick Web API, save, and add your Spotify account under User Management.\n"
            "4. Copy the app's Client ID and paste it below.", None)
        dashboard = button("Open Developer Dashboard", None, "ghost", slot=lambda: QDesktopServices.openUrl(
            QUrl("https://developer.spotify.com/dashboard")))
        copy_uri = button("Copy Redirect URI", None, "ghost",
                          slot=lambda: QGuiApplication.clipboard().setText(REDIRECT_URI))
        link_row = QHBoxLayout()
        link_row.addWidget(dashboard)
        link_row.addWidget(copy_uri)
        link_row.addStretch(1)

        self.client_id = QLineEdit(sp.client_id or "", placeholderText="Client ID")
        self.client_id.setAccessibleName("Spotify Client ID")
        self.client_id.textChanged.connect(self._update)
        self.status = _note(f"Connected as {sp.user_name or sp.user_id}." if sp.connected
                            else "Not connected.", None)
        self.connect_btn = button("Connect", "refresh", "primary", slot=self._connect)
        self.disconnect_btn = button("Disconnect", "close", "ghost", slot=self._disconnect)
        close = button("Close", None, "ghost", slot=self.accept)

        status_frame = QFrame()
        status_frame.setObjectName("SubHeader")
        sl = QHBoxLayout(status_frame)
        sl.setContentsMargins(space(3), space(2), space(3), space(2))
        sl.addWidget(self.status, 1)

        buttons = QHBoxLayout()
        buttons.addWidget(self.disconnect_btn)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.connect_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(5), space(5), space(5), space(4))
        layout.setSpacing(space(3))
        layout.addWidget(intro)
        layout.addWidget(steps)
        layout.addLayout(link_row)
        layout.addWidget(self.client_id)
        layout.addWidget(status_frame)
        layout.addLayout(buttons)
        _fit(self)
        self._busy = False
        self._receiver = None
        self._update()

    def done(self, result: int) -> None:
        # Closing mid-sign-in: stop listening for Spotify's redirect.
        if self._receiver is not None:
            self._receiver.close()
            self._receiver = None
        super().done(result)

    def _update(self, *_) -> None:
        self.connect_btn.setEnabled(bool(self.client_id.text().strip()) and not self._busy)
        self.disconnect_btn.setVisible(self.accts.spotify.connected)

    def _connect(self) -> None:
        from minuett.core.spotify_auth import (
            LoopbackReceiver, SpotifyClient, SpotifyError, authorize_url, exchange_code, make_verifier,
        )
        sp = self.accts.spotify
        sp.client_id = self.client_id.text().strip()
        verifier, state = make_verifier(), secrets.token_urlsafe(16)
        try:
            receiver = LoopbackReceiver(state)
        except SpotifyError as e:
            self.status.setText(str(e))
            return
        self._busy = True
        self._receiver = receiver
        self._update()
        self.status.setText("Finish signing in on the Spotify page in your browser…")
        QDesktopServices.openUrl(QUrl(authorize_url(sp.client_id, verifier, state)))

        def work():
            code = receiver.wait()
            exchange_code(sp, code, verifier)
            me = SpotifyClient(sp).me()
            sp.user_id, sp.user_name = me.get("id"), me.get("display_name") or me.get("id")
            return sp

        def finished(result, err):
            self._busy = False
            self._receiver = None
            if err:
                self.status.setText(str(err))
            else:
                self.accts.spotify = result
                accounts.save(self.accts)
                self.status.setText(f"Connected as {result.user_name}.")
                self.changed.emit()
            self._update()
        run_in_thread(self, work, finished)

    def _disconnect(self) -> None:
        self.accts.spotify = accounts.SpotifyAccount(client_id=self.accts.spotify.client_id)
        accounts.save(self.accts)
        self.status.setText("Disconnected.")
        self.changed.emit()
        self._update()


