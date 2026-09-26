"""'Arrange by' dropdown plus the browse tree that filters the library table."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from minuett.core.library.browse import ARRANGEMENTS, DEFAULT_ARRANGEMENT, Node, build_tree, matcher
from minuett.core.library.db import Track
from minuett.ui.views.library_model import format_ms
from minuett.ui.skin.components import label, section_label, space, tune_item_view
from minuett.ui.skin.manager import manager

PathRole = Qt.ItemDataRole.UserRole + 1
TrackIdRole = Qt.ItemDataRole.UserRole + 2   # set on track leaves


def _path(item: QTreeWidgetItem | None) -> tuple[str, ...]:
    # Qt may hand a stored tuple back as a list; normalise for comparisons.
    return tuple(item.data(0, PathRole) or ()) if item else ()


def _is_leaf(item: QTreeWidgetItem) -> bool:
    return item.data(0, TrackIdRole) is not None


class BrowseTree(QWidget):
    filter_changed = Signal(object)  # predicate(Track) -> bool, or None
    track_selected = Signal(int)     # a track leaf became current
    track_activated = Signal(int)    # a track leaf was double-clicked / Enter
    track_menu = Signal(list, object)  # [track id], global QPoint

    def __init__(self, settings: QSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("BrowsePane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.settings = settings
        self.combo = QComboBox()
        self.combo.addItems(list(ARRANGEMENTS))
        saved = settings.value("library/arrange", DEFAULT_ARRANGEMENT, type=str) if settings else None
        self.combo.setCurrentText(saved if saved in ARRANGEMENTS else DEFAULT_ARRANGEMENT)
        self.combo.currentTextChanged.connect(self._on_mode)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._on_current)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_menu)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._last_path: tuple[str, ...] | None = None
        self._memberships = None
        self.tree.setIndentation(space(4))
        tune_item_view(self.tree)
        self.combo.setAccessibleName("Arrange by")

        top = QVBoxLayout()
        top.setContentsMargins(space(3), space(3), space(3), space(2))
        top.setSpacing(space(2))
        top.addWidget(section_label("Browse"))
        row = QHBoxLayout()
        row.setSpacing(space(2))
        arrange = label("Arrange by", "Muted", "sm")
        arrange.setBuddy(self.combo)
        row.addWidget(arrange)
        row.addWidget(self.combo, 1)
        top.addLayout(row)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        self._tracks: list[Track] = []

    @property
    def mode(self) -> str:
        return self.combo.currentText()

    def current_path(self) -> tuple[str, ...]:
        return _path(self.tree.currentItem())

    def set_tracks(self, tracks: list[Track], memberships=None) -> None:
        """Rebuild, keeping the selected node (and expansion) where possible."""
        self._tracks = tracks
        if memberships is not None:
            self._memberships = memberships
        keep = self.current_path()
        expanded = self._expanded_paths()
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, ["All Music", str(len(tracks))])
        root.setData(0, PathRole, ())
        root.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.setForeground(1, manager().color("text-muted"))
        self._add(root, build_tree(tracks, self.mode, self._memberships), expanded)
        root.setExpanded(True)
        target = self._find(keep) or root
        self.tree.setCurrentItem(target)
        self.tree.blockSignals(False)
        if self.current_path() != keep:
            self._emit()   # the old node vanished; widen the filter

    def _add(self, parent: QTreeWidgetItem, nodes: list[Node], expanded: set) -> None:
        for n in nodes:
            item = QTreeWidgetItem(parent, [n.label, str(n.count)])
            item.setData(0, PathRole, n.path)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setForeground(1, manager().color("text-muted"))
            self._add(item, n.children, expanded)
            # The deepest groups list their songs, so titles are visible here too.
            for t in n.tracks:
                leaf = QTreeWidgetItem(item, [t.title or (t.path or "").rsplit("/", 1)[-1],
                                              format_ms(t.duration_ms)])
                leaf.setData(0, PathRole, n.path)      # selecting a song keeps its group's filter
                leaf.setData(0, TrackIdRole, t.id)
                leaf.setToolTip(0, " — ".join(x for x in (t.artist, t.title) if x))
                leaf.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                leaf.setForeground(1, manager().color("text-muted"))
            if n.path in expanded:
                item.setExpanded(True)

    def _expanded_paths(self) -> set:
        out = set()
        stack = [self.tree.invisibleRootItem()]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                c = it.child(i)
                if _is_leaf(c):
                    continue
                if c.isExpanded():
                    out.add(_path(c))
                stack.append(c)
        return out

    def _find(self, path: tuple[str, ...]) -> QTreeWidgetItem | None:
        stack = [self.tree.invisibleRootItem()]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                c = it.child(i)
                if _is_leaf(c):
                    continue
                if _path(c) == path:
                    return c
                stack.append(c)
        return None

    def _on_mode(self, mode: str) -> None:
        if self.settings:
            self.settings.setValue("library/arrange", mode)
        self.tree.setCurrentItem(None)
        self.set_tracks(self._tracks)
        self._emit()

    def filter_for_current(self):
        return matcher(self.mode, self.current_path(), self._memberships)

    def _emit(self) -> None:
        self._last_path = self.current_path()
        self.filter_changed.emit(self.filter_for_current())

    def _on_current(self, item, _prev=None) -> None:
        if self.current_path() != self._last_path:
            self._emit()
        if item is not None and _is_leaf(item):
            self.track_selected.emit(int(item.data(0, TrackIdRole)))

    def _on_activated(self, item, _col=0) -> None:
        if _is_leaf(item):
            self.track_activated.emit(int(item.data(0, TrackIdRole)))

    def _on_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or not _is_leaf(item):
            return
        ids = [int(i.data(0, TrackIdRole)) for i in self.tree.selectedItems() if _is_leaf(i)]
        if int(item.data(0, TrackIdRole)) not in ids:
            ids = [int(item.data(0, TrackIdRole))]
        self.track_menu.emit(ids, self.tree.viewport().mapToGlobal(pos))

    def playlist_scope(self) -> str | None:
        """The playlist being browsed (Arrange by Playlist ▸ <name>), if any."""
        path = self.current_path()
        return path[0] if self.mode == "Playlist" and path else None
