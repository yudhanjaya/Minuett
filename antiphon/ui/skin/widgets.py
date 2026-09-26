"""Custom-painted chrome: transport buttons, glowing sliders, faders, knobs,
and the display panel. Pure QSS can't do convincing gloss and glow, so these
paint themselves from the active theme's variables (see theme.py)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QAbstractButton, QDial, QSizePolicy, QSlider, QStyle, QWidget

from .manager import manager


def _c(name: str) -> QColor:
    return manager().color(name)


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


# --- transport buttons -------------------------------------------------------

class TransportButton(QAbstractButton):
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

        # Soft outer glow on the primary button.
        if self.primary:
            glow = _c("glow")
            g = QRadialGradient(r.center(), d * 0.5 + 6)
            g.setColorAt(0.78, _alpha(glow, 110 if hovered else 60))
            g.setColorAt(1.0, _alpha(glow, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(r.center(), d * 0.5 + 6, d * 0.5 + 6)

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
        p.end()

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


class GlowSlider(PaintedSlider):
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

            glow = _c("glow")
            hot = self.underMouse() or self.isSliderDown()
            rg = QRadialGradient(QPointF(x, cy), r * 2.3)
            rg.setColorAt(0.35, _alpha(glow, 200 if hot else 140))
            rg.setColorAt(1.0, _alpha(glow, 0))
            p.setBrush(rg)
            p.drawEllipse(QPointF(x, cy), r * 2.3, r * 2.3)

            thumb = _c("thumb")
            tg = QRadialGradient(QPointF(x - r * 0.3, cy - r * 0.4), r * 1.4)
            tg.setColorAt(0, thumb.lighter(130))
            tg.setColorAt(1, thumb)
            p.setBrush(tg)
            p.setPen(QPen(_c("btn-ring"), 1))
            p.drawEllipse(QPointF(x, cy), r, r)
        p.end()


class Fader(PaintedSlider):
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
        if enabled and self.value() != 0:
            glow = _c("glow")
            rg = QRadialGradient(QPointF(cx, y), 14)
            rg.setColorAt(0.3, _alpha(glow, 120))
            rg.setColorAt(1, _alpha(glow, 0))
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
        p.end()


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
    """The status readout: title, details line, state and time, LCD-style."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.title = "Antiphon"
        self.details = "Stopped"
        self.state = ""
        self.time = ""
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
        time_w = 0.0
        if self.time:
            p.setFont(m.lcd_font(20, bold=True))
            time_w = p.fontMetrics().horizontalAdvance(self.time) + 4
            p.setPen(_c("lcd-text"))
            p.drawText(QRectF(inner.right() - time_w, inner.top(), time_w, inner.height() * 0.66),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.time)
            if self.state:
                p.setFont(m.lcd_font(10))
                p.setPen(_c("lcd-dim"))
                p.drawText(QRectF(inner.right() - time_w, inner.top() + inner.height() * 0.6,
                                  time_w, inner.height() * 0.4),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           self.state.upper())
        text_rect = QRectF(inner.left(), inner.top(), inner.width() - time_w - 12, inner.height())
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
