"""The GStreamer side of the equalizer: a bin for playbin's ``audio-filter``.

    audioconvert ! volume name=preamp ! equalizer-nbands num-bands=10 ! audioconvert

Band properties can be changed while playing. "Off" is a bypass done by
zeroing gains and preamp rather than relinking the pipeline, so toggling
never glitches.
"""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .equalizer import NUM_BANDS, EqState  # noqa: E402

Gst.init(None)


class EqualizerFilter:
    def __init__(self) -> None:
        self.bin = Gst.parse_bin_from_description(
            f"audioconvert ! volume name=preamp ! "
            f"equalizer-nbands name=eq num-bands={NUM_BANDS} ! audioconvert",
            True)  # ghost the unlinked src/sink pads
        self.bin.set_name("antiphon-eq")
        self.preamp = self.bin.get_by_name("preamp")
        self.eq = self.bin.get_by_name("eq")
        peak = self._peak_type()
        for i in range(NUM_BANDS):
            band = self.eq.get_child_by_index(i)
            if peak is not None:
                band.set_property("type", peak)  # GStreamer defaults ends to shelves

    def _peak_type(self):
        band = self.eq.get_child_by_index(0)
        enum_type = band.find_property("type").value_type
        # Enum value 0 is "peak" in GstIirEqualizerBandType.
        try:
            return enum_type.pytype(0) if enum_type.pytype else 0
        except (TypeError, ValueError):
            return 0

    def apply(self, state: EqState) -> None:
        on = state.enabled
        self.preamp.set_property("volume", 10.0 ** (state.preamp / 20.0) if on else 1.0)
        for i, b in enumerate(state.bands[:NUM_BANDS]):
            band = self.eq.get_child_by_index(i)
            band.set_property("freq", float(b.freq))
            band.set_property("bandwidth", float(b.bandwidth))
            band.set_property("gain", float(b.gain) if on else 0.0)
