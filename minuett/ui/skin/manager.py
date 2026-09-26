"""Applies a CSS theme to the application and serves its colors to painted widgets."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from minuett.core.paths import config_dir

from .theme import (
    DEFAULT_THEME, Theme, ThemeError, base_template, discover, merged_vars, parse_color,
    render_qss, resolve,
)

log = logging.getLogger(__name__)


def user_theme_dir() -> Path:
    return config_dir() / "themes"


class ThemeManager(QObject):
    theme_changed = Signal(str)   # theme id
    themes_reloaded = Signal()

    def __init__(self, user_dir: Path | None = None) -> None:
        super().__init__()
        self.user_dir = user_dir if user_dir is not None else user_theme_dir()
        self.themes: dict[str, Theme] = {}
        self.current: Theme | None = None
        self._vars: dict[str, str] = {}
        self._colors: dict[str, QColor] = {}
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_file_changed)
        self._reload_timer = QTimer(self, singleShot=True, interval=250)
        self._reload_timer.timeout.connect(self._live_reload)
        self.reload()

    # --- discovery ---------------------------------------------------------

    def reload(self) -> None:
        self.themes = discover(self.user_dir)
        if DEFAULT_THEME not in self.themes:
            raise ThemeError("the built-in default theme is missing")
        self._watch()
        self.themes_reloaded.emit()

    def _watch(self) -> None:
        paths = self._watcher.files() + self._watcher.directories()
        if paths:
            self._watcher.removePaths(paths)
        if self.user_dir.is_dir():
            self._watcher.addPath(str(self.user_dir))
            files = [str(p) for p in self.user_dir.glob("*.css")]
            if files:
                self._watcher.addPaths(files)

    def _on_file_changed(self, _path: str) -> None:
        self._reload_timer.start()  # editors often write in several steps

    def _live_reload(self) -> None:
        current = self.current.id if self.current else DEFAULT_THEME
        self.reload()
        self.apply(current if current in self.themes else DEFAULT_THEME)

    def sorted_themes(self) -> list[Theme]:
        default = self.themes[DEFAULT_THEME]
        rest = sorted((t for t in self.themes.values() if t is not default),
                      key=lambda t: (not t.builtin, t.dark, t.name.casefold()))
        return [default, *rest]

    # --- applying ----------------------------------------------------------

    def apply(self, theme_id: str, app: QApplication | None = None) -> Theme:
        app = app or QApplication.instance()
        theme = self.themes.get(theme_id) or self.themes[DEFAULT_THEME]
        default = self.themes[DEFAULT_THEME]
        self.current = theme
        variables = merged_vars(theme, default)
        self._vars = {}
        for k in variables:
            try:
                self._vars[k] = resolve(k, variables)
            except ThemeError as e:
                log.warning("theme %s: %s", theme.id, e)
        self._colors = {}
        if app is not None:
            app.setStyle("Fusion")
            app.setPalette(self._palette())
            app.setFont(self._font())
            app.setStyleSheet(render_qss(base_template(), theme, default))
            for w in app.allWidgets():
                w.update()
        self.theme_changed.emit(theme.id)
        return theme

    def value(self, name: str, fallback: str = "") -> str:
        return self._vars.get(name, fallback)

    def color(self, name: str, fallback: str = "#ff00ff") -> QColor:
        c = self._colors.get(name)
        if c is None:
            rgba = parse_color(self.value(name, fallback)) or parse_color(fallback) or (255, 0, 255, 255)
            c = self._colors[name] = QColor(*rgba)
        return QColor(c)

    def number(self, name: str, fallback: float) -> float:
        raw = self.value(name, "").strip().removesuffix("px").removesuffix("pt")
        try:
            return float(raw)
        except ValueError:
            return fallback

    def font_families(self, name: str = "font") -> list[str]:
        raw = self.value(name, "")
        fams = [f.strip().strip("\"'") for f in raw.split(",") if f.strip()]
        generic = {"sans-serif": "Sans Serif", "serif": "Serif", "monospace": "Monospace"}
        return [generic.get(f, f) for f in fams]

    def _font(self) -> QFont:
        font = QFont(QApplication.font() if QApplication.instance() else QFont())
        fams = self.font_families("font")
        if fams:
            font.setFamilies(fams)
        size = self.number("font-size", 0)
        if size > 0:
            font.setPixelSize(round(size))
        return font

    def lcd_font(self, pixel_size: int, bold: bool = False) -> QFont:
        font = QFont()
        font.setFamilies(self.font_families("lcd-font") or ["Monospace"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        return font

    def _palette(self) -> QPalette:
        c = self.color
        p = QPalette()
        R = QPalette.ColorRole
        roles = {
            R.Window: "window", R.WindowText: "text", R.Base: "surface",
            R.AlternateBase: "surface-alt", R.Text: "text", R.Button: "button",
            R.ButtonText: "button-text", R.Highlight: "accent", R.HighlightedText: "accent-text",
            R.ToolTipBase: "tooltip", R.ToolTipText: "tooltip-text", R.PlaceholderText: "text-muted",
            R.Mid: "border", R.Dark: "chrome-bottom", R.Light: "button-hover",
            R.Midlight: "hover", R.Shadow: "chrome-edge", R.BrightText: "warning",
            R.Link: "accent",
        }
        for role, var in roles.items():
            p.setColor(role, c(var))
        muted = c("text-muted")
        for role in (R.WindowText, R.Text, R.ButtonText):
            p.setColor(QPalette.ColorGroup.Disabled, role, muted)
        return p

    # --- user themes -------------------------------------------------------

    def copy_for_editing(self, theme_id: str) -> Path:
        """Copy a theme into the user folder under a new name; return its path."""
        src = self.themes[theme_id]
        self.user_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{src.id}-custom" if src.builtin else f"{src.id}-copy"
        dest = self.user_dir / f"{stem}.css"
        n = 2
        while dest.exists():
            dest = self.user_dir / f"{stem}-{n}.css"
            n += 1
        assert src.path is not None
        text = src.path.read_text(encoding="utf-8")
        new_name = f"{src.name} (custom)" if src.builtin else f"{src.name} (copy)"
        dest.write_text(text.replace(f'--name: "{src.name}"', f'--name: "{new_name}"', 1),
                        encoding="utf-8")
        self.reload()
        return dest


_manager: ThemeManager | None = None


def manager() -> ThemeManager:
    """The app-wide theme manager (created on first use)."""
    global _manager
    if _manager is None:
        _manager = ThemeManager()
        _manager.apply(DEFAULT_THEME)
    return _manager


def install(m: ThemeManager) -> None:
    global _manager
    _manager = m


__all__ = ["ThemeManager", "manager", "install", "user_theme_dir"]
