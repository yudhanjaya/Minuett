import os
import re

import pytest

from antiphon.ui.skin.theme import (
    DEFAULT_THEME, REQUIRED_VARS, ThemeError, base_template, builtin_theme_dir, discover,
    expand, load_theme_file, parse_color, parse_theme, qss_value, render_qss, resolve,
)

BUILTINS = sorted(p.stem for p in builtin_theme_dir().glob("*.css"))


def test_there_are_eleven_distinct_builtins():
    assert len(BUILTINS) == 11 and DEFAULT_THEME in BUILTINS
    themes = [load_theme_file(builtin_theme_dir() / f"{b}.css", True) for b in BUILTINS]
    # Visually different: no two share the same window color.
    windows = {t.vars["window"].lower() for t in themes}
    assert len(windows) == len(themes)
    assert sum(not t.dark for t in themes) == 2  # two light themes


@pytest.mark.parametrize("theme_id", BUILTINS)
def test_builtin_is_complete_and_renders(theme_id):
    t = load_theme_file(builtin_theme_dir() / f"{theme_id}.css", builtin=True)
    missing = [v for v in REQUIRED_VARS if v not in t.vars]
    assert not missing, missing
    for name in REQUIRED_VARS:
        value = resolve(name, t.vars)
        if name not in ("font", "font-size", "lcd-font", "radius"):
            assert parse_color(value) is not None, (name, value)
    qss = render_qss(base_template(), t)
    assert "var(" not in qss
    assert "--" not in re.sub(r"/\*.*?\*/", "", qss, flags=re.S)


def test_parse_color_forms():
    assert parse_color("#abc") == (0xaa, 0xbb, 0xcc, 255)
    assert parse_color("#11223380") == (0x11, 0x22, 0x33, 0x80)   # CSS RGBA order
    assert parse_color("rgba(10, 20, 30, 0.5)") == (10, 20, 30, 128)
    assert parse_color("rgb(100%, 0%, 0%)") == (255, 0, 0, 255)
    assert parse_color("transparent") == (0, 0, 0, 0)
    assert parse_color("13px") is None
    # Qt reads 8-digit hex as ARGB, so translucent colors become rgba().
    assert qss_value("#11223380") == "rgba(17, 34, 51, 128)"
    assert qss_value("#112233") == "#112233"


def test_var_nesting_fallbacks_and_loops():
    v = {"a": "#010203", "b": "var(--a)", "c": "var(--missing, var(--b))", "x": "var(--y)", "y": "var(--x)"}
    assert resolve("b", v) == "#010203"
    assert resolve("c", v) == "#010203"
    assert expand("color: var(--nope, red);", v) == "color: red;"
    with pytest.raises(ThemeError):
        resolve("x", v)


def test_parse_theme_metadata_and_extras():
    t = parse_theme('/* note */\n:root { --name: "Mine"; --dark: false; --accent: #f00; }\n'
                    '#NavRail { color: var(--accent); }', "mine")
    assert (t.name, t.dark, t.vars, t.author_note) == ("Mine", False, {"accent": "#f00"}, "note")
    assert "#NavRail" in t.extra_qss
    with pytest.raises(ThemeError):
        parse_theme("QWidget { color: red; }", "bad")


def test_partial_user_theme_falls_back_and_overrides(tmp_path):
    (tmp_path / "tiny.css").write_text(':root { --name: "Tiny"; --accent: #ff0000; }')
    (tmp_path / "broken.css").write_text("not a theme")
    (tmp_path / "dracula.css").write_text(':root { --name: "My Dracula"; --window: #000000; }')
    themes = discover(tmp_path)
    assert "broken" not in themes
    assert themes["dracula"].name == "My Dracula" and not themes["dracula"].builtin
    qss = render_qss(base_template(), themes["tiny"], themes[DEFAULT_THEME])
    assert "#ff0000" in qss and "var(" not in qss


# --- Qt: applying, palette, live reload, customize ---------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_manager_apply_and_customize(app, tmp_path):
    from PySide6.QtGui import QPalette
    from antiphon.ui.skin.manager import ThemeManager
    tm = ThemeManager(tmp_path / "themes")
    tm.apply("dracula", app)
    assert tm.color("accent").name() == "#bd93f9"
    assert app.palette().color(QPalette.ColorRole.Window).name() == "#282a36"
    assert "#282a36" in app.styleSheet()

    path = tm.copy_for_editing("dracula")
    assert path.parent == tmp_path / "themes" and tm.themes[path.stem].name == "Dracula (custom)"
    tm.apply(path.stem, app)
    # Edit the file the way a user would; the watcher path reloads and reapplies.
    path.write_text(path.read_text().replace("--accent: #bd93f9", "--accent: #00ff00"))
    tm._live_reload()
    assert tm.current.id == path.stem and tm.color("accent").name() == "#00ff00"
    tm.apply("realplayer-classic", app)


def test_every_theme_paints_the_window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from antiphon.ui.main_window import MainWindow
    from antiphon.ui.skin.manager import ThemeManager, install
    tm = ThemeManager(tmp_path / "themes")
    install(tm)
    w = MainWindow()
    try:
        for theme_id in BUILTINS:
            w.set_theme(theme_id)
            for row in (1, 4):  # library, equalizer
                w.nav.setCurrentRow(row)
                img = w.grab().toImage()
                assert not img.isNull()
        assert w.settings.value("ui/theme") == BUILTINS[-1]
    finally:
        w.close()
