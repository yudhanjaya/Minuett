"""Minuett's icon set: simple 24px stroke icons, recolored per theme.

Drawn for Minuett in a consistent style (2px round strokes, 24x24 grid) so
one family is used everywhere. ``icon(name)`` renders crisp at any device
pixel ratio in the requested theme color.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from .manager import manager

_PATHS: dict[str, str] = {
    "now-playing": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>'
                   '<path d="M7 12a5 5 0 0 1 5-5"/>',
    "library": '<path d="M4 4v16"/><path d="M8.5 6v14"/><path d="M13 6v14"/><path d="m16.5 6.5 3.5 13"/>',
    "playlists": '<path d="M3 6h12"/><path d="M3 11h12"/><path d="M3 16h7"/>'
                 '<circle cx="16.5" cy="17.5" r="2.5"/><path d="M19 17.5V8l2.5-1"/>',
    "downloads": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    "equalizer": '<path d="M6 4v16"/><path d="M12 4v16"/><path d="M18 4v16"/>'
                 '<path d="M3.5 9h5"/><path d="M9.5 15h5"/><path d="M15.5 7h5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.3-4.3L4 8.5"/><path d="M4 4v4.5h4.5"/>'
               '<path d="M4 13a8 8 0 0 0 14.3 4.3l1.7-1.8"/><path d="M20 20v-4.5h-4.5"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "retry": '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "volume": '<path d="M11 5 6.5 9H3v6h3.5L11 19z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6"/>'
              '<path d="M18.5 6a9 9 0 0 1 0 12"/>',
    "music": '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "power": '<path d="M12 3v8"/><path d="M17.7 6.6a8 8 0 1 1-11.4 0"/>',
    "save": '<path d="M5 4h11l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/>'
            '<path d="M8 4v4h7"/><path d="M8 20v-6h8v6"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 13h9l1-13"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/>'
             '<path d="m20.5 16-5-5-9.5 8.5"/>',
    "queue": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/>'
             '<path d="M4 6h.5"/><path d="M4 12h.5"/><path d="M4 18h.5"/>',
    "compact": '<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="m14 10 6.5-6.5"/><path d="M3.5 20.5 10 14"/>',
    "warning": '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
}


def svg(name: str, color: str) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'{_PATHS[name]}</svg>').encode()


def pixmap(name: str, size: int = 18, color: QColor | str | None = None) -> QPixmap:
    c = QColor(color) if color is not None else manager().color("text")
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, c.name(QColor.NameFormat.HexArgb)
                                           if c.alpha() < 255 else c.name())))
    p = QPainter(pm)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, size: int = 18, color: str = "text", active: str | None = None) -> QIcon:
    """A theme-colored icon; ``active`` sets the color used when selected/checked."""
    tm = manager()
    ic = QIcon()
    normal = pixmap(name, size, tm.color(color))
    ic.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, tm.color("text-muted")), QIcon.Mode.Disabled)
    sel = pixmap(name, size, tm.color(active)) if active else normal
    ic.addPixmap(sel, QIcon.Mode.Selected, QIcon.State.Off)
    ic.addPixmap(sel, QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(sel, QIcon.Mode.Active, QIcon.State.On)
    return ic


def names() -> list[str]:
    return list(_PATHS)


__all__ = ["icon", "pixmap", "svg", "names"]
