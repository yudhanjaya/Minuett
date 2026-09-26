"""Custom-painted chrome: transport buttons, glowing sliders, faders, knobs,
and the display panel. Pure QSS can't do convincing gloss and glow, so these
paint themselves from the active theme's variables (see theme.py)."""

from __future__ import annotations

import math

import time
import weakref

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QAbstractButton, QDial, QSizePolicy, QSlider, QStyle, QWidget

from minuett.core.visualizer import Analyzer, bar_ranges, bars_from_magnitudes

from .manager import manager


def _c(name: str) -> QColor:
    return manager().color(name)


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


# --- halos --------------------------------------------------------------------

class HaloOverlay(QWidget):
    """Draws glows *over* the surrounding interface.

    A widget can't paint outside its own rectangle, so a thumb's glow used to
    be cut off at the slider's edges. Glowing controls instead hand their
    halo (and the thumb/cap that sits on it) to this transparent, click-
    through layer stacked above everything in the window.
    """

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sources: "weakref.WeakSet[QWidget]" = weakref.WeakSet()
        self._last: dict[int, tuple] = {}   # id(src) -> (rect, state) last drawn
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        host.window()._halo_overlay = self

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        elif event.type() == QEvent.Type.ChildAdded:
            QTimer.singleShot(0, self.raise_)   # stay on top of later children
        return False

    def _origin(self, src: QWidget) -> QPoint:
        """src's top-left in overlay coordinates. The overlay is a sibling, not
        an ancestor, so map through the window (an ancestor of both)."""
        win = self.window()
        return self.mapFrom(win, src.mapTo(win, QPoint(0, 0)))

    def track(self, src: QWidget) -> None:
        """A source repainted: refresh its halo (and erase where it was) if it
        changed. Repainting the overlay makes Qt repaint whatever is beneath
        it, including the source, so repainting unconditionally would loop."""
        self._sources.add(src)
        r = src.halo_rect().toAlignedRect()
        rect = QRect(self._origin(src) + r.topLeft(), r.size())
        tm = manager().current
        state = (rect.getRect(), src.halo_state(), tm.id if tm else None)
        old = self._last.get(id(src))
        if old is not None and old[1] == state:
            return
        self._last[id(src)] = (rect, state)
        self.update(rect.united(old[0]) if old is not None else rect)

    def forget(self, src: QWidget) -> None:
        old = self._last.pop(id(src), None)
        if old is not None:
            self.update(old[0])

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for src in list(self._sources):
            if not shiboken6.isValid(src) or not src.isVisible():
                continue
            p.save()
            p.translate(QPointF(self._origin(src)))
            src.paint_halo(p)
            p.restore()
        p.end()


def _overlay_for(widget: QWidget) -> HaloOverlay | None:
    ov = getattr(widget.window(), "_halo_overlay", None)
    return ov if ov is not None and shiboken6.isValid(ov) else None


class HaloMixin:
    """For widgets whose glow may reach past their own edges."""

    def halo_rect(self) -> QRectF:  # local coordinates
        raise NotImplementedError

    def halo_state(self) -> tuple:
        """Everything the halo's look depends on (besides position and theme)."""
        return (self.isEnabled(), self.underMouse(), getattr(self, "isDown", lambda: False)(),
                getattr(self, "sliderPosition", lambda: 0)(),
                getattr(self, "isSliderDown", lambda: False)(),
                getattr(self, "minimum", lambda: 0)(), getattr(self, "maximum", lambda: 0)())

    def paint_halo(self, p: QPainter) -> None:
        raise NotImplementedError

    def _halo(self, p: QPainter) -> None:
        """Call at the end of paintEvent: glow via the overlay, or inline."""
        ov = _overlay_for(self)
        if ov is not None:
            ov.track(self)
        else:
            self.paint_halo(p)

    def hideEvent(self, event) -> None:
        ov = _overlay_for(self)
        if ov is not None:
            ov.forget(self)
        super().hideEvent(event)


