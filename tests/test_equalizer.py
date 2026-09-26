import json

import pytest

from minuett.core.equalizer import (
    BUILTIN_PRESETS, ISO_FREQS, Band, EqState, PresetStore, band_response_db, load_state,
    log_freqs, peak_db, response_db, save_state,
)


def one_band(freq=1000.0, gain=6.0, q=1.5):
    return EqState(bands=[Band(f, gain if f == freq else 0.0, q) for f in ISO_FREQS])


def test_flat_is_zero_everywhere():
    assert all(abs(v) < 1e-9 for v in response_db(EqState(), log_freqs(50)))


def test_band_hits_its_gain_at_center_and_fades():
    b = Band(1000, 6.0)
    assert band_response_db(b, 1000) == pytest.approx(6.0, abs=1e-6)
    assert abs(band_response_db(b, 50)) < 0.05
    assert abs(band_response_db(b, 15000)) < 0.1


def test_higher_q_is_narrower():
    wide, narrow = Band(1000, 6, q=0.7), Band(1000, 6, q=5)
    assert band_response_db(narrow, 1400) < band_response_db(wide, 1400)


def test_disabled_and_preamp():
    s = one_band()
    s.preamp = -3
    assert response_db(s, [1000])[0] == pytest.approx(3.0, abs=1e-6)
    s.enabled = False
    assert response_db(s, [1000]) == [0.0]


def test_peak_db_for_clip_hint():
    assert peak_db(one_band(gain=9)) == pytest.approx(9.0, abs=0.05)


def test_from_dict_clamps_and_pads():
    s = EqState.from_dict({"preamp": 99, "bands": [{"freq": 5, "gain": -40, "q": 100}]})
    assert s.preamp == 12 and len(s.bands) == 10
    assert (s.bands[0].freq, s.bands[0].gain, s.bands[0].q) == (20, -12, 8)
    assert s.bands[1].freq == 62


def test_state_roundtrip(tmp_path):
    p = tmp_path / "eq.json"
    s = one_band(gain=4.5)
    s.preamp, s.enabled = -2, False
    save_state(p, s)
    assert load_state(p) == s
    p.write_text("{not json")
    assert load_state(p) == EqState()


def test_presets(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    assert store.names()[:5] == ["Flat", "Rock", "Vocal", "Bass Boost", "Treble Cut"]
    with pytest.raises(ValueError):
        store.save("Rock", EqState())
    mine = one_band(gain=3)
    store.save("Mine", mine)
    again = PresetStore(tmp_path / "presets.json")
    assert again.get("Mine").bands == mine.bands and again.get("Mine").preset == "Mine"
    again.delete("Mine")
    assert "Mine" not in PresetStore(tmp_path / "presets.json").names()
    # Loading a preset hands out a copy, so edits don't change the preset.
    rock = store.get("Rock")
    rock.bands[0].gain = 0
    assert BUILTIN_PRESETS["Rock"].bands[0].gain == 4.5


def test_corrupt_preset_file_is_ignored(tmp_path):
    p = tmp_path / "presets.json"
    p.write_text(json.dumps({"Bad": "nope", "Flat": {}}))
    assert PresetStore(p).names() == list(BUILTIN_PRESETS)


# --- against the real GStreamer element --------------------------------------

gst = pytest.importorskip("gi.repository.Gst")


@pytest.mark.parametrize("state", [
    one_band(1000, 9.0, 1.5),
    one_band(250, -8.0, 4.0),
    BUILTIN_PRESETS["Rock"],
    EqState(bands=[Band(3000 if f == 2000 else f, 12 if f == 2000 else 0, 0.7) for f in ISO_FREQS]),
], ids=["boost-1k", "narrow-cut", "rock", "moved-band"])
def test_curve_matches_what_gstreamer_does(state):
    from _eqmeasure import measure
    for f in (100, 700, 1500, 3000, 8000):
        assert measure(state, f) == pytest.approx(response_db(state, [f])[0], abs=0.05), f


def test_off_is_bypass():
    from _eqmeasure import measure
    s = BUILTIN_PRESETS["Bass Boost"].copy()
    s.enabled = False
    assert measure(s, 60) == pytest.approx(0.0, abs=0.05)
