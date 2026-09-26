"""Shared building blocks so every view uses the same spacing, type and controls.

Spacing and sizes come from the theme's layout tokens (``--space-*``,
``--control-height``, ``--row-height``, ``--text-*``), so a theme can change
the density of the whole app.
"""

from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from .icons import icon
from .manager import manager


# Widgets/actions whose icon is drawn in theme colors, re-rendered on theme change.
_THEMED: list[tuple[weakref.ref, str, int, str, str | None]] = []
_hooked = False


def themed_icon(target, name: str, size: int = 18, color: str = "text",
                active: str | None = None) -> None:
    """Set ``target``'s icon and keep it in the current theme's colors."""
    global _hooked
    target.setIcon(icon(name, size, color, active))
    _THEMED.append((weakref.ref(target), name, size, color, active))
    if not _hooked:
        manager().theme_changed.connect(_refresh_icons)
        _hooked = True


def _refresh_icons(*_) -> None:
    alive = []
    for ref, name, size, color, active in _THEMED:
        target = ref()
        if target is None or not shiboken6.isValid(target):
            continue
        target.setIcon(icon(name, size, color, active))
        alive.append((ref, name, size, color, active))
    _THEMED[:] = alive


def px(token: str, default: int) -> int:
    """A layout token in pixels, e.g. px("space-4", 16)."""
    return round(manager().number(token, default))


_SPACE_DEFAULTS = {1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32}


def space(n: int) -> int:
    """Step n of the spacing scale (4/8/12/16/24/32 px by default)."""
    return px(f"space-{n}", _SPACE_DEFAULTS[n])


def text_px(size: str) -> int:
    return px(f"text-{size}", {"xs": 11, "sm": 12, "md": 13, "lg": 15, "xl": 18, "2xl": 22}[size])


def font(size: str = "md", weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setPixelSize(text_px(size))
    f.setWeight(weight)
    return f


def label(text: str = "", role: str | None = None, size: str | None = None,
          weight: QFont.Weight | None = None, elide: bool = False) -> QLabel:
    """A QLabel with a style role (#ViewTitle, #Muted, …) and a type-scale size."""
    lab = QLabel(text)
    if role:
        lab.setObjectName(role)
    if size or weight is not None:
        lab.setFont(font(size or "md", weight if weight is not None else QFont.Weight.Normal))
    if elide:
        lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return lab


def section_label(text: str) -> QLabel:
    """Small caps-style pane heading (BROWSE, UP NEXT, …)."""
    lab = label(text.upper(), "SectionLabel", "xs", QFont.Weight.Bold)
    f = lab.font()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    lab.setFont(f)
    return lab


def button(text: str = "", icon_name: str | None = None, variant: str = "secondary",
           tooltip: str = "", slot=None) -> QPushButton:
    """Push button: variant is 'primary' (accent), 'secondary' or 'ghost'."""
    b = QPushButton(text)
    b.setProperty("variant", variant)
    b.setMinimumHeight(px("control-height", 32))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # focus ring for keyboard users, not after clicks
    if icon_name:
        themed_icon(b, icon_name, 16, "accent-text" if variant == "primary" else "button-text")
        b.setIconSize(QSize(16, 16))
    if tooltip:
        b.setToolTip(tooltip)
    if not text:
        b.setAccessibleName(tooltip)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def icon_button(icon_name: str, tooltip: str, slot=None, checkable: bool = False,
                size: int = 30, color: str = "chrome-text") -> QToolButton:
    """Square icon-only button; always has a tooltip and an accessible name."""
    b = QToolButton()
    b.setObjectName("IconButton")
    themed_icon(b, icon_name, 18, color, active="accent")
    b.setIconSize(QSize(18, 18))
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    b.setFixedSize(size, size)
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setAutoRaise(True)
    if slot is not None:
        (b.toggled if checkable else b.clicked).connect(slot)
    return b


class ViewHeader(QFrame):
    """Title + subtitle on the left, actions on the right, with a divider below."""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewHeader")
        self.title = label(title, "ViewTitle", "xl", QFont.Weight.DemiBold)
        self.subtitle = label(subtitle, "Muted", "sm", elide=True)
        self.subtitle.setTextFormat(Qt.TextFormat.RichText)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(space(2))
        row = QHBoxLayout(self)
        row.setContentsMargins(space(4), space(3), space(4), space(3))
        row.setSpacing(space(4))
        row.addLayout(text, 1)
        row.addLayout(self.actions)

    def add(self, *widgets: QWidget) -> None:
        for w in widgets:
            self.actions.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setToolTip(text if "<" not in text else "")


def view(header: ViewHeader, body: QWidget | None = None, padded: bool = False) -> QWidget:
    """A center view: header on top, body below (optionally with padding)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(header)
    if body is not None:
        if padded:
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(space(4), space(3), space(4), space(4))
            wl.addWidget(body)
            lay.addWidget(wrap, 1)
        else:
            lay.addWidget(body, 1)
    return w


def tune_item_view(v: QAbstractItemView, row_height: int | None = None,
                   stretch_column: int | None = None) -> None:
    """Consistent row height, left-aligned headers, no dotted focus rect."""
    h = row_height or px("row-height", 30)
    vh = getattr(v, "verticalHeader", None)
    if vh is not None:
        vh().setDefaultSectionSize(h)
        vh().setMinimumSectionSize(h)
        vh().hide()
    hh = getattr(v, "horizontalHeader", None) or getattr(v, "header", None)
    if hh is not None:
        header: QHeaderView = hh()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumHeight(px("row-height", 30))
        header.setHighlightSections(False)
        if stretch_column is not None:
            header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    v.setTextElideMode(Qt.TextElideMode.ElideRight)
    if hasattr(v, "setWordWrap"):
        v.setWordWrap(False)
    v.setFrameShape(QFrame.Shape.NoFrame)


def status_color(kind: str) -> str:
    """Theme variable for a status kind: ok, warn, error, muted, active."""
    return {"ok": "accent", "active": "accent", "warn": "warning", "error": "warning",
            "muted": "text-muted"}.get(kind, "text")