# --- transport buttons -------------------------------------------------------

class TransportButton(HaloMixin, QAbstractButton):
    """Round glossy button. ``primary`` is the oversized Play button."""

    KINDS = ("play", "pause", "stop", "prev", "next")

    def __init__(self, kind: str, diameter: int = 30, primary: bool = False, parent=None) -> None:
        super().__init__(parent)
        assert kind in self.KINDS
        self.kind = kind
        self.diameter = diameter
        self.primary = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.sizeHint())
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def sizeHint(self) -> QSize:
        pad = 10 if self.primary else 4
        return QSize(self.diameter + pad, self.diameter + pad)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.diameter
        r = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
        top = _c("play-top" if self.primary else "btn-top")
        bottom = _c("play-bottom" if self.primary else "btn-bottom")
        hovered = self.underMouse() and self.isEnabled()
        if self.isDown():
            top, bottom = bottom.darker(110), top.darker(110)
        elif hovered:
            top, bottom = top.lighter(115), bottom.lighter(115)

        body = QLinearGradient(r.topLeft(), r.bottomLeft())
        body.setColorAt(0, top)
        body.setColorAt(1, bottom)
        p.setPen(QPen(_c("btn-ring"), 1.2))
        p.setBrush(body)
        p.drawEllipse(r)

        # Gloss: a highlight over the upper half.
        gloss = _c("gloss")
        gr = QRectF(r.x() + d * 0.14, r.y() + d * 0.05, d * 0.72, d * 0.46)
        gg = QLinearGradient(gr.topLeft(), gr.bottomLeft())
        gg.setColorAt(0, gloss)
        gg.setColorAt(1, _alpha(gloss, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gg)
        p.drawEllipse(gr)

        icon = _c("play-icon" if self.primary else "btn-icon")
        if not self.isEnabled():
            icon.setAlpha(90)
        p.setBrush(icon)
        p.drawPath(self._icon(r))
        if self.hasFocus():  # keyboard focus ring
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_c("accent"), 2))
            p.drawEllipse(r.adjusted(-2, -2, 2, 2))
        if self.primary:
            self._halo(p)
        p.end()

    GLOW = 9.0   # how far the primary button's glow reaches past its rim

    def halo_rect(self) -> QRectF:
        c = QPointF(self.width() / 2, self.height() / 2)
        rad = self.diameter / 2 + self.GLOW
        return QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad)

    def paint_halo(self, p: QPainter) -> None:
        """A soft ring around the rim only, so drawing it on top never tints the button."""
        c = QPointF(self.width() / 2, self.height() / 2)
        body = self.diameter / 2
        rad = body + self.GLOW
        hovered = self.underMouse() and self.isEnabled()
        glow = _c("glow")
        g = QRadialGradient(c, rad)
        rim = body / rad
        g.setColorAt(0.0, _alpha(glow, 0))
        g.setColorAt(max(0.0, rim - 0.001), _alpha(glow, 0))
        g.setColorAt(rim, _alpha(glow, 130 if hovered else 75))
        g.setColorAt(1.0, _alpha(glow, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, rad, rad)

    def _icon(self, r: QRectF) -> QPainterPath:
        s = r.width() * (0.36 if self.primary else 0.34)
        cx, cy = r.center().x(), r.center().y()
        path = QPainterPath()

        def tri(x0, pointing_right=True, w=s * 0.9, h=s):
            if pointing_right:
                path.moveTo(x0, cy - h / 2)
                path.lineTo(x0 + w, cy)
                path.lineTo(x0, cy + h / 2)
            else:
                path.moveTo(x0 + w, cy - h / 2)
                path.lineTo(x0, cy)
                path.lineTo(x0 + w, cy + h / 2)
            path.closeSubpath()

        if self.kind == "play":
            tri(cx - s * 0.36)
        elif self.kind == "pause":
            w, h, gap = s * 0.3, s, s * 0.22
            path.addRoundedRect(QRectF(cx - gap / 2 - w, cy - h / 2, w, h), 1, 1)
            path.addRoundedRect(QRectF(cx + gap / 2, cy - h / 2, w, h), 1, 1)
        elif self.kind == "stop":
            e = s * 0.82
            path.addRoundedRect(QRectF(cx - e / 2, cy - e / 2, e, e), 1.5, 1.5)
        elif self.kind in ("prev", "next"):
            w, bar = s * 0.62, s * 0.16
            if self.kind == "next":
                tri(cx - w * 0.75, True, w, s * 0.85)
                path.addRect(QRectF(cx + w * 0.3, cy - s * 0.425, bar, s * 0.85))
            else:
                tri(cx - w * 0.25, False, w, s * 0.85)
                path.addRect(QRectF(cx - w * 0.3 - bar, cy - s * 0.425, bar, s * 0.85))
        return path


# --- sliders -----------------------------------------------------------------

class PaintedSlider(QSlider):
    """QSlider with jump-to-click, double-click reset, and custom painting."""

    reset = Signal()

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def thumb_radius(self) -> float:
        return 7.0

    def _value_at(self, pos: QPointF) -> int:
        r = self.thumb_radius()
        if self.orientation() == Qt.Orientation.Horizontal:
            span, offset = self.width() - 2 * r, pos.x() - r
            upside = False
        else:
            span, offset = self.height() - 2 * r, pos.y() - r
            upside = True  # vertical sliders grow upwards
        offset = max(0.0, min(span, offset))
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), round(offset), max(1, round(span)), upside)

    def _pos_of(self, value: int) -> float:
        r = self.thumb_radius()
        if self.orientation() == Qt.Orientation.Horizontal:
            span = self.width() - 2 * r
            return r + QStyle.sliderPositionFromValue(
                self.minimum(), self.maximum(), value, max(1, round(span)), False)
        span = self.height() - 2 * r
        return r + QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), value, max(1, round(span)), True)

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self.maximum() == self.minimum():
            return super().mousePressEvent(e)
        self.setSliderDown(True)   # emits sliderPressed
        self.setSliderPosition(self._value_at(e.position()))
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self.isSliderDown():
            self.setSliderPosition(self._value_at(e.position()))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self.isSliderDown():
            self.setSliderDown(False)   # emits sliderReleased
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.reset.emit()


