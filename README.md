# Antiphon

A Linux music player in the spirit of RealPlayer 10, with a YouTube playlist
downloader, a SQLite-backed library with tag editing, and a 10-band parametric
equalizer. Built with Python 3.12, PySide6 (Qt 6) and GStreamer.

See [docs/PLAN.md](docs/PLAN.md) for the full design and build order.

## Status

**Phases 1–2 of 5 done: playback, library scanner, metadata editing.**

- [x] GStreamer playbin wrapper: queue, gapless transitions, repeat, seek,
      volume, broken files skipped. Bus polled from a 50 ms `QTimer`.
- [x] Library DB (SQLite), mtime-aware scanner, mutagen tag read/write for
      MP3, M4A, Opus, FLAC, Ogg.
- [x] Basic window: transport strip, nav rail, sortable/searchable library
      table, Now Playing queue pane, compact toolbar mode (Ctrl+T, Esc exits).
- [ ] Arrange-by browse tree, folder watching (watchdog)
- [x] Phase 2: metadata editing. Double-click a tag cell (or F2) to edit it
      inline; select rows and press Ctrl+E for the batch editor with cover art
      (view, replace, remove). Files are written first and the database only
      updates on success. Enter, or double-clicking a read-only column, plays.
- [ ] Phase 3: playlist downloader (yt-dlp)
- [ ] Phase 4: parametric equalizer
- [ ] Phase 5: RealPlayer skin
- [ ] Flatpak packaging

## Running from source

System packages (Debian/Ubuntu names):

```bash
sudo apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav ffmpeg
```

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
  core/          # Qt-free: player, library, (downloader, equalizer)
  ui/            # PySide6 window, views, dialogs, skin
tests/
docs/PLAN.md
```

## Note on downloading

Downloading from YouTube is against its terms outside the cases it permits.
The downloader is intended for personal use of content you have the right to keep.
