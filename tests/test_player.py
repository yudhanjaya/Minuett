import time

import pytest

gst = pytest.importorskip("gi.repository.Gst")

from antiphon.core.player import Player, QueueItem, Repeat, State  # noqa: E402


def silent_player() -> Player:
    p = Player()
    sink = gst.ElementFactory.make("fakesink", None)
    sink.set_property("sync", True)
    p.playbin.set_property("audio-sink", sink)
    return p


def pump(player, until, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        player.poll()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_gapless_advance_then_stop(tmp_path, tone):
    files = [tone(tmp_path / f"{i}.opus", seconds=0.6) for i in range(3)]
    p = silent_player()
    changes = []
    p.track_changed.connect(lambda i, item: changes.append(i))
    p.set_queue([QueueItem(str(f)) for f in files])
    try:
        assert pump(p, lambda: p.state is State.PLAYING)
        assert pump(p, lambda: changes and changes[-1] == -1)
        # 0 from play_index, 1 and 2 from gapless STREAM_START, -1 at EOS
        assert changes == [0, 1, 2, -1]
        assert p.state is State.STOPPED
    finally:
        p.shutdown()


def test_manual_next_prev_and_repeat(tmp_path, tone):
    files = [tone(tmp_path / f"{i}.mp3", seconds=3) for i in range(2)]
    p = silent_player()
    p.set_queue([QueueItem(str(f)) for f in files])
    try:
        assert pump(p, lambda: p.state is State.PLAYING)
        p.next()
        assert p.index == 1
        p.next()
        assert p.state is State.STOPPED
        p.repeat = Repeat.ALL
        p.play_index(1)
        p.next()
        assert p.index == 0
        p.previous()
        assert p.index == 1
    finally:
        p.shutdown()


def test_pause_resume(tmp_path, tone):
    f = tone(tmp_path / "a.flac", seconds=3)
    p = silent_player()
    p.set_queue([QueueItem(str(f))])
    try:
        assert pump(p, lambda: p.state is State.PLAYING)
        p.toggle()
        assert pump(p, lambda: p.state is State.PAUSED)
        p.toggle()
        assert pump(p, lambda: p.state is State.PLAYING)
    finally:
        p.shutdown()


def test_bad_file_is_skipped(tmp_path, tone):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"garbage" * 100)
    good = tone(tmp_path / "good.mp3", seconds=2)
    p = silent_player()
    errors = []
    p.error.connect(errors.append)
    p.set_queue([QueueItem(str(bad)), QueueItem(str(good))])
    try:
        assert pump(p, lambda: p.index == 1 and p.state is State.PLAYING)
        assert errors
    finally:
        p.shutdown()


def test_plays_gapless_through_equalizer_with_live_changes(tmp_path, tone):
    from antiphon.core.eq_filter import EqualizerFilter
    from antiphon.core.equalizer import BUILTIN_PRESETS

    files = [tone(tmp_path / f"{i}.mp3", seconds=0.8) for i in range(2)]
    p = silent_player()
    eq = EqualizerFilter()
    p.set_audio_filter(eq.bin)
    changes = []
    p.track_changed.connect(lambda i, item: changes.append(i))
    p.set_queue([QueueItem(str(f)) for f in files])
    try:
        assert pump(p, lambda: p.state is State.PLAYING)
        eq.apply(BUILTIN_PRESETS["Rock"])       # while playing
        rock_off = BUILTIN_PRESETS["Rock"].copy()
        rock_off.enabled = False
        eq.apply(rock_off)
        assert pump(p, lambda: changes and changes[-1] == -1)
        assert changes == [0, 1, -1]
    finally:
        p.shutdown()
