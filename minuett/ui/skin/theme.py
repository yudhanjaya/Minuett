"""CSS themes.

A theme is a ``.css`` file:

    /* Any comment. */
    :root {
      --name: "Dracula";
      --dark: true;
      --window: #282a36;
      --accent: #bd93f9;
      ...
    }
    /* Optional extra Qt style sheet rules; they may use var(--...) too. */
    #NavRail::item:selected { color: var(--accent-2); }

Qt style sheets have no custom properties, so we implement them: the
``:root`` variables are substituted into ``base.qss`` (the shared skin) and
the theme's own extra rules. Custom-painted widgets (transport buttons,
sliders, knobs, display, EQ curve) read the same variables through
:func:`ThemeManager.color`, so one file restyles everything.

Missing variables fall back to the default theme, so a user theme only
needs to set what it changes. Colors may be written as ``#rgb``,
``#rrggbb``, ``#rrggbbaa`` (CSS order), ``rgb()``, ``rgba()`` with a 0–1
alpha, or a named color.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_THEME = "realplayer-classic"

# Every variable the skin uses. Built-in themes must define all of them.
REQUIRED_VARS = (
    # general UI
    "window", "surface", "surface-alt", "panel", "border", "text", "text-muted",
    "accent", "accent-text", "selection", "selection-text", "hover", "input",
    "button", "button-hover", "button-text", "scrollbar", "scrollbar-hover",
    "tooltip", "tooltip-text", "warning",
    # transport strip
    "chrome-top", "chrome-bottom", "chrome-edge", "chrome-text",
    "btn-top", "btn-bottom", "btn-ring", "btn-icon",
    "play-top", "play-bottom", "play-icon", "gloss",
    # sliders
    "groove", "groove-fill", "thumb", "glow",
    # display panel
    "lcd", "lcd-text", "lcd-dim", "lcd-border",
    # equalizer
    "eq-curve", "eq-fill", "eq-grid", "fader-cap", "knob-top", "knob-bottom",
    # type and shape
    "font", "font-size", "lcd-font", "radius",
)

_ROOT = re.compile(r":root\s*\{(?P<body>.*?)\}", re.S)
_DECL = re.compile(r"--(?P<name>[\w-]+)\s*:\s*(?P<value>[^;]+?)\s*(?:;|$)", re.M)
_VAR = re.compile(r"var\(\s*--(?P<name>[\w-]+)\s*(?:,\s*(?P<fallback>[^()]*(?:\([^()]*\))?[^()]*))?\)")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB = re.compile(r"^rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*(?:,\s*([\d.]+%?)\s*)?\)$")


class ThemeError(Exception):
    pass


@dataclass
class Theme:
    id: str                     # file stem
    name: str
    dark: bool
    vars: dict[str, str]
    extra_qss: str = ""
    path: Path | None = None
    builtin: bool = False
    author_note: str = field(default="", repr=False)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_theme(text: str, theme_id: str, path: Path | None = None, builtin: bool = False) -> Theme:
    m = _ROOT.search(_COMMENT.sub(lambda c: " " * len(c.group()), text))
    if not m:
        raise ThemeError(f"{theme_id}: no :root {{ … }} block with variables")
    variables = {d["name"]: d["value"].strip() for d in _DECL.finditer(m["body"])}
    extra = (text[:m.start()] + text[m.end():]).strip()
    name = _unquote(variables.pop("name", "")) or theme_id.replace("-", " ").title()
    dark = variables.pop("dark", "true").strip().lower() not in ("false", "0", "no")
    first_comment = re.match(r"\s*/\*(.*?)\*/", text, re.S)
    return Theme(theme_id, name, dark, variables, extra, path, builtin,
                 first_comment.group(1).strip(" *\n") if first_comment else "")


def load_theme_file(path: Path, builtin: bool = False) -> Theme:
    return parse_theme(Path(path).read_text(encoding="utf-8"), Path(path).stem, Path(path), builtin)


# --- values ------------------------------------------------------------------

def resolve(name: str, variables: dict[str, str], _depth: int = 0) -> str:
    """Value of --name with nested var() references expanded."""
    if _depth > 16:
        raise ThemeError(f"--{name}: var() references loop")
    if name not in variables:
        raise KeyError(name)
    return expand(variables[name], variables, _depth + 1)


def expand(text: str, variables: dict[str, str], _depth: int = 0) -> str:
    def sub(m: re.Match) -> str:
        try:
            return resolve(m["name"], variables, _depth)
        except KeyError:
            if m["fallback"] is not None:
                return expand(m["fallback"].strip(), variables, _depth + 1)
            log.warning("theme: undefined variable --%s", m["name"])
            return "transparent"
    return _VAR.sub(sub, text)


def parse_color(value: str) -> tuple[int, int, int, int] | None:
    """CSS color -> (r, g, b, a) with a in 0–255, or None if not a color."""
    v = value.strip()
    if _HEX.match(v):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
        return r, g, b, a
    m = _RGB.match(v)
    if m:
        def chan(s: str) -> int:
            return round(float(s[:-1]) * 2.55) if s.endswith("%") else round(float(s))
        r, g, b = (max(0, min(255, chan(m.group(i)))) for i in (1, 2, 3))
        a_raw = m.group(4)
        if a_raw is None:
            a = 255
        elif a_raw.endswith("%"):
            a = round(float(a_raw[:-1]) * 2.55)
        else:
            a = round(float(a_raw) * 255) if float(a_raw) <= 1 else round(float(a_raw))
        return r, g, b, max(0, min(255, a))
    if v.lower() == "transparent":
        return 0, 0, 0, 0
    return None


def qss_value(value: str) -> str:
    """Normalise colors for Qt style sheets (which read 8-digit hex as ARGB)."""
    rgba = parse_color(value)
    if rgba is None:
        return value
    r, g, b, a = rgba
    return f"#{r:02x}{g:02x}{b:02x}" if a == 255 else f"rgba({r}, {g}, {b}, {a})"


def merged_vars(theme: Theme, default: Theme | None) -> dict[str, str]:
    base = dict(default.vars) if default and default is not theme else {}
    base.update(theme.vars)
    return base


def render_qss(template: str, theme: Theme, default: Theme | None = None) -> str:
    variables = merged_vars(theme, default)
    resolved = {}
    for k in variables:
        try:
            resolved[k] = qss_value(resolve(k, variables))
        except ThemeError as e:
            log.warning("theme %s: %s", theme.id, e)
            resolved[k] = "transparent"
    return expand(template + "\n\n/* --- theme extras --- */\n" + theme.extra_qss, resolved)


# --- discovery ---------------------------------------------------------------

def builtin_theme_dir() -> Path:
    return Path(str(resources.files("minuett.ui.skin") / "themes"))


def base_template() -> str:
    return (resources.files("minuett.ui.skin") / "base.qss").read_text(encoding="utf-8")


def discover(user_dir: Path | None) -> dict[str, Theme]:
    """Built-in themes, then user themes (a user file with the same name wins)."""
    themes: dict[str, Theme] = {}
    sources = [(builtin_theme_dir(), True)]
    if user_dir is not None and user_dir.is_dir():
        sources.append((user_dir, False))
    for folder, builtin in sources:
        for path in sorted(folder.glob("*.css")):
            try:
                themes[path.stem] = load_theme_file(path, builtin)
            except (OSError, UnicodeDecodeError, ThemeError) as e:
                log.warning("skipping theme %s: %s", path, e)
    return themes
