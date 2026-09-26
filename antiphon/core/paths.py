"""XDG locations. Inside Flatpak these resolve to the sandbox's own dirs."""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "antiphon"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_ID


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_ID


def library_db_path() -> Path:
    return data_dir() / "library.db"