class GlowSlider(HaloMixin, PaintedSlider):
    """Horizontal slider with a filled groove and a glowing thumb."""

    def __init__(self, parent=None, thumb: float = 7.0) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._thumb = thumb
        self.setMinimumHeight(int(thumb * 2 + 8))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def thumb_radius(self) -> float:
        return self._thumb

    def sizeHint(self) -> QSize:
        return QSize(160, int(self._thumb * 2 + 8))

    def _thumb_centre(self) -> QPointF:
        return QPointF(self._pos_of(self.sliderPosition()), self.height() / 2)

    def halo_rect(self) -> QRectF:
        c, g = self._thumb_centre(), self._thumb * 2.3
        return QRectF(c.x() - g, c.y() - g, 2 * g, 2 * g)

    def paint_halo(self, p: QPainter) -> None:
        if self.maximum() == self.minimum():
            return
        r, c = self._thumb, self._thumb_centre()
        glow = _c("glow")
        hot = self.underMouse() or self.isSliderDown()
        rg = QRadialGradient(c, r * 2.3)
        rg.setColorAt(0.35, _alpha(glow, 200 if hot else 140))
        rg.setColorAt(1.0, _alpha(glow, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rg)
        p.drawEllipse(c, r * 2.3, r * 2.3)
        thumb = _c("thumb")
        tg = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.4), r * 1.4)
        tg.setColorAt(0, thumb.lighter(130))
        tg.setColorAt(1, thumb)
        p.setBrush(tg)
        p.setPen(QPen(_c("btn-ring"), 1))
        p.drawEllipse(c, r, r)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._thumb
        cy = self.height() / 2
        groove = QRectF(r, cy - 3, self.width() - 2 * r, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("groove"))
        p.drawRoundedRect(groove, 3, 3)
        empty = self.maximum() == self.minimum()
        x = self._pos_of(self.sliderPosition()) if not empty else r
        if not empty:
            fill = QRectF(groove.left(), groove.top(), x - groove.left(), groove.height())
            g = QLinearGradient(fill.topLeft(), fill.bottomLeft())
            fc = _c("groove-fill")
            g.setColorAt(0, fc.lighter(125))
            g.setColorAt(1, fc)
            p.setBrush(g)
            p.drawRoundedRect(fill, 3, 3)
            self._halo(p)   # glow + thumb, drawn over the neighbours
        p.end()


