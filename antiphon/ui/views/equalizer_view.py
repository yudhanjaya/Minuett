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
    QComboBox, QDial, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from antiphon.core.equalizer import (
    DEFAULT_Q, FREQ_MAX, FREQ_MIN, GAIN_MAX, GAIN_MIN, ISO_FREQS, NUM_BANDS, PREAMP_MAX,
    PREAMP_MIN, Q_MAX, Q_MIN, EqState, PresetStore, log_freqs, peak_db, response_db,
)

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


class ResettingSlider(QSlider):
    reset = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset.emit()


class ResettingDial(QDial):
    reset = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset.emit()


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
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        rect = QRectF(self.rect()).adjusted(34, 8, -8, -18)
        grid = QPen(pal.mid().color(), 1)
        text = pal.placeholderText().color()
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
            pen = QPen(pal.mid().color(), 1.6 if db == 0 else 1)
            p.setPen(pen)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(text)
            p.drawText(QRectF(0, y - 7, 30, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{db:+d}")

        values = response_db(self.state, self._freqs)
        path = QPainterPath()
        for i, (f, v) in enumerate(zip(self._freqs, values)):
            pt = QPointF(self._x(rect, f), self._y(rect, v))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)

        accent = pal.highlight().color()
        if not self.state.enabled:
            accent = pal.mid().color()
        fill = QPainterPath(path)
        fill.lineTo(rect.right(), self._y(rect, 0))
        fill.lineTo(rect.left(), self._y(rect, 0))
        fill.closeSubpath()
        tint = QColor(accent)
        tint.setAlpha(45)
        p.fillPath(fill, tint)
        p.setPen(QPen(accent, 2.2))
        p.drawPath(path)

        # A handle per band, sitting on the curve at the band's frequency.
        for i, b in enumerate(self.state.bands):
            v = response_db(self.state, [b.freq])[0]
            c = QPointF(self._x(rect, b.freq), self._y(rect, v))
            r = 5.0 if i == self.active_band else 3.5
            p.setPen(QPen(accent, 1.5))
            p.setBrush(pal.base() if b.gain == 0 else accent)
            p.drawEllipse(c, r, r)
        p.end()


class BandStrip(QWidget):
    """Gain slider + frequency knob + Q knob for one band."""

    changed = Signal(int)  # band index

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.gain_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.slider = ResettingSlider(Qt.Orientation.Vertical)
        self.slider.setRange(round(GAIN_MIN * 10), round(GAIN_MAX * 10))
        self.slider.setPageStep(10)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
        self.slider.setTickInterval(60)
        self.slider.setMinimumHeight(150)
        self.slider.setToolTip("Gain (double-click to reset)")
        self.freq = ResettingDial()
        self.freq.setRange(0, DIAL_STEPS)
        self.freq.setToolTip("Frequency (double-click to reset)")
        self.freq_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.q = ResettingDial()
        self.q.setRange(0, DIAL_STEPS)
        self.q.setToolTip("Q, i.e. width: higher is narrower (double-click to reset)")
        self.q_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        for dial in (self.freq, self.q):
            dial.setFixedSize(40, 40)
            dial.setNotchesVisible(False)

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

        self.power = QPushButton("EQ On", checkable=True)
        self.power.toggled.connect(self._on_power)
        self.preset_box = QComboBox()
        self.preset_box.setPlaceholderText("Custom")
        self.preset_box.setMinimumWidth(160)
        self.preset_box.activated.connect(self._on_preset_chosen)
        save = QPushButton("Save Preset…", clicked=self._save_preset)
        self.delete_btn = QPushButton("Delete Preset", clicked=self._delete_preset)
        reset = QPushButton("Reset", clicked=lambda: self._load_preset("Flat"))
        self.peak_label = QLabel()

        top = QHBoxLayout()
        top.addWidget(self.power)
        top.addSpacing(12)
        top.addWidget(QLabel("Preset"))
        top.addWidget(self.preset_box)
        top.addWidget(save)
        top.addWidget(self.delete_btn)
        top.addWidget(reset)
        top.addStretch(1)
        top.addWidget(self.peak_label)

        self.curve = ResponseCurve()

        # Preamp strip, then the ten bands.
        self.preamp = ResettingSlider(Qt.Orientation.Vertical)
        self.preamp.setRange(round(PREAMP_MIN * 10), round(PREAMP_MAX * 10))
        self.preamp.setPageStep(10)
        self.preamp.setTickPosition(QSlider.TickPosition.TicksBothSides)
        self.preamp.setTickInterval(60)
        self.preamp.setMinimumHeight(150)
        self.preamp.setToolTip("Preamp (double-click to reset)")
        self.preamp.valueChanged.connect(lambda _: self._on_preamp())
        self.preamp.reset.connect(lambda: self.preamp.setValue(0))
        self.preamp_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)

        strips = QGridLayout()
        strips.setHorizontalSpacing(4)
        pre = QVBoxLayout()
        pre.addWidget(self.preamp_label)
        pre.addWidget(self.preamp, 1, Qt.AlignmentFlag.AlignHCenter)
        cap = QLabel("Preamp", alignment=Qt.AlignmentFlag.AlignCenter)
        pre.addWidget(cap)
        pre.addStretch(0)
        strips.addLayout(pre, 0, 0)
        strips.setColumnMinimumWidth(1, 14)
        self.strips: list[BandStrip] = []
        for i in range(NUM_BANDS):
            strip = BandStrip(i)
            strip.changed.connect(self._on_band)
            self.strips.append(strip)
            strips.addWidget(strip, 0, i + 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.addLayout(top)
        layout.addWidget(self.curve)
        layout.addLayout(strips, 1)

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
        if self.state.enabled and peak > 0.5:
            self.peak_label.setText(f"Peak {peak:+.1f} dB: lower the preamp to avoid clipping")
        else:
            self.peak_label.setText("")

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
