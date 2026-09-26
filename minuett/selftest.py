"""``minuett --self-test``: check a packaged build has everything it needs.

Runs without a display. Verifies the pieces the Flatpak/AppImage bundle
rather than trusting the build: GStreamer elements, real decoding of each
format through the EQ, ffmpeg and a JavaScript runtime as yt-dlp sees them,
tag reading, and Qt widgets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ELEMENTS = [
    "playbin", "equalizer-nbands", "volume", "audioconvert", "autoaudiosink",
    "pulsesink", "opusdec", "vorbisdec", "flacdec", "mpg123audiodec", "avdec_aac",
    "qtdemux", "matroskademux", "oggdemux", "id3demux", "spectrum",
]
FORMATS = {  # extension -> ffmpeg encoder args
    "opus": ["-c:a", "libopus"],
    "m4a": ["-c:a", "aac"],
    "mp3": ["-c:a", "libmp3lame"],
    "flac": ["-c:a", "flac"],
}


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def warn(self, label: str, ok: bool, detail: str = "") -> None:
        """Environment-dependent checks (e.g. no audio server on a build box)."""
        print(f"  [{'ok' if ok else 'warn'}] {label}{': ' + detail if detail else ''}", flush=True)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            self.failed += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{': ' + detail if detail else ''}", flush=True)
        return ok


def _decode(path: Path) -> tuple[bool, str]:
    """Decode a file end to end through the EQ bin into a fakesink."""
    from gi.repository import Gst

    from minuett.core.eq_filter import EqualizerFilter
    from minuett.core.equalizer import BUILTIN_PRESETS

    playbin = Gst.ElementFactory.make("playbin")
    eq = EqualizerFilter()
    eq.apply(BUILTIN_PRESETS["Rock"])
    playbin.set_property("audio-filter", eq.bin)
    playbin.set_property("audio-sink", Gst.ElementFactory.make("fakesink"))
    playbin.set_property("uri", path.as_uri())
    playbin.set_state(Gst.State.PLAYING)
    msg = playbin.get_bus().timed_pop_filtered(
        10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    playbin.set_state(Gst.State.NULL)
    if msg is None:
        return False, "timed out"
    if msg.type == Gst.MessageType.ERROR:
        return False, msg.parse_error()[0].message
    return True, ""


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    r = Report()
    print(f"Minuett self-test (Python {sys.version.split()[0]}, {sys.executable})")

    print("GStreamer")
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    Gst.init(None)
    import minuett
    r.check("minuett package", True, str(Path(minuett.__file__).parent))
    r.check("version", True, Gst.version_string())
    eq_plugin = Gst.Registry.get().find_plugin("equalizer")
    r.check("loaded from", True, f"gi {Path(gi.__file__).parent}, "
            f"equalizer plugin {eq_plugin.get_filename() if eq_plugin else '?'}")
    for name in ELEMENTS:
        r.check(f"element {name}", Gst.ElementFactory.find(name) is not None)

    # Reaching the sound server proves the sandbox/audio setup; volume 0 so
    # nothing is heard.
    try:
        pipe = Gst.parse_launch("audiotestsrc volume=0 num-buffers=5 ! audioconvert ! pulsesink")
        pipe.set_state(Gst.State.PLAYING)
        msg = pipe.get_bus().timed_pop_filtered(
            5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        pipe.set_state(Gst.State.NULL)
        audio_ok = msg is not None and msg.type == Gst.MessageType.EOS
        detail = "" if audio_ok else (msg.parse_error()[0].message if msg else "timed out")
    except GLib.Error as e:
        audio_ok, detail = False, e.message
    r.warn("sound server reachable (silent test tone)", audio_ok, detail)

    print("Tools")
    ffmpeg = shutil.which("ffmpeg")
    r.check("ffmpeg", bool(ffmpeg), ffmpeg or "not on PATH")
    from minuett.core.downloader.playlist import js_runtimes
    runtimes = js_runtimes()
    r.check("JavaScript runtime for yt-dlp", bool(runtimes), ", ".join(runtimes) or "none")

    import yt_dlp
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        pp = FFmpegPostProcessor(ydl)
        r.check(f"yt-dlp {yt_dlp.version.__version__} finds ffmpeg", pp.available,
                str(getattr(pp, "_path", "")))
    try:
        import yt_dlp_ejs  # noqa: F401
        r.check("yt-dlp-ejs (YouTube challenge scripts)", True)
    except ImportError as e:
        r.check("yt-dlp-ejs (YouTube challenge scripts)", False, str(e))

    print("Decoding through the equalizer")
    from minuett.core.library.tags import read_tags
    with tempfile.TemporaryDirectory() as tmp:
        for ext, args in FORMATS.items():
            f = Path(tmp) / f"tone.{ext}"
            if not ffmpeg:
                r.check(ext, False, "no ffmpeg to make a test file")
                continue
            made = subprocess.run(
                [ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.5",
                 *args, "-metadata", "title=Self Test", str(f)], capture_output=True, text=True)
            if made.returncode != 0:
                r.check(ext, False, f"ffmpeg couldn't encode: {made.stderr.strip()[-200:]}")
                continue
            ok, err = _decode(f)
            tags = read_tags(f)
            r.check(f"{ext} decode + tags", ok and tags.tags.get("title") == "Self Test",
                    err or f"{tags.codec}, {tags.duration_ms} ms")

    print("Qt")
    from PySide6 import __version__ as pyside_version
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from minuett.ui.skin.manager import ThemeManager
    tm = ThemeManager(Path(tempfile.mkdtemp()))
    tm.apply("realplayer-classic", app)
    import PySide6
    r.check(f"PySide6 {pyside_version}, platform {app.platformName()}", True,
            str(Path(PySide6.__file__).parent))
    r.check("themes", len(tm.themes) >= 11, f"{len(tm.themes)} found")

    print("PASS" if not r.failed else f"FAILED: {r.failed} check(s)")
    return 1 if r.failed else 0
