"""Parametric EQ panel: live response curve over ten band strips.

Each strip has a vertical gain slider (±12 dB) with a frequency knob and a
Q knob beneath it, which is what makes it parametric rather than graphic.
Double-click a slider or knob to reset it. Styling is left to the skin pass.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from antiphon.core.equalizer import (
    DEFAULT_Q, FREQ_MAX, FREQ_MIN, GAIN_MAX, GAIN_MIN, ISO_FREQS, NUM_BANDS, PREAMP_MAX,
    PREAMP_MIN, Q_MAX, Q_MIN, EqState, PresetStore, log_freqs, peak_db, response_db,
)
from antiphon.ui.skin.manager import manager as theme_manager
from antiphon.ui.skin.components import ViewHeader, button, label, space, view
from antiphon.ui.skin.widgets import Fader, Knob

DIAL_STEPS = 1000
CURVE_DB = 15.0  # vertical range of the plot, ±


def fmt_freq(f: float) -> str:
    if f >= 1000:
        k = f / 1000
        return f"{k:.1f}k".replace(".0k", "k") if k < 10 else f"{k:.0f}k"
    return f"{f:.0f}"


def _log_to_dial(v: float, lo: float, hi: float) -> int:
    return round((math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * DIAL_STEPS)


def _dial_to_log(pos: int, lo: float, hi: float) -> float:
    return math.exp(math.log(lo) + pos / DIAL_STEPS * (math.log(hi) - math.log(lo)))


def manager_color(name: str) -> str:
    return theme_manager().color(name).name()


def _fader() -> Fader:
    f = Fader()
    f.setPageStep(10)
    return f


class ResponseCurve(QWidget):
    """Combined frequency response, drawn on a log-frequency axis."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.state = EqState()
        self.active_band: int | None = None
        self._freqs = log_freqs(240)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(640, 200)

    def set_state(self, state: EqState, active_band: int | None = None) -> None:
        self.state = state
        self.active_band = active_band
        self.update()

    def _x(self, rect: QRectF, f: float) -> float:
        span = math.log10(FREQ_MAX) - math.log10(FREQ_MIN)
        return rect.left() + (math.log10(f) - math.log10(FREQ_MIN)) / span * rect.width()

    def _y(self, rect: QRectF, db: float) -> float:
        db = max(-CURVE_DB, min(CURVE_DB, db))
        return rect.center().y() - db / CURVE_DB * rect.height() / 2

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tm = theme_manager()
        bg = tm.color("surface")
        p.setPen(QPen(tm.color("border"), 1))
        p.setBrush(bg)
        radius = tm.number("radius", 4)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        rect = QRectF(self.rect()).adjusted(34, 8, -8, -18)
        grid = QPen(tm.color("eq-grid"), 1)
        text = tm.color("text-muted")
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() * 0.8))
        p.setFont(small)

        for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
            x = self._x(rect, f)
            p.setPen(grid)
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            p.setPen(text)
            p.drawText(QRectF(x - 20, rect.bottom() + 2, 40, 14),
                       Qt.AlignmentFlag.AlignCenter, fmt_freq(f))
        for db in (-12, -6, 0, 6, 12):
            y = self._y(rect, db)
            pen = QPen(tm.color("eq-grid").lighter(130) if db == 0 else tm.color("eq-grid"),
                       1.6 if db == 0 else 1)
            p.setPen(pen)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(text)
            p.drawText(QRectF(0, y - 7, 30, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{db:+d}")

        values = response_db(self.state, self._freqs)
        points = [QPointF(self._x(rect, f), self._y(rect, v)) for f, v in zip(self._freqs, values)]
        path = QPainterPath(points[0])
        for pt in points[1:]:
            path.lineTo(pt)

        accent = tm.color("eq-curve") if self.state.enabled else tm.color("text-muted")
        if self.state.enabled:
            # Shade between the curve and the 0 dB line.
            zero = self._y(rect, 0)
            fill = QPainterPath(QPointF(points[0].x(), zero))
            for pt in points:
                fill.lineTo(pt)
            fill.lineTo(QPointF(points[-1].x(), zero))
            fill.closeSubpath()
            p.fillPath(fill, tm.color("eq-fill"))
        p.setPen(QPen(accent, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)  # stroke only; a leftover brush would fill the curve
        p.drawPath(path)

        # A handle per band, sitting on the curve at the band's frequency.
        for i, b in enumerate(self.state.bands):
            v = response_db(self.state, [b.freq])[0]
            c = QPointF(self._x(rect, b.freq), self._y(rect, v))
            r = 5.0 if i == self.active_band else 3.5
            p.setPen(QPen(accent, 1.5))
            p.setBrush(bg if b.gain == 0 else accent)
            p.drawEllipse(c, r, r)
        p.end()


class BandStrip(QWidget):
    """Gain slider + frequency knob + Q knob for one band."""

    changed = Signal(int)  # band index

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.gain_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.gain_label.setObjectName("EqValue")
        self.slider = _fader()
        self.slider.setRange(round(GAIN_MIN * 10), round(GAIN_MAX * 10))
        self.slider.setToolTip("Gain (double-click to reset)")
        self.freq = Knob()
        self.freq.setRange(0, DIAL_STEPS)
        self.freq.setToolTip("Frequency (double-click to reset)")
        self.freq_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.q = Knob(size=32)
        self.q.setRange(0, DIAL_STEPS)
        self.q.setToolTip("Q, i.e. width: higher is narrower (double-click to reset)")
        self.q_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.q_label.setObjectName("EqValue")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(2)
        lay.addWidget(self.gain_label)
        lay.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.freq, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.freq_label)
        lay.addWidget(self.q, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.q_label)

        self.slider.valueChanged.connect(lambda _: self.changed.emit(self.index))
        self.freq.valueChanged.connect(lambda _: self.changed.emit(self.index))
        self.q.valueChanged.connect(lambda _: self.changed.emit(self.index))
        self.slider.reset.connect(lambda: self.slider.setValue(0))
        self.freq.reset.connect(
            lambda: self.freq.setValue(_log_to_dial(ISO_FREQS[self.index], FREQ_MIN, FREQ_MAX)))
        self.q.reset.connect(lambda: self.q.setValue(_log_to_dial(DEFAULT_Q, Q_MIN, Q_MAX)))

    def set_values(self, freq: float, gain: float, q: float) -> None:
        for w, v in ((self.slider, round(gain * 10)),
                     (self.freq, _log_to_dial(freq, FREQ_MIN, FREQ_MAX)),
                     (self.q, _log_to_dial(q, Q_MIN, Q_MAX))):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.update_labels()

    def values(self) -> tuple[float, float, float]:
        freq = _dial_to_log(self.freq.value(), FREQ_MIN, FREQ_MAX)
        # Snap back to the exact ISO center when the knob sits on it.
        iso = ISO_FREQS[self.index]
        if self.freq.value() == _log_to_dial(iso, FREQ_MIN, FREQ_MAX):
            freq = iso
        q = _dial_to_log(self.q.value(), Q_MIN, Q_MAX)
        if self.q.value() == _log_to_dial(DEFAULT_Q, Q_MIN, Q_MAX):
            q = DEFAULT_Q
        return round(freq, 1), self.slider.value() / 10, round(q, 2)

    def update_labels(self) -> None:
        freq, gain, q = self.values()
        self.gain_label.setText(f"{gain:+.1f}")
        self.freq_label.setText(fmt_freq(freq))
        self.q_label.setText(f"Q {q:.2f}")


class EqualizerView(QWidget):
    state_changed = Signal(object)  # EqState

    def __init__(self, state: EqState, presets: PresetStore, parent=None) -> None:
        super().__init__(parent)
        self.state = state.copy()
        self.presets = presets

        self.power = button("EQ On", "power", "secondary", "Turn the equalizer on or off")
        self.power.setCheckable(True)
        self.power.toggled.connect(self._on_power)
        self.preset_box = QComboBox()
        self.preset_box.setPlaceholderText("Custom")
        self.preset_box.setMinimumWidth(170)
        self.preset_box.setAccessibleName("Preset")
        self.preset_box.setToolTip("Preset")
        self.preset_box.activated.connect(self._on_preset_chosen)
        save = button("Save", "save", "ghost", "Save these settings as a preset",
                      slot=self._save_preset)
        self.delete_btn = button("", "trash", "ghost", "Delete this preset",
                                 slot=self._delete_preset)
        reset = button("Reset", "retry", "ghost", "Back to Flat",
                       slot=lambda: self._load_preset("Flat"))
        self.header = ViewHeader("Equalizer", "10-band parametric")
        self.header.add(self.power, self.preset_box, save, self.delete_btn, reset)
        # The clipping hint lives in the header's subtitle line.
        self.peak_label = self.header.subtitle

        self.curve = ResponseCurve()

        # Preamp strip, then the ten bands.
        self.preamp = _fader()
        self.preamp.setRange(round(PREAMP_MIN * 10), round(PREAMP_MAX * 10))
        self.preamp.setToolTip("Preamp (double-click to reset)")
        self.preamp.valueChanged.connect(lambda _: self._on_preamp())
        self.preamp.reset.connect(lambda: self.preamp.setValue(0))
        self.preamp_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)

        strips = QGridLayout()
        strips.setHorizontalSpacing(space(1))
        # Mirror BandStrip's layout so the preamp fader lines up with the bands:
        # value label, fader, then the same height the two knobs take.
        pre_w = QWidget()
        pre = QVBoxLayout(pre_w)
        pre.setContentsMargins(2, 0, 2, 0)
        pre.setSpacing(2)
        self.preamp_label.setObjectName("EqValue")
        pre.addWidget(self.preamp_label)
        pre.addWidget(self.preamp, 1, Qt.AlignmentFlag.AlignHCenter)
        cap = QLabel("Preamp", alignment=Qt.AlignmentFlag.AlignCenter)
        self._preamp_foot = QWidget()
        foot = QVBoxLayout(self._preamp_foot)
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addWidget(cap, 0, Qt.AlignmentFlag.AlignTop)
        pre.addWidget(self._preamp_foot)
        strips.addWidget(pre_w, 0, 0)
        strips.setColumnMinimumWidth(1, 14)
        self.strips: list[BandStrip] = []
        for i in range(NUM_BANDS):
            strip = BandStrip(i)
            strip.changed.connect(self._on_band)
            self.strips.append(strip)
            strips.addWidget(strip, 0, i + 2)

        s0 = self.strips[0]
        # Knobs have a fixed size; QDial's sizeHint ignores that, so use the minimum.
        knob_area = sum(w.minimumHeight() or w.sizeHint().height()
                        for w in (s0.freq, s0.freq_label, s0.q, s0.q_label))
        self._preamp_foot.setFixedHeight(knob_area + 3 * s0.layout().spacing())

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(space(4), space(4), space(4), space(4))
        bl.setSpacing(space(4))
        bl.addWidget(self.curve)
        bl.addLayout(strips, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view(self.header, body))

        self._refresh_presets()
        self._show(self.state)

    # --- state <-> widgets -----------------------------------------------

    def _show(self, state: EqState, active: int | None = None) -> None:
        self.power.blockSignals(True)
        self.power.setChecked(state.enabled)
        self.power.setText("EQ On" if state.enabled else "EQ Off")
        self.power.blockSignals(False)
        self.preamp.blockSignals(True)
        self.preamp.setValue(round(state.preamp * 10))
        self.preamp.blockSignals(False)
        self.preamp_label.setText(f"{state.preamp:+.1f}")
        for strip, b in zip(self.strips, state.bands):
            strip.set_values(b.freq, b.gain, b.q)
        for w in [self.preamp, *self.strips]:
            w.setEnabled(state.enabled)
        self._sync_preset_box()
        self._refresh(active)

    def _refresh(self, active: int | None = None) -> None:
        self.curve.set_state(self.state, active)
        peak = peak_db(self.state)
        if not self.state.enabled:
            self.header.set_subtitle("Off: audio passes through unchanged")
        elif peak > 0.5:
            warn = manager_color("warning")
            self.header.set_subtitle(
                f'<span style="color:{warn}">Peak {peak:+.1f} dB: lower the preamp to avoid clipping</span>')
        else:
            name = self.state.preset or "Custom"
            self.header.set_subtitle(f"{name} · 10-band parametric")

    def _emit(self, active: int | None = None) -> None:
        self._refresh(active)
        self.state_changed.emit(self.state.copy())

    def _modified(self) -> None:
        if self.state.preset is not None:
            self.state.preset = None
            self._sync_preset_box()

    def _on_band(self, i: int) -> None:
        strip = self.strips[i]
        strip.update_labels()
        freq, gain, q = strip.values()
        b = self.state.bands[i]
        b.freq, b.gain, b.q = freq, gain, q
        self._modified()
        self._emit(active=i)

    def _on_preamp(self) -> None:
        self.state.preamp = self.preamp.value() / 10
        self.preamp_label.setText(f"{self.state.preamp:+.1f}")
        self._modified()
        self._emit()

    def _on_power(self, on: bool) -> None:
        self.state.enabled = on
        self.power.setText("EQ On" if on else "EQ Off")
        for w in [self.preamp, *self.strips]:
            w.setEnabled(on)
        self._emit()

    # --- presets ----------------------------------------------------------

    def _refresh_presets(self) -> None:
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItems(self.presets.names())
        self.preset_box.blockSignals(False)
        self._sync_preset_box()

    def _sync_preset_box(self) -> None:
        name = self.state.preset
        idx = self.preset_box.findText(name) if name else -1
        self.preset_box.setCurrentIndex(idx)
        self.delete_btn.setEnabled(bool(name) and not self.presets.is_builtin(name))

    def _on_preset_chosen(self, index: int) -> None:
        self._load_preset(self.preset_box.itemText(index))

    def _load_preset(self, name: str) -> None:
        preset = self.presets.get(name)
        if preset is None:
            return
        preset.enabled = self.state.enabled  # choosing a preset doesn't flip the power
        self.state = preset
        self._show(self.state)
        self.state_changed.emit(self.state.copy())

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:",
                                        text=self.state.preset or "")
        if not ok:
            return
        try:
            self.presets.save(name, self.state)
        except ValueError as e:
            QMessageBox.warning(self, "Save Preset", str(e))
            return
        self.state.preset = name.strip()
        self._refresh_presets()
        self.state_changed.emit(self.state.copy())

    def _delete_preset(self) -> None:
        name = self.state.preset
        if not name or self.presets.is_builtin(name):
            return
        self.presets.delete(name)
        self.state.preset = None
        self._refresh_presets()
        self.state_changed.emit(self.state.copy())


def debounce(parent: QWidget, ms: int, fn) -> QTimer:
    timer = QTimer(parent, singleShot=True, interval=ms)
    timer.timeout.connect(fn)
    return timer
