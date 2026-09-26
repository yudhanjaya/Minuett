"""Winamp-style spectrum analyzer maths. Pure Python, no Qt or GStreamer.

GStreamer's ``spectrum`` element reports magnitudes (dB) for evenly spaced
linear frequency bands. Music is perceived logarithmically, so we group
those bands into log-spaced bars, map dB to 0..1, and animate them the way
Winamp did: bars jump up instantly and fall under a constant decay; each
bar's peak marker holds briefly, then drops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FLOOR_DB = -72.0     # quiet end of the display
CEIL_DB = -20.0      # loud end (music rarely sits above this in any one band)
F_MIN, F_MAX = 40.0, 16000.0


def bar_ranges(n_bars: int, rate: int, n_bands: int,
               f_min: float = F_MIN, f_max: float = F_MAX) -> list[tuple[int, int]]:
    """For each bar, the [lo, hi) slice of linear spectrum bands it covers.

    Bars are log-spaced between f_min and f_max; every bar covers at least
    one band (low bars would otherwise be empty at coarse resolutions).
    """
    nyquist = rate / 2
    f_max = min(f_max, nyquist * 0.98)
    band_hz = nyquist / n_bands
    ranges = []
    lo_prev = 0
    for i in range(n_bars):
        f_lo = f_min * (f_max / f_min) ** (i / n_bars)
        f_hi = f_min * (f_max / f_min) ** ((i + 1) / n_bars)
        lo = max(int(f_lo / band_hz), lo_prev)
        hi = max(int(math.ceil(f_hi / band_hz)), lo + 1)
        hi = min(hi, n_bands)
        lo = min(lo, hi - 1)
        ranges.append((lo, hi))
        lo_prev = lo
    return ranges


def to_level(db: float, floor: float = FLOOR_DB, ceil: float = CEIL_DB) -> float:
    return min(1.0, max(0.0, (db - floor) / (ceil - floor)))


TILT_DB_PER_OCTAVE = 3.0   # "pink" compensation, pivoting at 1 kHz


def bars_from_magnitudes(mags: list[float], ranges: list[tuple[int, int]],
                         band_hz: float | None = None,
                         tilt: float = TILT_DB_PER_OCTAVE) -> list[float]:
    """Loudest band in each bar's range, mapped to 0..1.

    Music carries less energy per band as frequency rises, so without help
    the treble bars barely move. With ``band_hz`` given, each bar is tilted
    by ``tilt`` dB per octave around 1 kHz (the usual analyzer compensation).
    """
    out = []
    for lo, hi in ranges:
        if hi <= lo:
            out.append(0.0)
            continue
        db = max(mags[lo:hi])
        if band_hz:
            centre = max(band_hz, (lo + hi) / 2 * band_hz)
            db += tilt * math.log2(centre / 1000.0)
        out.append(to_level(db))
    return out


@dataclass
class Analyzer:
    """Holds bar and peak levels and animates them over time."""

    n_bars: int = 20
    fall_per_s: float = 1.6       # bar height lost per second (full height = 1.0)
    peak_hold_s: float = 0.45
    peak_fall_per_s: float = 0.9
    bars: list[float] = field(default_factory=list)
    peaks: list[float] = field(default_factory=list)
    _hold: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.resize(self.n_bars)

    def resize(self, n_bars: int) -> None:
        if n_bars == len(self.bars):
            return
        self.n_bars = n_bars
        self.bars = [0.0] * n_bars
        self.peaks = [0.0] * n_bars
        self._hold = [0.0] * n_bars

    def feed(self, levels: list[float]) -> None:
        """New measurement: bars rise to it instantly; peaks rise and re-arm their hold."""
        for i, v in enumerate(levels[: self.n_bars]):
            if v >= self.bars[i]:
                self.bars[i] = v
            if v >= self.peaks[i]:
                self.peaks[i] = v
                self._hold[i] = self.peak_hold_s

    def step(self, dt: float) -> bool:
        """Advance the fall animation by dt seconds. Returns True while anything is visible."""
        alive = False
        for i in range(self.n_bars):
            self.bars[i] = max(0.0, self.bars[i] - self.fall_per_s * dt)
            # Hold first; whatever time is left after the hold ends is spent falling.
            held = min(self._hold[i], dt)
            self._hold[i] -= held
            falling = dt - held
            if falling > 0:
                self.peaks[i] = max(self.bars[i], self.peaks[i] - self.peak_fall_per_s * falling)
            alive = alive or self.bars[i] > 0.001 or self.peaks[i] > 0.001
        return alive

    def silence(self) -> None:
        """Let everything fall to zero (on pause/stop)."""
        for i in range(self.n_bars):
            self._hold[i] = 0.0


def parse_magnitudes(structure_text: str) -> list[float]:
    """Magnitudes from a spectrum message's structure string.

    PyGObject can't convert the GstValueList in ``magnitude`` directly, so we
    read it from the structure's text form: ``magnitude=(float){ -60, -59.5, … }``.
    """
    start = structure_text.find("magnitude=(float){")
    if start < 0:
        return []
    start = structure_text.index("{", start) + 1
    end = structure_text.index("}", start)
    out = []
    for tok in structure_text[start:end].split(","):
        tok = tok.strip()
        if tok:
            try:
                out.append(float(tok))
            except ValueError:
                out.append(FLOOR_DB)
    return out
