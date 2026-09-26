"""Main window: transport strip on top, nav rail | active view | queue pane.

Functional scaffolding only. The RealPlayer skin (QSS + custom-painted
transport widgets) is a dedicated later pass, per docs/PLAN.md.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QCheckBox, QInputDialog, QMainWindow, QMenu, QMessageBox, QSlider, QSplitter, QStackedWidget, QTableView,
    QVBoxLayout, QWidget, QAbstractItemView, QHeaderView,
)

from minuett.core.library.db import LibraryDB, Track
from minuett.core.library.scanner import scan
from minuett.core.eq_filter import EqualizerFilter
from minuett.core.equalizer import PresetStore, load_state, save_state
from minuett.core.paths import eq_presets_path, eq_state_path, library_db_path
from minuett.core.player import Player, QueueItem, State
from . import ytdlp_update
from .dialogs.preferences import PreferencesDialog, load_preferences
from .dialogs.tag_editor import TagEditorDialog
from .download_manager import DownloadManager, OpResult
from .skin.manager import manager as theme_manager
from .skin.components import (
    ViewHeader, icon_button, px, section_label, space, themed_icon, tune_item_view, view,
)
from .skin.icons import icon, pixmap
from .skin.widgets import GlowSlider, HaloOverlay, StatusDisplay, TransportButton
from .views.accounts_panel import AccountsPanel
from .views.now_playing_view import NowPlayingView, QueueList
from .views.browse_tree import BrowseTree
from .views.downloads_view import DownloadsView
from .views.equalizer_view import EqualizerView, debounce
from .views.playlists_view import PlaylistsView
from .views.library_model import (
    COLUMNS, EDITABLE_ATTRS, LibraryFilterProxy, LibraryModel, format_ms,
)

NS_PER_MS = 1_000_000
NAV_ITEMS = ["Now Playing", "My Library", "Playlists", "Downloads", "Equalizer"]
NAV_ICONS = {"Now Playing": "now-playing", "My Library": "library", "Playlists": "playlists",
             "Downloads": "downloads", "Equalizer": "equalizer"}


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


class LibraryTable(QTableView):
    """Enter plays the current row (unless an inline editor is open)."""

    play_requested = Signal(object)  # proxy QModelIndex

    def keyPressEvent(self, event) -> None:
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self.state() != QAbstractItemView.State.EditingState
                and self.currentIndex().isValid()):
            self.play_requested.emit(self.currentIndex())
            return
        super().keyPressEvent(event)


class TransportBar(QWidget):
    """The top strip, after RealPlayer 10: round glossy transport buttons with
    an oversized Play, the display panel over a glowing position slider, and
    volume at the right. This strip alone is what toolbar mode shows."""

    def __init__(self, player: Player, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Transport")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.player = player
        self._seeking = False

        def btn(kind: str, tip: str, slot, diameter=32, primary=False) -> TransportButton:
            b = TransportButton(kind, diameter, primary)
            b.setToolTip(tip)
            b.setAccessibleName(tip.split(" (")[0])
            b.clicked.connect(slot)
            return b

        self.prev_btn = btn("prev", "Previous", player.previous)
        self.play_btn = btn("play", "Play/Pause (Space)", player.toggle, diameter=52, primary=True)
        self.stop_btn = btn("stop", "Stop", player.stop)
        self.next_btn = btn("next", "Next", player.next)

        self.display = StatusDisplay()
        self.display.set_text("Minuett", "Stopped")
        self.position = GlowSlider()
        self.position.setRange(0, 0)
        self.position.setToolTip("Position")
        self.position.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.position.sliderReleased.connect(self._seek)

        self.volume = GlowSlider(thumb=6)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(120)
        self.volume.setAccessibleName("Volume")
        self.position.setAccessibleName("Position")
        self.volume.setValue(int(player.volume * 100))
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(lambda v: setattr(player, "volume", v / 100))
        self.volume.reset.connect(lambda: self.volume.setValue(100))

        buttons = QHBoxLayout()
        buttons.setSpacing(space(1))
        for b in (self.prev_btn, self.play_btn, self.stop_btn, self.next_btn):
            buttons.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)

        center = QVBoxLayout()
        center.setSpacing(space(1))
        center.addWidget(self.display)
        center.addWidget(self.position)

        self.volume_icon = QLabel()
        self.volume_icon.setAccessibleName("Volume")
        vol = QHBoxLayout()
        vol.setSpacing(space(2))
        vol.addWidget(self.volume_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        vol.addWidget(self.volume, 0, Qt.AlignmentFlag.AlignVCenter)

        # Filled in by the window: queue and toolbar-mode toggles.
        self.extras = QHBoxLayout()
        self.extras.setSpacing(space(1))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(space(3), space(2), space(3), space(2))
        layout.setSpacing(space(4))
        layout.addLayout(buttons)
        layout.addLayout(center, 1)
        layout.addLayout(vol)
        layout.addLayout(self.extras)
        self.restyle()

        player.state_changed.connect(self._on_state)

    def restyle(self) -> None:
        """Re-render theme-colored pixmaps after a theme change."""
        self.volume_icon.setPixmap(pixmap("volume", 18, theme_manager().color("chrome-text")))

    def _seek(self) -> None:
        self._seeking = False
        self.player.seek_ns(self.position.value() * NS_PER_MS)

    def _on_state(self, state: State) -> None:
        self.play_btn.set_kind("pause" if state is State.PLAYING else "play")
        if state is not State.PLAYING:
            self.display.silence()
        if state is State.STOPPED:
            self.position.setRange(0, 0)
            self.display.set_time("", "")

    def tick(self) -> None:
        if self.player.state is State.STOPPED:
            return
        pos, dur = self.player.position_ns(), self.player.duration_ns()
        if dur:
            self.position.setMaximum(dur // NS_PER_MS)
        if pos is not None and not self._seeking:
            self.position.setValue(pos // NS_PER_MS)
        shown = self.position.sliderPosition() * NS_PER_MS if self._seeking else (pos or 0)
        self.display.set_time(
            f"{format_ms(shown // NS_PER_MS) or '0:00'} / {format_ms((dur or 0) // NS_PER_MS) or '0:00'}",
            self.player.state.value)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Minuett")
        self.resize(1200, 720)
        self.settings = QSettings("minuett", "minuett")

        self.db_path = str(library_db_path())
        self.db = LibraryDB(self.db_path)
        self.player = Player()
        # EQ goes into playbin's audio-filter slot before anything plays.
        self.eq_filter = EqualizerFilter()
        eq_state = load_state(eq_state_path())
        self.eq_filter.apply(eq_state)
        self.player.set_audio_filter(self.eq_filter.bin)
        self._track_cache: dict[int, Track] = {}

        # --- transport ---
        self.transport = TransportBar(self.player)

        # --- nav rail ---
        self.nav = QListWidget()
        self.nav.setObjectName("NavRail")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFixedWidth(196)
        self.nav.setAccessibleName("Sections")
        nav_h = px("nav-item-height", 38)
        for name in NAV_ITEMS:
            QListWidgetItem(name, self.nav).setSizeHint(QSize(0, nav_h))
        self.nav.setSpacing(1)
        self._restyle_nav()

        # --- center views ---
        self.stack = QStackedWidget()
        self.library_model = LibraryModel(self.db)
        self.proxy = LibraryFilterProxy()
        self.proxy.setSourceModel(self.library_model)
        self.library_view = self._build_library_view()

        self.downloads = DownloadManager(
            self.db_path, lambda: load_preferences(self.settings), self)
        self.downloads.plan_ready.connect(lambda *_: self._ensure_download_root_scanned())
        self.downloads.job_updated.connect(self._on_download_job)
        self.downloads.op_finished.connect(self._on_download_finished)
        self.playlists_view = PlaylistsView(self.db, self.downloads)
        self.playlists_view.play_tracks.connect(self.play_tracks)
        self.playlists_view.import_folder_requested.connect(self.import_folder)
        self.playlists_view.playlists_edited.connect(self._after_playlist_edit)
        self.playlists_view.show_downloads.connect(
            lambda: self.nav.setCurrentRow(NAV_ITEMS.index("Downloads")))
        self.downloads_view = DownloadsView(self.db, self.downloads)
        self.eq_view = EqualizerView(eq_state, PresetStore(eq_presets_path()))
        self._eq_save = debounce(self, 400, lambda: save_state(eq_state_path(), self.eq_view.state))
        self.eq_view.state_changed.connect(self._on_eq_changed)

        self.now_playing = NowPlayingView()
        self.now_playing.show_library.connect(
            lambda: self.nav.setCurrentRow(NAV_ITEMS.index("My Library")))

        self.views: dict[str, QWidget] = {}
        built = {"Now Playing": self.now_playing, "My Library": self.library_view,
                 "Playlists": self.playlists_view, "Downloads": self.downloads_view,
                 "Equalizer": self.eq_view}
        for name in NAV_ITEMS:
            w = built.get(name) or self._placeholder(name)
            self.views[name] = w
            self.stack.addWidget(w)
        self.nav.currentTextChanged.connect(lambda n: self.stack.setCurrentWidget(self.views[n]))
        self.nav.setCurrentRow(NAV_ITEMS.index("My Library"))

        # --- queue pane ---
        self.queue_list = QueueList()
        self.queue_list.setAccessibleName("Up next")
        self.queue_list.itemDoubleClicked.connect(
            lambda it: self.player.play_index(self.queue_list.row(it)))
        queue_pane = QWidget()
        queue_pane.setObjectName("QueuePane")
        queue_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        queue_pane.setMinimumWidth(220)
        ql = QVBoxLayout(queue_pane)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(0)
        qhead = QHBoxLayout()
        qhead.setContentsMargins(space(3), space(3), space(3), space(2))
        qhead.addWidget(section_label("Up next"))
        qhead.addStretch(1)
        self.queue_count = QLabel()
        self.queue_count.setObjectName("Muted")
        qhead.addWidget(self.queue_count)
        ql.addLayout(qhead)
        ql.addWidget(self.queue_list, 1)
        self.accounts_panel = AccountsPanel()
        ql.addWidget(self.accounts_panel)
        self.queue_pane = queue_pane

        splitter = QSplitter()
        splitter.addWidget(self.nav)
        splitter.addWidget(self.stack)
        splitter.addWidget(queue_pane)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(1, False)
        splitter.setSizes([196, 800, 270])

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.transport)
        cl.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.body = splitter
        # Glows (slider thumbs, EQ caps, the Play button) paint on this layer,
        # above neighbouring widgets instead of being clipped at their edges.
        self.halos = HaloOverlay(central)

        self._build_menus()
        self.nav.setFocus()  # start keyboard focus in the navigation, not on a button

        # --- player wiring ---
        self.player.track_changed.connect(self._on_track_changed)
        self.player.queue_changed.connect(self._refresh_queue)
        self.player.bitrate_changed.connect(self._on_bitrate)
        self.player.spectrum.connect(self.transport.display.feed_spectrum)
        self.player.error.connect(lambda m: self.statusBar().showMessage(f"Playback error: {m}", 8000))
        self._bitrate: int | None = None

        # GStreamer bus poll: no GLib main loop, just a 50 ms timer.
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._scan_thread: QThread | None = None
        # A quick rescan at startup (unchanged files are skipped) drops songs
        # whose files were deleted while Minuett was closed.
        if self.folders():
            QTimer.singleShot(1500, self.rescan)
        if self.library_model.rowCount() == 0 and not self.folders():
            self.statusBar().showMessage("Library is empty — use File ▸ Add Music Folder…")
            self.library_model.rowsInserted.connect(self._clear_empty_hint)

    # --- construction helpers -------------------------------------------

    def _placeholder(self, name: str) -> QWidget:
        label = QLabel(f"{name}\n\n(coming in a later build phase)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _build_library_view(self) -> QWidget:
        self.search = QLineEdit(placeholderText="Search title, artist, album…", clearButtonEnabled=True)
        self.search.setAccessibleName("Search library")
        self.search.setFixedWidth(300)
        self._search_icon = self.search.addAction(
            icon("search", 16, "text-muted"), QLineEdit.ActionPosition.LeadingPosition)
        themed_icon(self._search_icon, "search", 16, "text-muted")
        self.search.textChanged.connect(self.proxy.set_search)

        table = LibraryTable()
        table.setModel(self.proxy)
        table.setSortingEnabled(True)
        table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Double-click or Enter plays (or pauses the song that's playing);
        # F2 edits the cell, Ctrl+E opens the tag editor.
        table.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        tune_item_view(table)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for col, width in ((0, 48), (1, 300), (2, 190), (3, 190), (4, 130), (5, 110), (6, 64)):
            table.setColumnWidth(col, width)
        table.doubleClicked.connect(self._on_table_double_click)
        table.play_requested.connect(self._on_table_double_click)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._on_table_menu)
        edit_act = QAction("Edit Tags…", table, shortcut=QKeySequence("Ctrl+E"),
                           triggered=self.edit_selected_tags)
        edit_act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete_act = QAction("Delete…", table, shortcut=QKeySequence(QKeySequence.StandardKey.Delete),
                             triggered=lambda: self.delete_tracks([t.id for t in self.selected_tracks()]))
        delete_act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        table.addActions([edit_act, delete_act])
        self.edit_tags_action = edit_act
        self.table = table

        self.library_model.edit_failed.connect(
            lambda m: QMessageBox.warning(self, "Edit Tag", f"Couldn't write the file, nothing changed.\n\n{m}"))
        self.library_model.tracks_edited.connect(self._on_tracks_edited)

        self.browse = BrowseTree(self.settings)
        self.browse.filter_changed.connect(self.proxy.set_node_filter)
        self.browse.track_selected.connect(self._select_track_row)
        self.browse.track_activated.connect(self.activate_track)
        self.browse.track_menu.connect(self.show_track_menu)
        self.browse.set_tracks(self.library_model.tracks, self.db.memberships())
        self.proxy.set_node_filter(self.browse.filter_for_current())
        for sig in (self.library_model.rowsInserted, self.library_model.rowsRemoved,
                    self.library_model.modelReset, self.library_model.dataChanged):
            sig.connect(self._schedule_browse_rebuild)
        self._browse_timer = QTimer(self, singleShot=True, interval=150)
        self._browse_timer.timeout.connect(
            lambda: self.browse.set_tracks(self.library_model.tracks, self.db.memberships()))

        self.library_header = ViewHeader("My Library")
        self.library_header.add(self.search)
        for sig in (self.library_model.rowsInserted, self.library_model.rowsRemoved,
                    self.library_model.modelReset, self.proxy.layoutChanged,
                    self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset):
            sig.connect(self._update_library_subtitle)
        self._update_library_subtitle()

        split = QSplitter()
        split.addWidget(self.browse)
        split.addWidget(table)
        split.setStretchFactor(1, 1)
        split.setCollapsible(1, False)
        split.setSizes([290, 700])
        return view(self.library_header, split)

    def _update_library_subtitle(self, *_) -> None:
        total, shown = self.library_model.rowCount(), self.proxy.rowCount()
        noun = "song" if total == 1 else "songs"
        text = f"{total} {noun}" if shown == total else f"{shown} of {total} {noun}"
        self.library_header.set_subtitle(text)

    def _schedule_browse_rebuild(self, *_) -> None:
        self._browse_timer.start()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        add = QAction("&Add Music Folder…", self, triggered=self.add_folder)
        rescan = QAction("&Rescan Library", self, shortcut=QKeySequence("F5"),
                         triggered=self.rescan)
        quit_ = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit,
                        triggered=self.close)
        import_pl = QAction("&Import YouTube Playlist…", self, shortcut=QKeySequence("Ctrl+I"),
                            triggered=lambda: (self.nav.setCurrentRow(NAV_ITEMS.index("Playlists")),
                                               self.playlists_view.import_playlist()))
        prefs = QAction("&Preferences…", self, shortcut=QKeySequence("Ctrl+,"),
                        triggered=lambda: PreferencesDialog(self.settings, self).exec())
        import_dir = QAction("Import &Folder as Playlist…", self, shortcut=QKeySequence("Ctrl+Shift+I"),
                             triggered=self.import_folder)
        for a in (import_pl, import_dir, add, rescan):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(prefs)
        file_menu.addSeparator()
        file_menu.addAction(quit_)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.edit_tags_action)
        edit_menu.addSeparator()
        edit_menu.addAction(QAction("Move Genre Tags Out of Titles…", self,
                                    triggered=self.fix_genre_tags))

        view_menu = self.menuBar().addMenu("&View")
        self.toggle_queue = QAction("Show &Queue Pane", self, checkable=True, checked=True,
                                    toggled=self.queue_pane.setVisible)
        self.compact = QAction("&Toolbar Mode", self, checkable=True,
                               shortcut=QKeySequence("Ctrl+T"), toggled=self.set_compact)
        view_menu.addAction(self.toggle_queue)
        view_menu.addAction(self.compact)
        vis_on = self.settings.value("ui/visualizer", True, type=bool)
        self.visualizer_action = QAction("&Visualizer", self, checkable=True, checked=vis_on,
                                         toggled=self.set_visualizer)
        view_menu.addAction(self.visualizer_action)
        self.set_visualizer(vis_on)
        self.queue_button = icon_button("queue", "Show or hide the Up Next queue",
                                        checkable=True)
        self.queue_button.setChecked(True)
        self.queue_button.toggled.connect(self.toggle_queue.setChecked)
        self.toggle_queue.toggled.connect(self.queue_button.setChecked)
        self.compact_button = icon_button("compact", "Toolbar mode (Ctrl+T)",
                                          slot=lambda: self.compact.toggle())
        self.transport.extras.addWidget(self.queue_button)
        self.transport.extras.addWidget(self.compact_button)
        view_menu.addSeparator()
        self.theme_menu = view_menu.addMenu("&Theme")
        self._build_theme_menu()
        theme_manager().themes_reloaded.connect(self._build_theme_menu)

        play_menu = self.menuBar().addMenu("&Play")
        for text, key, slot in (
            ("Play/Pause", "Space", self.player.toggle),
            ("Stop", "Ctrl+.", self.player.stop),
            ("Next", "Ctrl+Right", self.player.next),
            ("Previous", "Ctrl+Left", self.player.previous),
        ):
            play_menu.addAction(QAction(text, self, shortcut=QKeySequence(key), triggered=slot))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction(
            "Check for &yt-dlp Update…", self,
            triggered=lambda: ytdlp_update.check_for_update(self, lambda: self.downloads.running)))

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
        self.library_model.sync()
        msg = (f"Scan complete: {result.added_or_updated} added/updated, "
               f"{result.unchanged} unchanged, {result.removed} removed")
        if result.errors:
            msg += f", {len(result.errors)} errors"
        self.statusBar().showMessage(msg, 10000)

    # --- playing, pausing and the track menu -------------------------------

    def _on_table_double_click(self, proxy_index) -> None:
        track = proxy_index.data(LibraryModel.TrackRole) if proxy_index.isValid() else None
        if track is not None:
            self.activate_track(track.id)

    def _is_current(self, track_id: int) -> bool:
        item = self.player.current
        return (item is not None and item.track_id == track_id
                and self.player.state in (State.PLAYING, State.PAUSED))

    def activate_track(self, track_id: int) -> None:
        """Play a song; if it's the one already playing, pause or resume it."""
        if self._is_current(track_id):
            self.player.toggle()
            return
        row = self._proxy_row_of(track_id)
        if row is not None:
            self._play_from_table(self.proxy.index(row, 0))
        else:
            t = self.db.get(track_id)
            if t:
                self.play_tracks([t])

    def _proxy_row_of(self, track_id: int) -> int | None:
        for r in range(self.proxy.rowCount()):
            t = self.proxy.index(r, 0).data(LibraryModel.TrackRole)
            if t is not None and t.id == track_id:
                return r
        return None

    def _select_track_row(self, track_id: int) -> None:
        row = self._proxy_row_of(track_id)
        if row is not None:
            self.table.selectRow(row)
            self.table.scrollTo(self.proxy.index(row, 0))

    def _on_table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        clicked = index.data(LibraryModel.TrackRole)
        ids = [t.id for t in self.selected_tracks()]
        if clicked.id not in ids:
            self.table.selectRow(index.row())
            ids = [clicked.id]
        self.show_track_menu(ids, self.table.viewport().mapToGlobal(pos))

    def show_track_menu(self, track_ids: list[int], global_pos) -> None:
        menu = QMenu(self)
        first = track_ids[0]
        if len(track_ids) == 1 and self._is_current(first):
            playing = self.player.state is State.PLAYING
            menu.addAction("Pause" if playing else "Resume", self.player.toggle)
        else:
            menu.addAction("Play", lambda: self._play_ids(track_ids))
        if self.player.state is State.PLAYING and not (len(track_ids) == 1 and self._is_current(first)):
            menu.addAction("Pause", self.player.pause)
        menu.addSeparator()
        menu.addAction(self.edit_tags_action)

        add = menu.addMenu("Add to Playlist")
        for pl in self.db.playlists():
            add.addAction(pl.name, lambda _=False, pid=pl.id: self.add_to_playlist(pid, track_ids))
        if self.db.playlists():
            add.addSeparator()
        add.addAction("New Playlist…", lambda: self.add_to_new_playlist(track_ids))

        scope = self.browse.playlist_scope()
        pl = self.db.playlist_by_name(scope) if scope else None
        if pl is not None and any(pl.id in self.db.playlists_containing(t) for t in track_ids):
            menu.addAction(f"Remove from “{pl.name}”",
                           lambda: self.remove_from_playlist(pl.id, track_ids))
        menu.addSeparator()
        n = len(track_ids)
        menu.addAction(f"Delete {n} Songs…" if n > 1 else "Delete…",
                       lambda: self.delete_tracks(track_ids))
        menu.exec(global_pos)

    def _play_ids(self, track_ids: list[int]) -> None:
        if len(track_ids) == 1:
            self.activate_track(track_ids[0])
            return
        tracks = [t for t in (self.db.get(i) for i in track_ids) if t]
        if tracks:
            self.play_tracks(tracks)

    def add_to_playlist(self, playlist_id: int, track_ids: list[int]) -> None:
        added = self.db.add_to_playlist(playlist_id, track_ids)
        pl = self.db.get_playlist(playlist_id)
        name = pl.name if pl else "playlist"
        note = ""
        if pl is not None and pl.is_imported and not pl.is_folder:
            note = " (only here: the playlist on YouTube/Spotify isn't changed)"
        skipped = len(track_ids) - added
        msg = f"Added {added} song{'s' if added != 1 else ''} to “{name}”{note}"
        if skipped:
            msg += f"; {skipped} already there"
        self.statusBar().showMessage(msg, 8000)
        self._after_playlist_edit()

    def add_to_new_playlist(self, track_ids: list[int]) -> None:
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if ok and name.strip():
            self.add_to_playlist(self.db.create_playlist(name.strip()), track_ids)

    def remove_from_playlist(self, playlist_id: int, track_ids: list[int]) -> None:
        self.db.remove_from_playlist(playlist_id, track_ids)
        pl = self.db.get_playlist(playlist_id)
        self.statusBar().showMessage(
            f"Removed from “{pl.name if pl else 'playlist'}”; updates won't bring "
            f"{'them' if len(track_ids) > 1 else 'it'} back", 8000)
        self._after_playlist_edit()

    def _after_playlist_edit(self) -> None:
        self.library_model.sync()
        self._schedule_browse_rebuild()
        self.playlists_view.refresh()

    def delete_tracks(self, track_ids: list[int]) -> None:
        tracks = [t for t in (self.db.get(i) for i in track_ids) if t]
        if not tracks:
            return
        n = len(tracks)
        what = f"“{tracks[0].title or tracks[0].path}”" if n == 1 else f"{n} songs"
        box = QMessageBox(QMessageBox.Icon.Question, "Delete",
                          f"Remove {what} from your library and playlists?", parent=self)
        box.setInformativeText("Playlist updates won't download them again. "
                               "Playlists on YouTube or Spotify aren't changed.")
        trash = QCheckBox("Also move the file" + ("s" if n > 1 else "") + " to the Trash")
        box.setCheckBox(trash)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel)
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is not delete:
            return
        item = self.player.current
        if item is not None and item.track_id in track_ids:
            self.player.stop()
        paths = self.db.delete_tracks([t.id for t in tracks])
        failed = {}
        if trash.isChecked():
            from minuett.core.library.trash import move_to_trash
            failed = move_to_trash(paths)
        self._after_playlist_edit()
        if failed:
            QMessageBox.warning(self, "Delete", "Removed from the library, but couldn't move "
                                f"{len(failed)} file(s) to the Trash:\n\n"
                                + "\n".join(f"• {p}: {e}" for p, e in list(failed.items())[:8]))
        else:
            self.statusBar().showMessage(
                f"Deleted {n} song{'s' if n != 1 else ''}"
                + (" and moved the files to the Trash" if trash.isChecked() else ""), 8000)

    def selected_tracks(self) -> list[Track]:
        rows = self.table.selectionModel().selectedRows()
        return [r.data(LibraryModel.TrackRole) for r in sorted(rows, key=lambda i: i.row())]

    def edit_selected_tags(self) -> None:
        tracks = self.selected_tracks()
        if not tracks:
            return
        dlg = TagEditorDialog(self.db, tracks, self)
        dlg.exec()
        if dlg.result_ and dlg.result_.updated:
            self.library_model.refresh_tracks(dlg.result_.updated)
            self.statusBar().showMessage(f"Updated {len(dlg.result_.updated)} track(s)", 5000)

    def _on_tracks_edited(self, ids: list[int]) -> None:
        # Keep the queue pane and status line in step with edits.
        for tid in ids:
            if tid in self._track_cache:
                fresh = self.db.get(tid)
                if fresh:
                    self._track_cache[tid] = fresh
        self._refresh_queue()
        item = self.player.current
        if item and item.track_id in ids:
            self._track_status(self._track_cache.get(item.track_id), item)

    def _play_from_table(self, proxy_index) -> None:
        # Queue everything currently visible, in view order, starting here.
        rows = range(self.proxy.rowCount())
        tracks: list[Track] = [
            self.proxy.index(r, 0).data(LibraryModel.TrackRole) for r in rows]
        self.play_tracks(tracks, proxy_index.row())

    def play_tracks(self, tracks: list[Track], start: int = 0) -> None:
        self._track_cache = {t.id: t for t in tracks}
        items = [QueueItem(t.path, t.id) for t in tracks]
        self.player.set_queue(items, start=start)

    def _on_eq_changed(self, state) -> None:
        self.eq_filter.apply(state)   # live: GStreamer band properties are runtime-safe
        self._eq_save.start()         # write the JSON once dragging settles

    def set_visualizer(self, on: bool) -> None:
        """Show or hide the spectrum analyzer (off also stops the analysis work)."""
        self.settings.setValue("ui/visualizer", on)
        self.eq_filter.set_spectrum_enabled(on)
        self.transport.display.set_visualizer_enabled(on)

    def fix_genre_tags(self) -> None:
        """Tidy tracks downloaded before genre tags were split from titles."""
        from minuett.core.library.cleanup import apply_genre_tag_fixes, find_genre_tag_fixes
        fixes = find_genre_tag_fixes(self.db)
        if not fixes:
            QMessageBox.information(self, "Genre Tags", "No downloaded titles end in a genre tag.")
            return
        examples = "\n".join(f"• {f.title}  →  genre “{f.genre}”" for f in fixes[:5])
        more = f"\n…and {len(fixes) - 5} more" if len(fixes) > 5 else ""
        answer = QMessageBox.question(
            self, "Genre Tags",
            f"Move the genre tag out of {len(fixes)} title{'s' if len(fixes) != 1 else ''}?\n\n"
            f"{examples}{more}\n\nOnly songs with no genre (or YouTube's “Music”) are changed.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = apply_genre_tag_fixes(self.db, fixes)
        self.library_model.sync()
        self._on_tracks_edited(result.updated)
        msg = f"Updated {len(result.updated)} song{'s' if len(result.updated) != 1 else ''}"
        if result.failed:
            msg += f"; {len(result.failed)} couldn't be written"
        self.statusBar().showMessage(msg, 8000)

    # --- downloads --------------------------------------------------------

    def _ensure_download_root_scanned(self) -> None:
        """Keep the download folder among library folders, so rescans cover it."""
        root = str(load_preferences(self.settings).music_root)
        folders = self.folders()
        if root not in folders:
            self.settings.setValue("library/folders", folders + [root])

    def _on_download_job(self, _i: int, job) -> None:
        if job.status.value == "done":
            self.library_model.sync()

    def import_folder(self, path: str | None = None) -> None:
        """Scan a folder into the library as a playlist named after the folder."""
        if not path:
            path = QFileDialog.getExistingDirectory(self, "Import Folder as Playlist",
                                                    str(Path.home() / "Music"))
        if not path:
            return
        folders = self.folders()
        if path not in folders:   # keep it in rescans (F5) too
            self.settings.setValue("library/folders", folders + [path])
        self.downloads.import_folder(path)
        self.statusBar().showMessage(f"Importing {Path(path).name}…")

    def _on_download_finished(self, r: OpResult) -> None:
        self.library_model.sync()
        self._schedule_browse_rebuild()
        if r.folder is not None:
            f = r.folder
            parts = [f"{f.total} song{'s' if f.total != 1 else ''}"]
            if f.added:
                parts.append(f"{f.added} new")
            if f.removed:
                parts.append(f"{f.removed} no longer in the folder")
            if f.errors:
                parts.append(f"{f.errors} unreadable")
            self.statusBar().showMessage(f"Playlist “{f.name}”: " + ", ".join(parts), 10000)
            return
        if r.error:
            self.statusBar().showMessage(f"Playlist error: {r.error}", 10000)
        elif r.plan is not None and r.kind == "check":
            n = len(r.plan.to_download)
            self.statusBar().showMessage(
                f"{n} new song{'s' if n != 1 else ''} available" if n else "Playlist is up to date",
                8000)

    # --- player feedback --------------------------------------------------

    def _tick(self) -> None:
        self.player.poll()
        self.transport.tick()

    def _queue_rows(self) -> list[tuple[str, str]]:
        rows = []
        for item in self.player.queue:
            t = self._track_cache.get(item.track_id) if item.track_id else None
            title = (t.title if t else None) or item.path.rsplit("/", 1)[-1]
            sub = " · ".join(x for x in ((t.artist if t else None), (t.album if t else None)) if x)
            rows.append((title, sub))
        return rows

    def _refresh_queue(self) -> None:
        rows = self._queue_rows()
        self.queue_list.set_rows(rows, self.player.index)
        n = len(rows)
        self.queue_count.setText(f"{n} track{'s' if n != 1 else ''}" if n else "")

    def _clear_empty_hint(self, *_) -> None:
        if self.statusBar().currentMessage().startswith("Library is empty"):
            self.statusBar().clearMessage()

    def _highlight_current(self) -> None:
        self.queue_list.set_current(self.player.index)

    def _restyle_nav(self) -> None:
        for i, name in enumerate(NAV_ITEMS):
            self.nav.item(i).setIcon(icon(NAV_ICONS[name], 18, "text-muted", active="selection-text"))

    def _on_track_changed(self, index: int, item: QueueItem | None) -> None:
        self._bitrate = None
        self._highlight_current()
        if item is None:
            self.transport.display.set_text("Minuett", "Stopped")
            self.now_playing.show_track(None)
            self.setWindowTitle("Minuett")
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
        headline = f"{t.artist} – {title}" if t and t.artist else title
        parts = []
        if t and t.album:
            parts.append(t.album)
        if t and t.source_playlist:
            parts.append(f"from {t.source_playlist}")
        bitrate = self._bitrate or (t.bitrate if t else None)
        codec = t.codec.upper() if t and t.codec else ""
        if bitrate or codec:
            parts.append(" ".join(x for x in (codec, f"{round(bitrate / 1000)} kbps" if bitrate else "") if x))
        self.transport.display.set_text(headline, "  ·  ".join(parts))
        self.setWindowTitle(f"{title} — Minuett")
        rows = self._queue_rows()
        nxt = self.player.index + 1
        up_next = rows[nxt][0] if 0 < nxt < len(rows) else None
        fmt = parts[-1] if (bitrate or codec) else ""
        self.now_playing.show_track(t, item.path, fmt, up_next)

    # --- themes -------------------------------------------------------------

    def _build_theme_menu(self) -> None:
        tm = theme_manager()
        menu = self.theme_menu
        menu.clear()
        group = QActionGroup(menu)
        current = tm.current.id if tm.current else None
        last_builtin = None
        for theme in tm.sorted_themes():
            if last_builtin is True and not theme.builtin:
                menu.addSeparator()
            last_builtin = theme.builtin
            act = QAction(theme.name, menu, checkable=True, checked=theme.id == current)
            act.setToolTip(theme.author_note)
            act.triggered.connect(lambda _=False, tid=theme.id: self.set_theme(tid))
            group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(QAction("Customize Current Theme…", menu, triggered=self._customize_theme))
        menu.addAction(QAction("Open Themes Folder", menu, triggered=self._open_theme_folder))
        menu.addAction(QAction("Reload Themes", menu, triggered=tm.reload))

    def set_theme(self, theme_id: str) -> None:
        theme = theme_manager().apply(theme_id)
        self.settings.setValue("ui/theme", theme.id)
        self._build_theme_menu()
        self._restyle_nav()
        self.transport.restyle()
        self.queue_list.viewport().update()

    def _open_theme_folder(self) -> None:
        folder = theme_manager().user_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _customize_theme(self) -> None:
        tm = theme_manager()
        path = tm.copy_for_editing(tm.current.id)
        self.set_theme(path.stem)
        QMessageBox.information(
            self, "Customize Theme",
            f"Saved an editable copy as:\n{path}\n\nEdit the CSS variables in any text "
            "editor; Minuett reloads the theme each time you save.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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
        if self.downloads.running:
            answer = QMessageBox.question(
                self, "Quit", "A playlist is still downloading. Stop it and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.downloads.shutdown()
        if self._eq_save.isActive():
            self._eq_save.stop()
            save_state(eq_state_path(), self.eq_view.state)
        self.timer.stop()
        self.player.shutdown()
        self.db.close()
        super().closeEvent(event)
