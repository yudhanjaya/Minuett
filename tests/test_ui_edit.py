"""Offscreen checks of inline and batch tag editing through the Qt layer."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402

from antiphon.core.library.db import LibraryDB  # noqa: E402
from antiphon.core.library.scanner import scan  # noqa: E402
from antiphon.core.library.tags import read_tags  # noqa: E402
from antiphon.ui.dialogs.tag_editor import MULTIPLE, TagEditorDialog  # noqa: E402
from antiphon.ui.views.library_model import COLUMNS, LibraryModel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def lib(tmp_path, tone):
    tone(tmp_path / "1.mp3", title="One", artist="A", album="X")
    tone(tmp_path / "2.opus", title="Two", artist="A", album="Y")
    db = LibraryDB(":memory:")
    scan(db, [tmp_path])
    return db


def col(attr):
    return [a for a, _ in COLUMNS].index(attr)


def test_inline_edit_writes_file(app, lib):
    model = LibraryModel(lib)
    edited = []
    model.tracks_edited.connect(edited.append)
    idx = model.index(0, col("artist"))
    assert model.flags(idx) & Qt.ItemFlag.ItemIsEditable
    assert not model.flags(model.index(0, col("bitrate"))) & Qt.ItemFlag.ItemIsEditable
    assert model.setData(idx, "New Artist")
    t = model.tracks[0]
    assert t.artist == "New Artist"
    assert read_tags(t.path).tags["artist"] == "New Artist"
    assert edited == [[t.id]]


def test_inline_edit_bad_year_reports_and_keeps(app, lib):
    model = LibraryModel(lib)
    errors = []
    model.edit_failed.connect(errors.append)
    assert not model.setData(model.index(0, col("year")), "soon")
    assert errors and model.tracks[0].year is None


def test_batch_dialog_only_writes_touched_fields(app, lib):
    tracks = lib.all_tracks()
    dlg = TagEditorDialog(lib, tracks)
    assert dlg.edits["artist"].text() == "A"
    assert dlg.edits["album"].text() == "" and dlg.edits["album"].placeholderText() == MULTIPLE
    # Simulate typing in genre only.
    dlg.edits["genre"].setText("Jazz")
    dlg.edits["genre"].textEdited.emit("Jazz")
    assert dlg.changes() == {"genre": "Jazz"}
    dlg._save()
    assert sorted(dlg.result_.updated) == sorted(t.id for t in tracks)
    after = {t.title: t for t in lib.all_tracks()}
    assert after["One"].album == "X" and after["Two"].album == "Y"  # mixed field untouched
    assert all(t.genre == "Jazz" for t in after.values())
