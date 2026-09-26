import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from antiphon.core.equalizer import BUILTIN_PRESETS, DEFAULT_Q, EqState, PresetStore  # noqa: E402
from antiphon.ui.views.equalizer_view import EqualizerView  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app, tmp_path):
    v = EqualizerView(EqState(), PresetStore(tmp_path / "p.json"))
    v.changes = []
    v.state_changed.connect(v.changes.append)
    return v


def test_defaults_are_iso_graphic_eq(view):
    assert [s.values() for s in view.strips][:2] == [(31.0, 0.0, DEFAULT_Q), (62.0, 0.0, DEFAULT_Q)]
    assert view.preset_box.currentText() == "Flat"


def test_slider_drag_updates_state_and_marks_custom(view):
    view.strips[5].slider.setValue(65)
    st = view.changes[-1]
    assert st.bands[5].gain == 6.5 and st.preset is None
    assert view.preset_box.currentIndex() == -1   # shows "Custom"
    assert "Peak +6.5" in view.peak_label.text()
    view.preamp.setValue(-70)
    assert "Peak" not in view.peak_label.text()


def test_knobs_move_frequency_and_q(view):
    view.strips[3].freq.setValue(view.strips[3].freq.value() + 60)
    view.strips[3].q.setValue(900)
    f, _, q = view.changes[-1].bands[3].freq, None, view.changes[-1].bands[3].q
    assert f > 300 and q > 5
    view.strips[3].freq.reset.emit()
    view.strips[3].q.reset.emit()
    assert (view.state.bands[3].freq, view.state.bands[3].q) == (250.0, DEFAULT_Q)


def test_preset_load_save_delete(view, monkeypatch):
    view._load_preset("Rock")
    assert [b.gain for b in view.state.bands] == [b.gain for b in BUILTIN_PRESETS["Rock"].bands]
    assert view.strips[0].slider.value() == 45 and view.preamp.value() == -45
    assert not view.delete_btn.isEnabled()
    monkeypatch.setattr("antiphon.ui.views.equalizer_view.QInputDialog.getText",
                        lambda *a, **k: ("Mine", True))
    view._save_preset()
    assert view.preset_box.currentText() == "Mine" and view.delete_btn.isEnabled()
    view._delete_preset()
    assert view.preset_box.findText("Mine") == -1


def test_power_toggle(view):
    view._load_preset("Bass Boost")
    view.power.setChecked(False)
    assert view.changes[-1].enabled is False and not view.strips[0].slider.isEnabled()
    view._load_preset("Rock")
    assert view.state.enabled is False  # presets don't turn it back on
