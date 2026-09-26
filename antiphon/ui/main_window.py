"""Main window: transport strip on top, nav rail | active view | queue pane.

Functional scaffolding only. The RealPlayer skin (QSS + custom-painted
transport widgets) is a dedicated later pass, per docs/PLAN.md.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QSlider, QSplitter, QStackedWidget, QTableView,
    QToolButton, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView,
)

from antiphon.core.library.db import LibraryDB, Track
from antiphon.core.library.scanner import scan
from antiphon.core.paths import library_db_path
from antiphon.core.player import Player, QueueItem, State
from .views.library_model import LibraryFilterProxy, LibraryModel, format_ms

NS_PER_MS = 1_000_000
NAV_ITEMS = ["Now Playing", "My Library", "Playlists", "Downloads", "Equalizer"]


class ScanWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)  # ScanResult

    def __init__(self, db_path: str, roots: list[str]) -> None:
        super().__init__()
        self.db_path = db_path
        self.roots = roots

    def run(self) -> None:
        # sqlite connections are per-thread; open our own.
        db = LibraryDB(self.db_path)
        try:
            result = scan(db, self.roots,
                          progress=lambda i, p: i % 25 == 0 and self.progress.emit(str(p)))
        finally:
            db.close()
        self.finished.emit(result)


class TransportBar(QWidget):
    def __init__(self, player: Player, parent=None) -> None:
        super().__init__(parent)
        self.player = player
        self._seeking = False

        def btn(text: str, tip: str, slot) -> QToolButton:
            b = QToolButton(text=text, toolTip=tip)
            b.clicked.connect(slot)
            return b

        self.prev_btn = btn("⏮", "Previous", player.previous)
        self.play_btn = btn("▶", "Play/Pause", player.toggle)
        self.stop_btn = btn("⏹", "Stop", player.stop)
        self.next_btn = btn("⏭", "Next", player.next)
        self.play_btn.setMinimumSize(44, 44)

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.position.sliderReleased.connect(self._seek)
        self.time_label = QLabel("0:00 / 0:00")

        self.volume = QSlider(Qt.Orientation.Horizontal, maximumWidth=100)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(player.volume * 100))
        self.volume.valueChanged.connect(lambda v: setattr(player, "volume", v / 100))

        self.status = QLabel("Stopped")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        top = QHBoxLayout()
        for w in (self.prev_btn, self.play_btn, self.stop_btn, self.next_btn):
            top.addWidget(w)
        top.addWidget(self.position, 1)
        top.addWidget(self.time_label)
        top.addWidget(QLabel("Vol"))
        top.addWidget(self.volume)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.status)

        player.state_changed.connect(self._on_state)

    def _seek(self) -> None:
        self._seeking = False
        self.player.seek_ns(self.position.value() * NS_PER_MS)

    def _on_state(self, state: State) -> None:
        self.play_btn.setText("⏸" if state is State.PLAYING else "▶")
        if state is State.STOPPED:
            self.position.setRange(0, 0)
            self.time_label.setText("0:00 / 0:00")

    def tick(self) -> None:
        if self.player.state is State.STOPPED:
            return
        pos, dur = self.player.position_ns(), self.player.duration_ns()
        if dur:
            self.position.setMaximum(dur // NS_PER_MS)
        if pos is not None and not self._seeking:
            self.position.setValue(pos // NS_PER_MS)
        self.time_label.setText(
            f"{format_ms((pos or 0) // NS_PER_MS) or '0:00'} / "
            f"{format_ms((dur or 0) // NS_PER_MS) or '0:00'}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Antiphon")
        self.resize(1200, 720)
        self.settings = QSettings("antiphon", "antiphon")

        self.db_path = str(library_db_path())
        self.db = LibraryDB(self.db_path)
        self.player = Player()
        self._track_cache: dict[int, Track] = {}

        # --- transport ---
        self.transport = TransportBar(self.player)

        # --- nav rail ---
        self.nav = QListWidget()
        self.nav.addItems(NAV_ITEMS)
        self.nav.setMaximumWidth(170)

        # --- center views ---
        self.stack = QStackedWidget()
        self.library_model = LibraryModel(self.db)
        self.proxy = LibraryFilterProxy()
        self.proxy.setSourceModel(self.library_model)
        self.library_view = self._build_library_view()
        self.views: dict[str, QWidget] = {}
        for name in NAV_ITEMS:
            w = self.library_view if name == "My Library" else self._placeholder(name)
            self.views[name] = w
            self.stack.addWidget(w)
        self.nav.currentTextChanged.connect(lambda n: self.stack.setCurrentWidget(self.views[n]))
        self.nav.setCurrentRow(NAV_ITEMS.index("My Library"))

        # --- queue pane ---
        self.queue_list = QListWidget()
        self.queue_list.itemDoubleClicked.connect(
            lambda it: self.player.play_index(self.queue_list.row(it)))
        queue_pane = QWidget()
        ql = QVBoxLayout(queue_pane)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.addWidget(QLabel("Now Playing"))
        ql.addWidget(self.queue_list)
        self.queue_pane = queue_pane

        splitter = QSplitter()
        splitter.addWidget(self.nav)
        splitter.addWidget(self.stack)
        splitter.addWidget(queue_pane)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([160, 800, 240])

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.transport)
        cl.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.body = splitter

        self._build_menus()

        # --- player wiring ---
        self.player.track_changed.connect(self._on_track_changed)
        self.player.queue_changed.connect(self._refresh_queue)
        self.player.bitrate_changed.connect(self._on_bitrate)
        self.player.error.connect(lambda m: self.statusBar().showMessage(f"Playback error: {m}", 8000))
        self._bitrate: int | None = None

        # GStreamer bus poll: no GLib main loop, just a 50 ms timer.
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._scan_thread: QThread | None = None
        if self.library_model.rowCount() == 0 and not self.folders():
            self.statusBar().showMessage("Library is empty — use File ▸ Add Music Folder…")

    # --- construction helpers -------------------------------------------

    def _placeholder(self, name: str) -> QWidget:
        label = QLabel(f"{name}\n\n(coming in a later build phase)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _build_library_view(self) -> QWidget:
        self.search = QLineEdit(placeholderText="Search library…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.proxy.set_search)

        table = QTableView()
        table.setModel(self.proxy)
        table.setSortingEnabled(True)
        table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(22)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for col, width in ((0, 36), (1, 260), (2, 180), (3, 200), (4, 56)):
            table.setColumnWidth(col, width)
        table.doubleClicked.connect(self._play_from_table)
        self.table = table

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(table)
        return w

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        add = QAction("&Add Music Folder…", self, triggered=self.add_folder)
        rescan = QAction("&Rescan Library", self, shortcut=QKeySequence("F5"),
                         triggered=self.rescan)
        quit_ = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit,
                        triggered=self.close)
        for a in (add, rescan):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(quit_)

        view_menu = self.menuBar().addMenu("&View")
        self.toggle_queue = QAction("Show &Queue Pane", self, checkable=True, checked=True,
                                    toggled=self.queue_pane.setVisible)
        self.compact = QAction("&Toolbar Mode", self, checkable=True,
                               shortcut=QKeySequence("Ctrl+T"), toggled=self.set_compact)
        view_menu.addAction(self.toggle_queue)
        view_menu.addAction(self.compact)

        play_menu = self.menuBar().addMenu("&Play")
        for text, key, slot in (
            ("Play/Pause", "Space", self.player.toggle),
            ("Stop", "Ctrl+.", self.player.stop),
            ("Next", "Ctrl+Right", self.player.next),
            ("Previous", "Ctrl+Left", self.player.previous),
        ):
            play_menu.addAction(QAction(text, self, shortcut=QKeySequence(key), triggered=slot))

    # --- library ----------------------------------------------------------

    def folders(self) -> list[str]:
        return list(self.settings.value("library/folders", [], type=list))

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add Music Folder")
        if not path:
            return
        folders = self.folders()
        if path not in folders:
            folders.append(path)
            self.settings.setValue("library/folders", folders)
        self.rescan()

    def rescan(self) -> None:
        if self._scan_thread is not None:
            return
        folders = self.folders()
        if not folders:
            self.add_folder()
            return
        thread = QThread(self)
        worker = ScanWorker(self.db_path, folders)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda p: self.statusBar().showMessage(f"Scanning {p}"))
        worker.finished.connect(self._scan_done)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._scan_worker = worker
        self._scan_thread = thread
        self.statusBar().showMessage("Scanning…")
        thread.start()

    def _scan_done(self, result) -> None:
        self._scan_thread = None
        self.library_model.reload()
        msg = (f"Scan complete: {result.added_or_updated} added/updated, "
               f"{result.unchanged} unchanged, {result.removed} removed")
        if result.errors:
            msg += f", {len(result.errors)} errors"
        self.statusBar().showMessage(msg, 10000)

    def _play_from_table(self, proxy_index) -> None:
        # Queue everything currently visible, in view order, starting here.
        rows = range(self.proxy.rowCount())
        tracks: list[Track] = [
            self.proxy.index(r, 0).data(LibraryModel.TrackRole) for r in rows]
        self._track_cache = {t.id: t for t in tracks}
        items = [QueueItem(t.path, t.id) for t in tracks]
        self.player.set_queue(items, start=proxy_index.row())

    # --- player feedback --------------------------------------------------

    def _tick(self) -> None:
        self.player.poll()
        self.transport.tick()

    def _refresh_queue(self) -> None:
        self.queue_list.clear()
        for item in self.player.queue:
            t = self._track_cache.get(item.track_id) if item.track_id else None
            label = f"{t.artist} – {t.title}" if t and t.artist else (t.title if t else item.path)
            self.queue_list.addItem(QListWidgetItem(label))
        self._highlight_current()

    def _highlight_current(self) -> None:
        idx = self.player.index
        for i in range(self.queue_list.count()):
            f = self.queue_list.item(i).font()
            f.setBold(i == idx)
            self.queue_list.item(i).setFont(f)

    def _on_track_changed(self, index: int, item: QueueItem | None) -> None:
        self._bitrate = None
        self._highlight_current()
        if item is None:
            self.transport.status.setText("Stopped")
            self.setWindowTitle("Antiphon")
            return
        t = self._track_cache.get(item.track_id) if item.track_id else None
        if t and t.id is not None:
            self.db.record_play(t.id)
        self._track_status(t, item)

    def _on_bitrate(self, bps: int) -> None:
        if self._bitrate is None:
            self._bitrate = bps
            item = self.player.current
            if item:
                self._track_status(self._track_cache.get(item.track_id), item)

    def _track_status(self, t: Track | None, item: QueueItem) -> None:
        title = (t.title if t else None) or item.path.rsplit("/", 1)[-1]
        parts = [f"{t.artist} – {title}" if t and t.artist else title]
        if t and t.album:
            parts.append(t.album)
        bitrate = self._bitrate or (t.bitrate if t else None)
        codec = t.codec.upper() if t and t.codec else ""
        if bitrate or codec:
            parts.append(" ".join(x for x in (codec, f"{round(bitrate / 1000)} kbps" if bitrate else "") if x))
        self.transport.status.setText("   •   ".join(parts))
        self.setWindowTitle(f"{title} — Antiphon")

    # --- compact toolbar mode --------------------------------------------

    def set_compact(self, on: bool) -> None:
        if on:
            self._full_geometry = self.saveGeometry()
            self.body.hide()
            self.menuBar().hide()
            self.statusBar().hide()
            self.centralWidget().layout().activate()
            self.setFixedHeight(self.transport.sizeHint().height())
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            self.body.show()
            self.menuBar().show()
            self.statusBar().show()
            if getattr(self, "_full_geometry", None):
                self.restoreGeometry(self._full_geometry)

    def keyPressEvent(self, event) -> None:
        # Menubar is hidden in toolbar mode, so keep an escape hatch.
        if self.compact.isChecked() and event.key() == Qt.Key.Key_Escape:
            self.compact.setChecked(False)
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.player.shutdown()
        self.db.close()
        super().closeEvent(event)
