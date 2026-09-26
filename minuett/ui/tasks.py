"""Run a blocking function on a worker thread and get the result back on the UI thread."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Task(QObject):
    done = Signal(object, object)  # result, error

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001 - handed to the callback
            self.done.emit(None, e)


_alive: set = set()   # keep threads referenced until they finish


def run_in_thread(parent: QObject, fn: Callable[[], Any],
                  callback: Callable[[Any, Exception | None], None]) -> None:
    """``callback(result, error)`` runs on the UI thread when ``fn`` returns."""
    thread = QThread(parent)
    task = _Task(fn)
    task.moveToThread(thread)
    thread.started.connect(task.run)
    task.done.connect(callback)
    task.done.connect(thread.quit)
    pair = (thread, task)
    _alive.add(pair)
    thread.finished.connect(lambda: _alive.discard(pair))
    thread.finished.connect(task.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
