"""The Now Playing view (large cover art and track details) and the
two-line queue list used in the right-hand pane."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy, QStyle,
    QStyledItemDelegate, QVBoxLayout, QWidget,
)

from minuett.core.library.db import Track
from minuett.core.library.tags import TagError, read_cover
from minuett.ui.skin.components import ViewHeader, button, font, label, space, view
from minuett.ui.skin.icons import pixmap
from minuett.ui.skin.manager import manager

SubtitleRole = Qt.ItemDataRole.UserRole + 1
CurrentRole = Qt.ItemDataRole.UserRole + 2


class QueueDelegate(QStyledItemDelegate):
    """Two-line queue rows: title over artist, current track marked in accent."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 46)

    def paint(self, p: QPainter, option, index) -> None:
        tm = manager()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        current = bool(index.data(CurrentRole))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            p.fillRect(r, tm.color("selection"))
        elif hovered:
            p.fillRect(r, tm.color("hover"))
        if current:
            p.fillRect(r.x(), r.y() + 8, 3, r.height() - 16, tm.color("accent"))
        pad = space(3)
        text_w = r.width() - 2 * pad
        title_color = tm.color("selection-text" if selected else ("accent" if current else "text"))
        f1 = font("md", QFont.Weight.DemiBold if current else QFont.Weight.Normal)
        p.setFont(f1)
        p.setPen(title_color)
        title = p.fontMetrics().elidedText(index.data() or "", Qt.TextElideMode.ElideRight, text_w)
        p.drawText(r.x() + pad, r.y() + 6, text_w, 18, Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(font("sm"))
        p.setPen(tm.color("selection-text" if selected else "text-muted"))
        sub = p.fontMetrics().elidedText(index.data(SubtitleRole) or "", Qt.TextElideMode.ElideRight, text_w)
        p.drawText(r.x() + pad, r.y() + 24, text_w, 16, Qt.AlignmentFlag.AlignVCenter, sub)
        p.restore()


class QueueList(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(QueueDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)

    def set_rows(self, rows: list[tuple[str, str]], current: int) -> None:
        self.clear()
        for i, (title, subtitle) in enumerate(rows):
            item = QListWidgetItem(title)
            item.setData(SubtitleRole, subtitle)
            item.setData(CurrentRole, i == current)
            item.setToolTip(f"{title}\n{subtitle}" if subtitle else title)
            self.addItem(item)

    def set_current(self, current: int) -> None:
        for i in range(self.count()):
            self.item(i).setData(CurrentRole, i == current)
        if 0 <= current < self.count():
            self.scrollToItem(self.item(current))


class CoverArt(QWidget):
    """Square cover with rounded corners; a music glyph when there's no art."""

    def __init__(self, size: int = 280, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pix: QPixmap | None = None

    def set_image(self, data: bytes | None) -> None:
        pm = QPixmap()
        self._pix = pm if data and pm.loadFromData(data) else None
        self.update()

    def paintEvent(self, event) -> None:
        tm = manager()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(6.0, tm.number("radius", 4) * 2)
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)
        if self._pix is not None:
            scaled = self._pix.scaled(self.size() * self.devicePixelRatioF(),
                                      Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(self.devicePixelRatioF())
            x = (self.width() - scaled.width() / scaled.devicePixelRatio()) / 2
            y = (self.height() - scaled.height() / scaled.devicePixelRatio()) / 2
            p.drawPixmap(round(x), round(y), scaled)
        else:
            p.fillRect(rect, tm.color("panel"))
            glyph = pixmap("music", self.width() // 3, tm.color("text-muted"))
            p.drawPixmap((self.width() - self.width() // 3) // 2,
                         (self.height() - self.width() // 3) // 2, glyph)
        p.setClipping(False)
        p.setPen(QPen(tm.color("border"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        p.end()


class NowPlayingView(QWidget):
    show_library = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.header = ViewHeader("Now Playing", "Nothing playing")
        self.cover = CoverArt(280)
        self.title = label("Nothing playing", None, "2xl", QFont.Weight.DemiBold)
        self.title.setWordWrap(True)
        self.artist = label("", None, "lg")
        self.artist.setWordWrap(True)
        self.meta = label("", "Muted", "sm")
        self.meta.setWordWrap(True)
        self.up_next = label("", "Muted", "sm")
        self.up_next.setWordWrap(True)
        self.empty_action = button("Go to My Library", "library", "primary",
                                   slot=self.show_library.emit)

        text = QVBoxLayout()
        text.setSpacing(space(2))
        text.addStretch(1)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addSpacing(space(2))
        text.addWidget(self.meta)
        text.addSpacing(space(4))
        text.addWidget(self.up_next)
        text.addWidget(self.empty_action, 0, Qt.AlignmentFlag.AlignLeft)
        text.addStretch(1)

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(space(6), space(5), space(6), space(5))
        row.setSpacing(space(6))
        row.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(text, 1)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(view(self.header, body))
        self.show_track(None)

    def show_track(self, track: Track | None, path: str | None = None, details: str = "",
                   up_next: str | None = None) -> None:
        playing = track is not None or path is not None
        self.empty_action.setVisible(not playing)
        if not playing:
            self.title.setText("Nothing playing")
            self.artist.setText("Pick something from your library or a playlist.")
            self.meta.setText("")
            self.up_next.setText("")
            self.cover.set_image(None)
            self.header.set_subtitle("Nothing playing")
            return
        title = (track.title if track else None) or (path or "").rsplit("/", 1)[-1]
        self.title.setText(title)
        self.artist.setText(track.artist if track and track.artist else "Unknown artist")
        bits = [b for b in ((track.album if track else None),
                            (f"from {track.source_playlist}" if track and track.source_playlist else None),
                            details) if b]
        self.meta.setText("  ·  ".join(bits))
        self.up_next.setText(f"Up next: {up_next}" if up_next else "")
        self.header.set_subtitle(f"{self.artist.text()} — {title}")
        try:
            cover = read_cover(path or track.path)
        except (TagError, OSError, AttributeError):
            cover = None
        self.cover.set_image(cover.data if cover else None)
