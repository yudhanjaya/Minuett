"""'Arrange by' dropdown plus the browse tree that filters the library table."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from minuett.core.library.browse import ARRANGEMENTS, DEFAULT_ARRANGEMENT, Node, build_tree, matcher
from minuett.core.library.db import Track
from minuett.ui.skin.components import label, section_label, space, tune_item_view
from minuett.ui.skin.manager import manager

PathRole = Qt.ItemDataRole.UserRole + 1


def _path(item: QTreeWidgetItem | None) -> tuple[str, ...]:
    # Qt may hand a stored tuple back as a list; normalise for comparisons.
    return tuple(item.data(0, PathRole) or ()) if item else ()


class BrowseTree(QWidget):
    filter_changed = Signal(object)  # predicate(Track) -> bool, or None

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
        self.tree.currentItemChanged.connect(lambda *_: self._emit())
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

    def set_tracks(self, tracks: list[Track]) -> None:
        """Rebuild, keeping the selected node (and expansion) where possible."""
        self._tracks = tracks
        keep = self.current_path()
        expanded = self._expanded_paths()
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, ["All Music", str(len(tracks))])
        root.setData(0, PathRole, ())
        root.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.setForeground(1, manager().color("text-muted"))
        self._add(root, build_tree(tracks, self.mode), expanded)
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
            if n.path in expanded:
                item.setExpanded(True)

    def _expanded_paths(self) -> set:
        out = set()
        stack = [self.tree.invisibleRootItem()]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                c = it.child(i)
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
        return matcher(self.mode, self.current_path())

    def _emit(self) -> None:
        self.filter_changed.emit(self.filter_for_current())
