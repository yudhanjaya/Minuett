"""The Accounts section at the foot of the right sidebar: optional sign-ins."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QVBoxLayout

from antiphon.core import accounts
from antiphon.ui.skin.components import button, label, section_label, space


class AccountsPanel(QFrame):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AccountsPanel")
        self.yt_status = label("", "Muted", "sm", elide=True)
        self.sp_status = label("", "Muted", "sm", elide=True)
        self.yt_btn = button("Sign In", None, "compact", "Optional: YouTube Premium audio",
                             slot=self._open_youtube)
        self.sp_btn = button("Connect", None, "compact", "Optional: read your own playlists in full",
                             slot=self._open_spotify)
        grid = QGridLayout()
        grid.setHorizontalSpacing(space(2))
        grid.setVerticalSpacing(space(2))
        for row, (name, status, btn) in enumerate((("YouTube Music", self.yt_status, self.yt_btn),
                                                   ("Spotify", self.sp_status, self.sp_btn))):
            title = label(name, None, "md", QFont.Weight.DemiBold)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(title)
            col.addWidget(status)
            grid.addLayout(col, row, 0)
            grid.addWidget(btn, row, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(0, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(3), space(3), space(3), space(3))
        layout.setSpacing(space(2))
        layout.addWidget(section_label("Accounts (optional)"))
        layout.addLayout(grid)
        self.refresh()

    def refresh(self) -> None:
        a = accounts.load()
        yt, sp = a.youtube, a.spotify
        if not yt.configured:
            text = "Sign in for Premium audio"
        elif yt.signed_in and yt.premium:
            text = "Premium · high-bitrate audio"
        elif yt.signed_in:
            text = "Signed in · no Premium"
        else:
            text = "Saved · not checked"
        self.yt_status.setText(text)
        self.yt_status.setToolTip(text)
        self.yt_btn.setText("Manage" if yt.configured else "Sign In")
        sp_text = f"Connected as {sp.user_name or sp.user_id}" if sp.connected else \
            "Import your own playlists"
        self.sp_status.setText(sp_text)
        self.sp_status.setToolTip(sp_text)
        self.sp_btn.setText("Manage" if sp.connected else "Connect")

    def _open_youtube(self) -> None:
        from antiphon.ui.dialogs.accounts import YouTubeSignInDialog
        dlg = YouTubeSignInDialog(self.window())
        dlg.changed.connect(self._changed)
        dlg.exec()

    def _open_spotify(self) -> None:
        from antiphon.ui.dialogs.accounts import SpotifyConnectDialog
        dlg = SpotifyConnectDialog(self.window())
        dlg.changed.connect(self._changed)
        dlg.exec()

    def _changed(self) -> None:
        self.refresh()
        self.changed.emit()
