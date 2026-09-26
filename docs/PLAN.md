# Antiphon — design plan

A Linux media player with a RealPlayer 10–style interface, a YouTube playlist
downloader, a SQLite-backed library with tag editing, and a 10-band parametric
equalizer.

## Stack

- **Python 3.12**, **PySide6 (Qt 6)** for the interface, **GStreamer** (via
  PyGObject) for playback. Qt Multimedia is skipped because it does not expose
  audio filters; GStreamer's `equalizer-nbands` exposes frequency, bandwidth
  and gain per band.
- **yt-dlp** as a Python library for downloading.
- **mutagen** for tags (MP3, M4A, Opus, FLAC, Ogg), stdlib **sqlite3** for the
  library, **watchdog** for inotify folder monitoring, **ffmpeg** (system) for
  yt-dlp post-processing.
- Shipped as a **Flatpak**, bundling GStreamer plugins and ffmpeg.

## Architecture

The core has no Qt imports so it can be tested headless. The UI talks to it
through callbacks bridged to Qt signals.

```
antiphon/
  core/
    signals.py      # tiny Qt-free signal/slot helper
    player.py       # GStreamer playbin wrapper, queue, gapless
    equalizer.py    # band model, presets, response-curve math
    library/
      db.py         # schema, queries
      scanner.py    # walk folders, read tags, upsert
      tags.py       # mutagen read/write, format quirks
    downloader/
      playlist.py   # flat-extract playlist, build job list
      worker.py     # sequential yt-dlp runs, progress hooks
      naming.py     # parse "Artist - Title" from video titles
  ui/
    main_window.py
    skin/           # QSS, custom-painted transport, bitmaps
    views/          # library, now playing, downloads, EQ
    dialogs/        # tag editor, preferences
```

GStreamer and Qt event loops: no GLib main loop thread. The UI polls the
pipeline bus from a 50 ms `QTimer` via `bus.pop_filtered()`.

## a) Playlist downloader

Two phases, so the user sees the whole playlist before anything downloads.

1. **Flat extract.** yt-dlp with `extract_flat: "in_playlist"` returns every
   entry's ID, title and position without touching media. This populates the
   Downloads view: position, title, status (queued, downloading, converting,
   tagging, done, skipped, failed) and a per-row progress bar.
2. **Sequential download** in a `QThread` worker, one video ID at a time via the
   yt-dlp Python API. `progress_hooks` feeds the row's progress bar; raising
   inside the hook implements Cancel. Failures are marked and the queue moves
   on; **Retry Failed** reruns only those rows.

```python
{
  "format": "bestaudio/best",
  "download_archive": "~/.local/share/antiphon/archive.txt",
  "postprocessors": [
    {"key": "FFmpegExtractAudio", "preferredcodec": "opus"},
    {"key": "FFmpegMetadata"},
    {"key": "EmbedThumbnail"},
  ],
  "writethumbnail": True,
  "sleep_interval": 2, "max_sleep_interval": 6,
  "outtmpl": "%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s",
}
```

Keep native audio (Opus/M4A); MP3 is an opt-in preference. The download
archive makes re-entering a playlist URL a free "sync playlist".

After each track, `naming.py` splits "Artist - Song (Official Video)" into
artist/title, strips noise suffixes, writes tags with mutagen and hands the file
to the scanner. Each downloaded playlist becomes a saved library playlist in
its original order.

Warnings: YouTube breaks yt-dlp regularly — include "Check for yt-dlp update".
Recent yt-dlp wants a JavaScript runtime (e.g. Deno) for full YouTube support;
verify current requirements when building this phase. Downloading from YouTube
is against its terms outside permitted cases — personal use of content you have
the right to keep.

## b) RealPlayer-style interface

Modelled on RealPlayer 10: dark charcoal and blue chrome, glossy transport bar
on top with an oversized round Play button, a position slider with a glowing
thumb, and a status readout (track info, bitrate). Below, three panes: left
navigation rail (Now Playing, My Library, Playlists, Downloads, Equalizer),
center active view, collapsible right Now Playing queue. A compact toolbar mode
shrinks the window to just the transport strip.

Chrome is QSS plus custom `paintEvent` widgets for transport buttons and
sliders.

## Library

`QTableView` over a SQLite-backed model with `QSortFilterProxyModel` for
sorting and live search. An "Arrange by" dropdown switches a browse tree:
Artist → Album, Album Artist, Genre, Year, Source Playlist, Date Added.
Selecting a node filters the table.

```sql
CREATE TABLE tracks (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT, artist TEXT, album TEXT, album_artist TEXT,
  genre TEXT, year INTEGER, track_no INTEGER, disc_no INTEGER,
  duration_ms INTEGER, codec TEXT, bitrate INTEGER,
  youtube_id TEXT, source_playlist TEXT,
  date_added TEXT, play_count INTEGER DEFAULT 0,
  last_played TEXT, rating INTEGER,
  mtime REAL
);
```

`mtime` lets rescans skip unchanged files.

## Metadata editing

- Double-click a cell to edit that field inline.
- Multi-select + key opens a batch editor: differing fields show
  "(multiple values)"; only changed fields are written.
- Every edit writes the file first via mutagen; the DB updates only on success.
- Cover art: view, replace from file, remove.

## Parametric equalizer

In playbin's `audio-filter` slot:

```
audioconvert ! volume name=preamp ! equalizer-nbands num-bands=10 ! audioconvert
```

Ten vertical gain sliders (±12 dB), each with a frequency knob and Q knob. A
live combined response curve (standard peaking-filter formulas) drawn above.
Defaults to ISO centers 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k Hz. Preamp,
on/off toggle, JSON presets (Flat, Rock, Vocal, Bass Boost, Treble Cut).

## Build order

1. Playback and library scanner. ← **current**
2. Metadata editing.
3. Downloader.
4. Equalizer.
5. Skin pass, once functionality is stable.
