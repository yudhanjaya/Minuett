"""Measure the gain GStreamer's EQ bin applies to a sine at ``freq``."""

import array
import math

from minuett.core.eq_filter import EqualizerFilter
from minuett.core.equalizer import EqState
from gi.repository import Gst


def _make(factory, **props):
    e = Gst.ElementFactory.make(factory)
    for k, v in props.items():
        e.set_property(k.replace("_", "-"), v)
    return e


def measure(state: EqState, freq: float, rate: int = 48000) -> float:
    eq = EqualizerFilter()
    eq.apply(state)
    pipe = Gst.Pipeline()
    chain = [
        _make("audiotestsrc", freq=float(freq), volume=0.1, num_buffers=30, samplesperbuffer=1600),
        _make("capsfilter", caps=Gst.Caps.from_string(
            f"audio/x-raw,format=F32LE,rate={rate},channels=1")),
        eq.bin,
        _make("capsfilter", caps=Gst.Caps.from_string("audio/x-raw,format=F32LE")),
        _make("appsink", sync=False),
    ]
    for e in chain:
        pipe.add(e)
    for a, b in zip(chain, chain[1:]):
        assert a.link(b)
    pipe.set_state(Gst.State.PLAYING)
    data = array.array("f")
    while (sample := chain[-1].emit("try-pull-sample", 5 * Gst.SECOND)) is not None:
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        data.frombytes(bytes(info.data))
        buf.unmap(info)
    pipe.set_state(Gst.State.NULL)
    tail = data[len(data) // 3:]  # skip the filter's settling time
    rms = math.sqrt(sum(x * x for x in tail) / len(tail))
    return 20 * math.log10(rms / (0.1 / math.sqrt(2)))
