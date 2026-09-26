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

### As built: playlist-based library and per-playlist updates

The library is organised around imported playlists. Changes from the
original sketch above:

- **No `download_archive`.** The database records every remote entry
  (`playlist_entries`: position, video ID, status, linked track). An update
  re-reads the listing and downloads only entries that are `new` or `failed`.
  The archive is keyed only by video ID, so it would skip a video that
  appears in a second playlist; instead that video is linked, not
  re-downloaded.
- **Each playlist is updated on its own.** Every playlist row has **Check**
  (fetch the listing, show how many songs are new, download nothing) and
  **Update** (check, then download the new ones). Operations queue and run one
  at a time. There is no "update all".
- **Upstream changes:** reordering is mirrored; removed videos drop out of
  the playlist (status `removed`) but keep their files and library rows;
  deleted/private videos are `unavailable`; a playlist renamed on YouTube is
  renamed here, and its tracks' Playlist column follows.
- **Files:** `<download root>/<playlist>/<title> [<video id>].<ext>`. No index
  in the file name, because order lives in the database. The folder is fixed
  at import so renames don't split it.
- **Audio:** `FFmpegExtractAudio` with `preferredcodec: "best"` copies
  Opus/AAC without re-encoding; MP3 is a preference.
- **Tags:** YouTube Music's own track/artist/album fields when present,
  otherwise parsed from the video title. The upload date is not written as
  the year.
- **JavaScript runtime:** yt-dlp 2026.x needs one for YouTube, plus the
  `yt-dlp-ejs` package (from `yt-dlp[default]`). yt-dlp enables only Deno by
  default; Antiphon passes whichever of Deno/Node/QuickJS/Bun is installed.

### As built: other sources (Spotify, Pandora, files)

- **Spotify links** are read from the public embed page. Since February 2026
  Spotify's Web API only returns tracks for playlists the signed-in user
  owns, so it isn't an option for pasted links. The embed page lists at most
  100 tracks and is unofficial, so it may break or change.
- **Export files** (CSV/TXT) are parsed by column name, which covers Exportify,
  TuneMyMusic and Soundiiz, plus plain "Artist - Title" lists. Pandora has no
  public API or playlist pages, so this is the only route for it. Entries are
  keyed by track URI, else ISRC, else artist|title; importing a newer export of
  the same playlist updates it.
- **Matching** (`core/downloader/matching.py`): YouTube Music song search,
  shortlist by title, confirm with full details, score title/artist/length;
  fall back to regular YouTube; below 0.62 the song is "not found" rather
  than guessed. The matched video id is stored on the entry.
- **Schema v3**: `playlist_entries` keyed by `item_id` (source key) with the
  matched `youtube_id` separate; `tracks.source`/`source_url` and
  `playlists.source` record where music came from.

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

### As built: equalizer

- All ten bands are peaking filters. GStreamer makes the first and last
  bands shelves by default, so Antiphon overrides that.
- The curve uses GStreamer's own filter design (gain scaled as 10^(dB/40), bandwidth
  in Hz warped with tan(bw/2)) rather than the RBJ cookbook, so it shows what
  you hear. Knob Q is converted to bandwidth as freq / Q. The default Q of 1.5
  matches GStreamer's own 10-band spacing.
- Off is a bypass (gains and preamp to unity), not a pipeline relink.
- State lives in `~/.config/antiphon/equalizer.json`; user presets in
  `eq_presets.json`. Built-in presets can't be overwritten.

## Build order

1. Playback and library scanner. ✓
2. Metadata editing. ✓
3. Downloader. ✓ (playlist-based sync, see below)
4. Equalizer. ✓
5. Skin pass, once functionality is stable. ← **next**
