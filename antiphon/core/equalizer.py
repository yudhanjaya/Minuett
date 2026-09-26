"""10-band parametric equalizer: band model, presets, and response-curve math.

Pure Python, no GStreamer or Qt. :mod:`antiphon.core.eq_filter` applies an
:class:`EqState` to GStreamer's ``equalizer-nbands``.

The curve math reproduces GStreamer's own peaking filter (gstiirequalizer.c)
rather than the textbook RBJ cookbook one: gain is scaled as 10^(dB/40), and
the bandwidth is set in Hz and warped with tan(bw/2). The two differ in how
wide a band is at a given Q, so matching GStreamer means the drawn curve is
what you actually hear. tests/test_equalizer.py measures the real element to
keep this honest.
"""

from __future__ import annotations

import cmath
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

ISO_FREQS = (31.0, 62.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
NUM_BANDS = len(ISO_FREQS)

GAIN_MIN, GAIN_MAX = -12.0, 12.0
PREAMP_MIN, PREAMP_MAX = -12.0, 12.0
FREQ_MIN, FREQ_MAX = 20.0, 20000.0
Q_MIN, Q_MAX = 0.3, 8.0
DEFAULT_Q = 1.5  # GStreamer's own default spacing for 10 bands is ~Q 1.5
DEFAULT_RATE = 48000


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class Band:
    freq: float
    gain: float = 0.0   # dB
    q: float = DEFAULT_Q

    @property
    def bandwidth(self) -> float:
        """Bandwidth in Hz, which is what GStreamer's band takes."""
        return self.freq / self.q

    def clamped(self) -> "Band":
        return Band(_clamp(float(self.freq), FREQ_MIN, FREQ_MAX),
                    _clamp(float(self.gain), GAIN_MIN, GAIN_MAX),
                    _clamp(float(self.q), Q_MIN, Q_MAX))


def default_bands() -> list[Band]:
    return [Band(f) for f in ISO_FREQS]


@dataclass
class EqState:
    enabled: bool = True
    preamp: float = 0.0            # dB
    bands: list[Band] = field(default_factory=default_bands)
    preset: str | None = "Flat"    # name of the preset last loaded, if unmodified

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EqState":
        raw = d.get("bands") or []
        bands = [Band(**b).clamped() for b in raw[:NUM_BANDS]]
        bands += default_bands()[len(bands):]
        return cls(
            enabled=bool(d.get("enabled", True)),
            preamp=_clamp(float(d.get("preamp", 0.0)), PREAMP_MIN, PREAMP_MAX),
            bands=bands,
            preset=d.get("preset"),
        )

    def copy(self) -> "EqState":
        return EqState.from_dict(self.to_dict())


# --- response math -----------------------------------------------------------

def peak_coefficients(band: Band, rate: int = DEFAULT_RATE) -> tuple[float, ...]:
    """(a0, a1, a2, b1, b2) in GStreamer's convention:
    y[n] = a0 x[n] + a1 x[n-1] + a2 x[n-2] + b1 y[n-1] + b2 y[n-2]."""
    if band.gain == 0.0:
        return (1.0, 0.0, 0.0, 0.0, 0.0)
    gain = 10.0 ** (band.gain / 40.0)
    nyquist = rate / 2.0
    omega = math.pi if band.freq >= nyquist else 2.0 * math.pi * band.freq / rate
    width = band.bandwidth
    if width <= 0:
        return (1.0, 0.0, 0.0, 0.0, 0.0)
    bw = (math.pi - math.pi / rate) if width / rate >= 0.5 else 2.0 * math.pi * width / rate
    alpha = math.tan(bw / 2.0)
    alpha1, alpha2 = alpha * gain, alpha / gain
    b0 = 1.0 + alpha2
    c = math.cos(omega)
    return ((1.0 + alpha1) / b0, (-2.0 * c) / b0, (1.0 - alpha1) / b0,
            (2.0 * c) / b0, -(1.0 - alpha2) / b0)


def band_response_db(band: Band, freq: float, rate: int = DEFAULT_RATE) -> float:
    a0, a1, a2, b1, b2 = peak_coefficients(band, rate)
    z1 = cmath.exp(-1j * 2.0 * math.pi * freq / rate)
    z2 = z1 * z1
    h = (a0 + a1 * z1 + a2 * z2) / (1.0 - b1 * z1 - b2 * z2)
    return 20.0 * math.log10(max(abs(h), 1e-12))


def response_db(state: EqState, freqs: list[float], rate: int = DEFAULT_RATE) -> list[float]:
    """Combined response (preamp + all bands) in dB at each frequency."""
    if not state.enabled:
        return [0.0 for _ in freqs]
    active = [b for b in state.bands if b.gain != 0.0]
    return [state.preamp + sum(band_response_db(b, f, rate) for b in active) for f in freqs]


def log_freqs(n: int = 200, lo: float = FREQ_MIN, hi: float = FREQ_MAX) -> list[float]:
    step = (math.log10(hi) - math.log10(lo)) / (n - 1)
    return [10 ** (math.log10(lo) + i * step) for i in range(n)]


def peak_db(state: EqState, rate: int = DEFAULT_RATE) -> float:
    """Highest point of the curve, for the clipping hint next to the preamp."""
    return max(response_db(state, log_freqs(400), rate))


# --- presets -----------------------------------------------------------------

def _preset(gains: list[float], preamp: float = 0.0) -> EqState:
    return EqState(preamp=preamp, bands=[Band(f, g) for f, g in zip(ISO_FREQS, gains)])


BUILTIN_PRESETS: dict[str, EqState] = {
    "Flat": _preset([0] * 10),
    "Rock": _preset([4.5, 4.0, 3.0, 1.0, -1.0, -1.5, 0.5, 2.5, 3.5, 4.0], preamp=-4.5),
    "Vocal": _preset([-3.0, -2.0, -1.0, 0.0, 1.5, 3.0, 3.5, 2.5, 1.0, 0.0], preamp=-3.5),
    "Bass Boost": _preset([6.0, 5.5, 4.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], preamp=-6.0),
    "Treble Cut": _preset([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0, -3.0, -6.0, -8.0]),
}


class PresetStore:
    """Built-in presets plus user presets in a JSON file. Built-ins are read-only."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.user: dict[str, EqState] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.user = {name: EqState.from_dict(d) for name, d in data.items()
                         if name not in BUILTIN_PRESETS}
        except (OSError, ValueError, TypeError, AttributeError):
            self.user = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({n: s.to_dict() for n, s in self.user.items()}, indent=2))
        tmp.replace(self.path)

    def names(self) -> list[str]:
        return list(BUILTIN_PRESETS) + sorted(self.user, key=str.casefold)

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

    def get(self, name: str) -> EqState | None:
        state = BUILTIN_PRESETS.get(name) or self.user.get(name)
        if state is None:
            return None
        out = state.copy()
        out.preset = name
        return out

    def save(self, name: str, state: EqState) -> None:
        name = name.strip()
        if not name:
            raise ValueError("preset name can't be empty")
        if self.is_builtin(name):
            raise ValueError(f"“{name}” is a built-in preset; choose another name")
        stored = state.copy()
        stored.enabled, stored.preset = True, None
        self.user[name] = stored
        self._save()

    def delete(self, name: str) -> None:
        if self.user.pop(name, None) is not None:
            self._save()


def load_state(path: Path) -> EqState:
    try:
        return EqState.from_dict(json.loads(Path(path).read_text()))
    except (OSError, ValueError, TypeError, AttributeError):
        return EqState()


def save_state(path: Path, state: EqState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2))
    tmp.replace(path)