class Fader(HaloMixin, PaintedSlider):
    """Vertical EQ fader: centre-zero groove, lit from 0 to the value, glossy cap."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setMinimumSize(30, 150)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def thumb_radius(self) -> float:
        return 7.0

    def sizeHint(self) -> QSize:
        return QSize(30, 180)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        r = self.thumb_radius()
        top, bottom = r, self.height() - r
        groove = QRectF(cx - 2.5, top, 5, bottom - top)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("groove"))
        p.drawRoundedRect(groove, 2.5, 2.5)

        # Tick marks every 6 dB (values are tenths of a dB).
        p.setPen(QPen(_c("eq-grid"), 1))
        step = 60
        v = (self.minimum() // step) * step
        while v <= self.maximum():
            y = self._pos_of(v)
            w = 7 if v == 0 else 4
            p.drawLine(QPointF(cx - 6 - w, y), QPointF(cx - 6, y))
            p.drawLine(QPointF(cx + 6, y), QPointF(cx + 6 + w, y))
            v += step

        enabled = self.isEnabled()
        y0 = self._pos_of(0)
        y = self._pos_of(self.sliderPosition())
        lit = QRectF(cx - 2.5, min(y, y0), 5, abs(y - y0))
        fill = _c("groove-fill") if enabled else _c("text-muted")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(lit, 2.5, 2.5)
        self._halo(p)   # glow + cap, drawn over the neighbours
        p.end()

    def halo_rect(self) -> QRectF:
        cx, y = self.width() / 2, self._pos_of(self.sliderPosition())
        return QRectF(cx - 15, y - 15, 30, 30)

    def paint_halo(self, p: QPainter) -> None:
        cx, y = self.width() / 2, self._pos_of(self.sliderPosition())
        enabled = self.isEnabled()
        fill = _c("groove-fill") if enabled else _c("text-muted")
        if enabled and self.value() != 0:
            glow = _c("glow")
            rg = QRadialGradient(QPointF(cx, y), 14)
            rg.setColorAt(0.3, _alpha(glow, 120))
            rg.setColorAt(1, _alpha(glow, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(cx, y), 14, 14)
        cap = QRectF(cx - 11, y - 5.5, 22, 11)
        cg = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        base = _c("fader-cap")
        cg.setColorAt(0, base.lighter(135))
        cg.setColorAt(0.5, base)
        cg.setColorAt(1, base.darker(125))
        p.setBrush(cg)
        p.setPen(QPen(_c("btn-ring"), 1))
        p.drawRoundedRect(cap, 2.5, 2.5)
        p.setPen(QPen(fill, 1.5))
        p.drawLine(QPointF(cap.left() + 4, y), QPointF(cap.right() - 4, y))


class Knob(QDial):
    """Small rotary knob with a value arc; double-click resets."""

    reset = Signal()

    def __init__(self, parent=None, size: int = 36) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, e) -> None:
        self.reset.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        c = QPointF(self.width() / 2, self.height() / 2)
        outer = side / 2 - 2
        arc_rect = QRectF(c.x() - outer, c.y() - outer, outer * 2, outer * 2)
        start, span = 225.0, -270.0  # degrees, Qt counter-clockwise from 3 o'clock

        p.setPen(QPen(_c("groove"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, int(start * 16), int(span * 16))
        rng = max(1, self.maximum() - self.minimum())
        frac = (self.value() - self.minimum()) / rng
        accent = _c("groove-fill") if self.isEnabled() else _c("text-muted")
        p.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, int(start * 16), int(span * frac * 16))

        body_r = outer - 5
        g = QLinearGradient(QPointF(c.x(), c.y() - body_r), QPointF(c.x(), c.y() + body_r))
        top, bottom = _c("knob-top"), _c("knob-bottom")
        if self.underMouse():
            top, bottom = top.lighter(112), bottom.lighter(112)
        g.setColorAt(0, top)
        g.setColorAt(1, bottom)
        p.setPen(QPen(_c("btn-ring"), 1))
        p.setBrush(g)
        p.drawEllipse(c, body_r, body_r)

        ang = math.radians(start + span * frac)
        tip = QPointF(c.x() + math.cos(ang) * (body_r - 3), c.y() - math.sin(ang) * (body_r - 3))
        mid = QPointF(c.x() + math.cos(ang) * body_r * 0.35, c.y() - math.sin(ang) * body_r * 0.35)
        p.setPen(QPen(accent, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(mid, tip)
        p.end()


# --- display panel -----------------------------------------------------------

class StatusDisplay(QWidget):
    """The now-playing readout, LCD-style: time on the left, title and details
    in the middle, and a Winamp-style spectrum analyzer on the right half."""

    VIS_MAX_FRACTION = 0.5     # the analyzer never takes more than half the panel
    VIS_MIN_WIDTH = 60
    FRAME_MS = 33

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.title = "Minuett"
        self.details = "Stopped"
        self.state = ""
        self.time = ""
        self.visualizer_enabled = True
        self.analyzer = Analyzer(24)
        self._ranges_key: tuple | None = None
        self._ranges: list[tuple[int, int]] = []
        self._vis_rect = QRectF()
        self._last_step = 0.0
        self._timer = QTimer(self, interval=self.FRAME_MS)
        self._timer.timeout.connect(self._animate)
        self.setMinimumSize(260, 46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(420, 48)

    def set_text(self, title: str, details: str) -> None:
        self.title, self.details = title, details
        self.setToolTip(f"{title}\n{details}")
        self.update()

    def set_time(self, time: str, state: str = "") -> None:
        if (time, state) != (self.time, self.state):
            self.time, self.state = time, state
            self.update()

    # --- visualizer --------------------------------------------------------

    def set_visualizer_enabled(self, on: bool) -> None:
        self.visualizer_enabled = on
        if not on:
            self._timer.stop()
            self.analyzer.resize(0)
        self.update()

    def feed_spectrum(self, magnitudes: list[float], rate: int) -> None:
        if not self.visualizer_enabled or self._vis_rect.width() < self.VIS_MIN_WIDTH:
            return
        n = self.analyzer.n_bars
        key = (n, rate, len(magnitudes))
        if key != self._ranges_key:
            self._ranges = bar_ranges(n, rate, len(magnitudes))
            self._ranges_key = key
        self.analyzer.feed(bars_from_magnitudes(magnitudes, self._ranges,
                                                band_hz=rate / 2 / len(magnitudes)))
        if not self._timer.isActive():
            self._last_step = time.monotonic()
            self._timer.start()

    def silence(self) -> None:
        """Playback paused or stopped: let the bars fall away."""
        self.analyzer.silence()

    def _animate(self) -> None:
        now = time.monotonic()
        alive = self.analyzer.step(min(0.1, now - self._last_step))
        self._last_step = now
        self.update(self._vis_rect.toAlignedRect().adjusted(-2, -2, 2, 2))
        if not alive:
            self._timer.stop()

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:
        m = manager()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(2.0, m.number("radius", 4))
        bg = _c("lcd")
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, bg.lighter(112))
        g.setColorAt(1, bg)
        p.setPen(QPen(_c("lcd-border"), 1))
        p.setBrush(g)
        p.drawRoundedRect(rect, radius, radius)

        inner = rect.adjusted(10, 5, -10, -5)
        x = inner.left()

        # Time on the left (Winamp's big clock), state underneath.
        if self.time:
            elapsed = self.time.split(" / ")[0]
            p.setFont(m.lcd_font(22, bold=True))
            tw = p.fontMetrics().horizontalAdvance(elapsed) + 2
            p.setPen(_c("lcd-text"))
            p.drawText(QRectF(x, inner.top() - 2, tw, inner.height() * 0.7),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elapsed)
            p.setFont(m.lcd_font(10))
            p.setPen(_c("lcd-dim"))
            total = self.time.split(" / ")[1] if " / " in self.time else ""
            under = " ".join(part for part in (self.state.upper(), f"/ {total}" if total else "") if part)
            uw = p.fontMetrics().horizontalAdvance(under) + 2
            p.drawText(QRectF(x, inner.top() + inner.height() * 0.62, uw, inner.height() * 0.38),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, under)
            x += max(tw, uw) + 14

        # Analyzer on the right, never wider than half the panel.
        vis_w = 0.0
        if self.visualizer_enabled:
            vis_w = min(inner.width() * self.VIS_MAX_FRACTION, inner.right() - x - 120)
        if vis_w >= self.VIS_MIN_WIDTH:
            self._vis_rect = QRectF(inner.right() - vis_w, inner.top() + 1, vis_w, inner.height() - 2)
            self._paint_spectrum(p, self._vis_rect)
            text_right = self._vis_rect.left() - 12
        else:
            self._vis_rect = QRectF()
            text_right = inner.right()

        text_rect = QRectF(x, inner.top(), max(0.0, text_right - x), inner.height())
        p.setFont(m.lcd_font(14, bold=True))
        p.setPen(_c("lcd-text"))
        fm = p.fontMetrics()
        p.drawText(QRectF(text_rect.left(), text_rect.top(), text_rect.width(), text_rect.height() * 0.55),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideRight, int(text_rect.width())))
        p.setFont(m.lcd_font(11))
        p.setPen(_c("lcd-dim"))
        fm = p.fontMetrics()
        p.drawText(QRectF(text_rect.left(), text_rect.top() + text_rect.height() * 0.55,
                          text_rect.width(), text_rect.height() * 0.45),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.details, Qt.TextElideMode.ElideRight, int(text_rect.width())))
        p.end()

    def _paint_spectrum(self, p: QPainter, r: QRectF) -> None:
        gap = 1.0
        n = int(max(10, min(40, r.width() // 5)))
        self.analyzer.resize(n)
        bar_w = (r.width() - gap * (n - 1)) / n
        low, mid, high, peak = (_c("vis-low"), _c("vis-mid"), _c("vis-high"), _c("vis-peak"))
        # One gradient spanning the full height: a short bar shows only the
        # low colour, a tall one runs up into the hot colours, as in Winamp.
        grad = QLinearGradient(r.bottomLeft(), r.topLeft())
        grad.setColorAt(0.0, low)
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, high)
        base = _alpha(_c("lcd-dim"), 70)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            bx = r.left() + i * (bar_w + gap)
            p.fillRect(QRectF(bx, r.bottom() - 1, bar_w, 1), base)
            h = self.analyzer.bars[i] * r.height()
            if h >= 1:
                p.fillRect(QRectF(bx, r.bottom() - h, bar_w, h), grad)
            pk = self.analyzer.peaks[i]
            if pk > 0.02:
                y = r.bottom() - pk * r.height()
                p.fillRect(QRectF(bx, max(r.top(), y - 1.5), bar_w, 1.5), peak)
