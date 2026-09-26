"""Minimal Qt-free signal so the core can notify the UI without importing Qt.

The UI connects these to Qt signals (or slots directly). Emission is
synchronous on the caller's thread; the player only emits from ``poll()``,
which the UI calls on the Qt main thread.
"""

from __future__ import annotations

from typing import Any, Callable


class Signal:
    def __init__(self) -> None:
        self._slots: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: Callable[..., Any]) -> None:
        self._slots.remove(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            slot(*args)
