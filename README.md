# Antiphon

A Linux music player in the spirit of RealPlayer 10, with a YouTube playlist
downloader, a SQLite-backed library with tag editing, and a 10-band parametric
equalizer. Built with Python 3.12, PySide6 (Qt 6) and GStreamer.

See [docs/PLAN.md](docs/PLAN.md) for the full design and build order.

## Status

**All five build phases done: playback, library, metadata editing, playlist downloader, equalizer, and the skin.**

- [x] GStreamer playbin wrapper: queue, gapless transitions, repeat, seek,
      volume, broken files skipped. Bus polled from a 50 ms `QTimer`.
- [x] Library DB (SQLite), mtime-aware scanner, mutagen tag read/write for
      MP3, M4A, Opus, FLAC, Ogg.
- [x] Basic window: transport strip, nav rail, sortable/searchable library
      table, Now Playing queue pane, compact toolbar mode (Ctrl+T, Esc exits).
- [x] Arrange-by browse tree (Playlist, Artist/Album, Album Artist, Album,
      Genre, Year, Date Added); library refreshes in place, keeping selection.
- [ ] Folder watching (watchdog)
- [x] Phase 2: metadata editing. Double-click a tag cell (or F2) to edit it
      inline; select rows and press Ctrl+E for the batch editor with cover art
      (view, replace, remove). Files are written first and the database only
      updates on success. Enter, or double-clicking a read-only column, plays.
- [x] Phase 3: playlist-based downloader (yt-dlp). Import a YouTube playlist
      (Ctrl+I); every imported playlist is tracked and has its own **Check**
      (see what's new) and **Update** (download only new songs) buttons in
      Playlists; there is deliberately no "update all". Songs removed on
      YouTube leave the playlist but stay in your library; a video in two
      playlists is downloaded once and linked into both. Downloads view shows
      per-song progress with Cancel and Retry Failed. The playlist name is a
      sortable, searchable library column.
- [x] Phase 4: 10-band parametric equalizer in playbin's audio-filter slot.
      Gain slider (±12 dB), frequency knob and Q knob per band, preamp, on/off,
      live response curve, clipping hint, JSON presets (Flat, Rock, Vocal, Bass
      Boost, Treble Cut, plus your own). The drawn curve is checked against
      GStreamer's actual output in the tests (within 0.05 dB).
- [x] Phase 5: RealPlayer 10-style skin with custom-painted transport, glowing
      sliders, EQ faders and knobs, and an LCD-style display. Themes are CSS
      files: 11 built in (RealPlayer Classic plus ten VS Code favourites), and
      you can write your own. See [docs/THEMES.md](docs/THEMES.md).
- [x] Flatpak and AppImage packages (x86_64), each with a built-in
      `--self-test`.

## Installing

Download from `dist/` (or build it yourself, below).

**Flatpak** (needs the Flathub remote for the GNOME 50 runtime):

```bash
flatpak install --user Antiphon-0.1.0-x86_64.flatpak
```

The Flatpak can read and write `~/Music`. For music elsewhere, grant access:

```bash
flatpak override --user --filesystem=/path/to/music io.github.antiphon.Antiphon
```

**AppImage**: make it executable and run it. It bundles Python, GStreamer,
ffmpeg and Deno, and needs glibc 2.39 or newer (Ubuntu 24.04, Fedora 40,
Debian 13, or later).

```bash
chmod +x Antiphon-0.1.0-x86_64.AppImage
```

Either package can check itself: it decodes each audio format through the EQ
and confirms yt-dlp can find ffmpeg and a JavaScript runtime.

```bash
flatpak run io.github.antiphon.Antiphon --self-test
```

## Building the packages

```bash
./packaging/flatpak/build.sh
```

```bash
./packaging/appimage/build.sh
```

The Flatpak build needs `org.flatpak.Builder` and `org.gnome.Sdk//50` from
Flathub. Python wheels are pinned in `packaging/flatpak/python-deps.json`;
after changing dependencies, regenerate it with `python3 tools/gen_flatpak_deps.py`.
The AppImage is assembled from the build machine's Python 3.12, PyGObject and
GStreamer, so build it on the oldest distro you want to support.

## Running from source

System packages (Debian/Ubuntu names):

```bash
sudo apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav ffmpeg libxcb-cursor0
```

`libxcb-cursor0` is needed by Qt 6.5+ on X11 and isn't installed by default
on Ubuntu-based systems.

YouTube downloads also need a JavaScript runtime that yt-dlp supports: Deno,
Node.js, QuickJS or Bun. Antiphon uses whichever is installed (see
File ▸ Preferences).

Then create a virtualenv that can see the system PyGObject:

```bash
python3.12 -m venv --system-site-packages .venv
```

```bash
.venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/python -m antiphon
```

Use **File ▸ Add Music Folder…** to populate the library. F5 rescans.

## Tests

The core has no Qt imports and is tested headless. Tests generate short sine-wave
files with ffmpeg; player tests route audio to a `fakesink`.

```bash
.venv/bin/python -m pytest
```

## Layout

```
antiphon/
  core/          # Qt-free: player, library, downloader, equalizer
  ui/            # PySide6 window, views, dialogs
    skin/        # base.qss, painted widgets, themes/*.css
  selftest.py    # antiphon --self-test
packaging/       # desktop file, metainfo, icon, flatpak/, appimage/
tools/           # packaging helpers
tests/
docs/            # PLAN.md, THEMES.md
```

## Note on downloading

Downloading from YouTube is against its terms outside the cases it permits.
The downloader is intended for personal use of content you have the right to keep.
