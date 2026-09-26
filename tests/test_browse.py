import os

import pytest

from minuett.core.library.browse import ARRANGEMENTS, build_tree, matcher
from minuett.core.library.db import LibraryDB, Track


def tracks():
    return [
        Track(path="/1", id=1, artist="Daft Punk", album="Discovery", year=2001, genre="House",
              source_playlist="Road Trip", date_added="2026-09-01T00:00:00"),
        Track(path="/2", id=2, artist="daft punk", album="Homework", year=1997, genre="House",
              source_playlist="Road Trip", date_added="2026-08-01T00:00:00"),
        Track(path="/3", id=3, artist="Air", album="Moon Safari", year=1998,
              source_playlist="Chill", date_added="2026-09-02T00:00:00"),
        Track(path="/4", id=4, title="untagged"),
    ]


def labels(nodes):
    return [(n.label, n.count) for n in nodes]


def test_every_arrangement_builds():
    for name in ARRANGEMENTS:
        assert sum(n.count for n in build_tree(tracks(), name)) == 4


def test_playlist_first_then_artist():
    tree = build_tree(tracks(), "Playlist")
    assert labels(tree) == [("Chill", 1), ("Road Trip", 2), ("Not from a playlist", 1)]
    assert labels(tree[1].children) == [("Daft Punk", 1), ("daft punk", 1)]


def test_years_newest_first_unknown_last():
    tree = build_tree(tracks(), "Year")
    assert [n.label for n in tree] == ["2000s", "1990s", "Unknown"]
    assert [n.label for n in tree[1].children] == ["1998", "1997"]


def test_matcher_filters_by_path():
    ts = tracks()
    pred = matcher("Artist / Album", ("Air", "Moon Safari"))
    assert [t.id for t in ts if pred(t)] == [3]
    pred = matcher("Genre", ("Unknown",))
    assert [t.id for t in ts if pred(t)] == [3, 4]
    assert matcher("Genre", ()) is None


# --- Qt: sync keeps selection; tree filters the table ------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_sync_is_incremental(app):
    from PySide6.QtCore import QItemSelectionModel
    from minuett.ui.views.library_model import LibraryFilterProxy, LibraryModel
    db = LibraryDB(":memory:")
    for t in tracks():
        t.id = None
        db.upsert(t)
    model = LibraryModel(db)
    proxy = LibraryFilterProxy()
    proxy.setSourceModel(model)
    view = QtWidgets.QTableView()
    view.setModel(proxy)
    resets = []
    model.modelReset.connect(lambda: resets.append(1))
    view.selectionModel().select(proxy.index(2, 0), QItemSelectionModel.SelectionFlag.Select
                                 | QItemSelectionModel.SelectionFlag.Rows)
    selected_path = proxy.index(2, 0).data(LibraryModel.TrackRole).path

    db.upsert(Track(path="/5", title="new"))
    db.remove_paths(["/1"])
    t2 = db.get_by_path("/2")
    db.update_fields(t2.id, {"title": "renamed"})
    model.sync()

    assert resets == []
    assert sorted(t.path for t in model.tracks) == ["/2", "/3", "/4", "/5"]
    assert db.get_by_path("/2").title == next(t.title for t in model.tracks if t.path == "/2")
    rows = view.selectionModel().selectedRows()
    if selected_path != "/1":
        assert [r.data(LibraryModel.TrackRole).path for r in rows] == [selected_path]


def test_browse_tree_filters_proxy(app):
    from minuett.ui.views.browse_tree import BrowseTree
    from minuett.ui.views.library_model import LibraryFilterProxy, LibraryModel
    db = LibraryDB(":memory:")
    for t in tracks():
        t.id = None
        db.upsert(t)
    model = LibraryModel(db)
    proxy = LibraryFilterProxy()
    proxy.setSourceModel(model)
    tree = BrowseTree()
    tree.combo.setCurrentText("Playlist")
    tree.filter_changed.connect(proxy.set_node_filter)
    tree.set_tracks(model.tracks)
    assert proxy.rowCount() == 4
    road = tree._find(("Road Trip",))
    tree.tree.setCurrentItem(road)
    assert proxy.rowCount() == 2
    # Rebuilding (e.g. after a download) keeps the selected node and filter.
    tree.set_tracks(model.tracks)
    assert tree.current_path() == ("Road Trip",) and proxy.rowCount() == 2
    tree.combo.setCurrentText("Genre")
    assert tree.current_path() == () and proxy.rowCount() == 4
