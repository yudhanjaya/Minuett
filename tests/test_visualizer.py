import pytest

from minuett.core.visualizer import (
    Analyzer, bar_ranges, bars_from_magnitudes, parse_magnitudes, to_level,
)


def test_bar_ranges_are_log_spaced_and_non_empty():
    r = bar_ranges(24, 48000, 512)
    assert len(r) == 24 and all(hi > lo for lo, hi in r)
    widths = [hi - lo for lo, hi in r]
    assert widths[-1] > widths[len(widths) // 2] >= widths[0]   # wider bands up high
    assert r[-1][1] <= 512
    assert r == sorted(r)


def test_levels_and_bars():
    assert to_level(-100) == 0.0 and to_level(0) == 1.0
    mags = [-80.0] * 512
    mags[21] = -12.0   # ~1 kHz at 48 kHz / 512 bands
    ranges = bar_ranges(24, 48000, 512)
    bars = bars_from_magnitudes(mags, ranges)
    lit = [i for i, b in enumerate(bars) if b > 0.5]
    assert lit and all(ranges[i][0] <= 21 < ranges[i][1] for i in lit)


def test_analyzer_rises_instantly_falls_gradually_and_holds_peaks():
    a = Analyzer(3, fall_per_s=1.0, peak_hold_s=0.5, peak_fall_per_s=1.0)
    a.feed([1.0, 0.5, 0.0])
    assert a.bars == [1.0, 0.5, 0.0] and a.peaks[0] == 1.0
    a.step(0.25)
    assert a.bars[0] == pytest.approx(0.75) and a.peaks[0] == 1.0      # peak held
    a.step(0.5)
    assert a.bars[0] == pytest.approx(0.25) and a.peaks[0] < 1.0       # hold over, peak falls
    assert a.peaks[0] >= a.bars[0]
    assert a.step(5.0) is False and max(a.bars + a.peaks) == 0.0


def test_parse_magnitudes_from_structure_text():
    text = ("spectrum, endtime=(guint64)1, timestamp=(guint64)0, "
            "magnitude=(float){ -60, -59.5, -12.25 };")
    assert parse_magnitudes(text) == [-60.0, -59.5, -12.25]
    assert parse_magnitudes("spectrum, rms=1;") == []


def test_player_emits_spectrum_in_sync(tmp_path, tone):
    """A 1 kHz tone through the real EQ bin lights the bars around 1 kHz."""
    import time
    import subprocess
    gst = pytest.importorskip("gi.repository.Gst")
    from minuett.core.eq_filter import EqualizerFilter
    from minuett.core.player import Player, QueueItem
    f = tmp_path / "k.opus"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=1000:duration=2", "-c:a", "libopus", str(f)], check=True)
    p = Player()
    sink = gst.ElementFactory.make("fakesink")
    sink.set_property("sync", True)
    p.playbin.set_property("audio-sink", sink)
    p.set_audio_filter(EqualizerFilter().bin)
    frames = []
    p.spectrum.connect(lambda m, r: frames.append((m, r)))
    p.set_queue([QueueItem(str(f))])
    end = time.time() + 1.2
    try:
        while time.time() < end:
            p.poll()
            time.sleep(0.02)
    finally:
        p.shutdown()
    assert len(frames) > 15   # ~30 per second
    mags, rate = frames[-1]
    ranges = bar_ranges(24, rate, len(mags))
    bars = bars_from_magnitudes(mags, ranges)
    loudest = max(range(24), key=lambda i: bars[i])
    lo, hi = ranges[loudest]
    assert lo * rate / 2 / len(mags) <= 1000 <= hi * rate / 2 / len(mags)


def test_treble_tilt_lifts_high_bars():
    ranges = bar_ranges(10, 48000, 2048)
    flat = [-50.0] * 2048
    plain = bars_from_magnitudes(flat, ranges)
    tilted = bars_from_magnitudes(flat, ranges, band_hz=24000 / 2048)
    assert plain[0] == plain[-1]
    assert tilted[-1] > plain[-1] and tilted[0] < plain[0]
