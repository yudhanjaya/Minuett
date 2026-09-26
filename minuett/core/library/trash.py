"""Move files to the desktop Trash (recoverable), never delete them outright.

GLib's trash handles the freedesktop Trash spec and, inside a Flatpak, goes
through the Trash portal, so files land in the same Trash your file manager
shows.
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


def move_to_trash(paths: list[str]) -> dict[str, str]:
    """Trash each file; returns {path: error} for the ones that failed."""
    failed: dict[str, str] = {}
    for p in paths:
        try:
            Gio.File.new_for_path(p).trash(None)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                continue  # already gone: nothing to do
            failed[p] = e.message
    return failed
