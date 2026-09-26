"""GStreamer playbin wrapper with a play queue and gapless transitions.

No GLib main loop runs. The owner calls :meth:`Player.poll` periodically
(the UI uses a 50 ms QTimer); it drains the bus with ``pop_filtered`` and
emits signals on the caller's thread.

Gapless: playbin fires ``about-to-finish`` on a streaming thread shortly
before the current stream ends. We set the next URI right there (the only
place that works for gapless), and learn that the switch actually happened
from the STREAM_START message on the bus.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .signals import Signal  # noqa: E402

Gst.init(None)


class State(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class Repeat(Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True)
class QueueItem:
    path: str
    track_id: int | None = None

    @property
    def uri(self) -> str:
        return Path(self.path).resolve().as_uri()


_BUS_TYPES = (
    Gst.MessageType.EOS
    | Gst.MessageType.ERROR
    | Gst.MessageType.STATE_CHANGED
    | Gst.MessageType.STREAM_START
    | Gst.MessageType.DURATION_CHANGED
    | Gst.MessageType.TAG
)


class Player:
    def __init__(self) -> None:
        self.playbin = Gst.ElementFactory.make("playbin", "player")
        if self.playbin is None:
            raise RuntimeError("GStreamer playbin element is unavailable")
        # Audio only for now: disable video/subtitle decoding.
        flags = self.playbin.get_property("flags")
        self.playbin.set_property("flags", flags & ~0x1 & ~0x4)  # ~video, ~text
        self.bus = self.playbin.get_bus()
        self.playbin.connect("about-to-finish", self._on_about_to_finish)

        self._lock = threading.Lock()
        self._queue: list[QueueItem] = []
        self._index = -1          # item currently audible
        self._pending = -1        # item queued via about-to-finish, not yet started
        self._state = State.STOPPED
        self.repeat = Repeat.OFF

        # Signals (emitted from poll() on the owner's thread)
        self.state_changed = Signal()     # (State)
        self.track_changed = Signal()     # (index, QueueItem | None)
        self.duration_changed = Signal()  # (ns)
        self.bitrate_changed = Signal()   # (bits per second)
        self.queue_changed = Signal()     # ()
        self.error = Signal()             # (message)

    # --- queue ------------------------------------------------------------

    @property
    def queue(self) -> list[QueueItem]:
        with self._lock:
            return list(self._queue)

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> QueueItem | None:
        with self._lock:
            return self._queue[self._index] if 0 <= self._index < len(self._queue) else None

    def set_queue(self, items: list[QueueItem], start: int = 0, play: bool = True) -> None:
        with self._lock:
            self._queue = list(items)
            self._pending = -1
        self.queue_changed.emit()
        if items and play:
            self.play_index(start)
        elif not items:
            self.stop()

    def enqueue(self, items: list[QueueItem]) -> None:
        with self._lock:
            self._queue.extend(items)
        self.queue_changed.emit()

    def remove(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self._queue):
                return
            del self._queue[index]
            if index < self._index:
                self._index -= 1
            self._pending = -1
            removed_current = index == self._index
        self.queue_changed.emit()
        if removed_current:
            self.stop()

    def _next_index(self, after: int, manual: bool) -> int:
        """Index to play after ``after``, or -1. Caller holds the lock."""
        n = len(self._queue)
        if n == 0:
            return -1
        if self.repeat is Repeat.ONE and not manual:
            return after
        nxt = after + 1
        if nxt < n:
            return nxt
        return 0 if self.repeat is not Repeat.OFF else -1

    # --- transport --------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    def play_index(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self._queue):
                return
            item = self._queue[index]
            self._index = index
            self._pending = -1
        # NULL, not READY: after an error the pipeline can't reach READY
        # cleanly. Then drop the previous stream's leftover messages; a failing
        # file often posts several ERRORs that would be blamed on this track.
        self.playbin.set_state(Gst.State.NULL)
        self.bus.set_flushing(True)
        self.bus.set_flushing(False)
        self.playbin.set_property("uri", item.uri)
        self.playbin.set_state(Gst.State.PLAYING)
        self.track_changed.emit(index, item)

    def play(self) -> None:
        if self._state is State.PAUSED:
            self.playbin.set_state(Gst.State.PLAYING)
        elif self._state is State.STOPPED:
            self.play_index(max(self._index, 0))

    def pause(self) -> None:
        if self._state is State.PLAYING:
            self.playbin.set_state(Gst.State.PAUSED)

    def toggle(self) -> None:
        if self._state is State.PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self.playbin.set_state(Gst.State.NULL)
        with self._lock:
            self._pending = -1
        self._set_state(State.STOPPED)

    def next(self) -> None:
        with self._lock:
            nxt = self._next_index(self._index, manual=True)
        if nxt >= 0:
            self.play_index(nxt)
        else:
            self.stop()

    def previous(self) -> None:
        # Like most players: restart the track if we're more than 3 s in.
        pos = self.position_ns()
        if pos is not None and pos > 3 * Gst.SECOND:
            self.seek_ns(0)
            return
        with self._lock:
            prev = self._index - 1
            if prev < 0 and self.repeat is Repeat.ALL:
                prev = len(self._queue) - 1
        if prev >= 0:
            self.play_index(prev)
        else:
            self.seek_ns(0)

    def seek_ns(self, position: int) -> None:
        self.playbin.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, max(0, position))

    def position_ns(self) -> int | None:
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        return pos if ok else None

    def duration_ns(self) -> int | None:
        ok, dur = self.playbin.query_duration(Gst.Format.TIME)
        return dur if ok and dur > 0 else None

    @property
    def volume(self) -> float:
        return self.playbin.get_property("volume")

    @volume.setter
    def volume(self, value: float) -> None:
        self.playbin.set_property("volume", min(max(value, 0.0), 1.0))

    def set_audio_filter(self, element: Gst.Element | None) -> None:
        """Install the EQ bin. Takes effect from the next READY/NULL state."""
        self.playbin.set_property("audio-filter", element)

    def shutdown(self) -> None:
        self.playbin.set_state(Gst.State.NULL)

    # --- bus / streaming thread ------------------------------------------

    def _on_about_to_finish(self, playbin: Gst.Element) -> None:
        # Streaming thread. Only touch queue state under the lock.
        with self._lock:
            nxt = self._next_index(self._index, manual=False)
            if nxt < 0:
                return
            self._pending = nxt
            uri = self._queue[nxt].uri
        playbin.set_property("uri", uri)

    def _set_state(self, state: State) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state)

    def poll(self) -> None:
        """Drain pending bus messages. Call regularly from the owner's loop."""
        while True:
            msg = self.bus.pop_filtered(_BUS_TYPES)
            if msg is None:
                break
            self._handle(msg)

    def _handle(self, msg: Gst.Message) -> None:
        t = msg.type
        if t == Gst.MessageType.STREAM_START:
            with self._lock:
                switched = self._pending >= 0
                if switched:
                    self._index, self._pending = self._pending, -1
                    item = self._queue[self._index]
            if switched:
                self.track_changed.emit(self._index, item)
        elif t == Gst.MessageType.EOS:
            # End of the whole queue (gapless would have avoided EOS otherwise).
            self.stop()
            self.track_changed.emit(-1, None)
        elif t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            self.error.emit(f"{err.message}")
            # Skip the broken track rather than halting the queue.
            with self._lock:
                nxt = self._next_index(self._index, manual=True)
                if nxt == self._index:
                    nxt = -1
            if nxt >= 0:
                self.play_index(nxt)
            else:
                self.stop()
        elif t == Gst.MessageType.STATE_CHANGED and msg.src == self.playbin:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._set_state(State.PLAYING)
            elif new == Gst.State.PAUSED and self._state is State.PLAYING:
                self._set_state(State.PAUSED)
        elif t == Gst.MessageType.DURATION_CHANGED:
            dur = self.duration_ns()
            if dur:
                self.duration_changed.emit(dur)
        elif t == Gst.MessageType.TAG:
            taglist = msg.parse_tag()
            for tag in (Gst.TAG_BITRATE, Gst.TAG_NOMINAL_BITRATE):
                ok, value = taglist.get_uint(tag)
                if ok and value:
                    self.bitrate_changed.emit(value)
                    break
