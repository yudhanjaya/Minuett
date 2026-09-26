# Minuett

<!-- Introduction and screenshots go here. -->

A music player for Linux in the spirit of RealPlayer 10 and Winamp: a glossy
transport strip, a library you browse by playlist, a parametric equalizer, and
a spectrum analyzer, with playlists pulled in from YouTube, YouTube Music,
Spotify and Pandora.

## Features

**Playback**
- Gapless playback of Opus, AAC/M4A, MP3, FLAC, Ogg Vorbis and WAV
  (GStreamer).
- Transport strip with an LCD-style display, a glowing position slider, and a
  Winamp-style spectrum analyzer that follows what you hear, after the EQ.
- Toolbar mode shrinks the window to just the transport strip (Ctrl+T; Esc
  returns).
- Up Next queue in the right sidebar; a Now Playing view with cover art.

**Library**
- SQLite library with an incremental folder scanner (unchanged files are
  skipped on rescans).
- Browse by Playlist, Artist/Album, Album Artist, Album, Genre, Year, Date
  Added or Source; live search across title, artist, album, genre and
  playlist.
- Tag editing: double-click a cell (or F2) to edit one field; select several
  songs and press Ctrl+E for the batch editor. Fields that differ show
  "(multiple values)" and are only written if you change them. Cover art can
  be viewed, replaced and removed. Files are written first; the library only
  updates when the write succeeds.
- Double-click (or Enter) plays a song, and pauses or resumes it if it's the
  one playing. Titles also appear as leaves in the browse tree.
- Right-click a song for Play/Pause, Edit Tags, Add to Playlist (including a
  new playlist), Remove from Playlist and Delete (from the library, optionally
  moving the file to the Trash).
- **Import a folder as a playlist** (File ▸ Import Folder as Playlist…, or
  Playlists ▸ Import Folder): every audio file under it, in natural order,
  as a playlist named after the folder. Rescan picks up added and removed files.
- Your changes survive updates: songs you add to an imported playlist stay
  (after the source's own songs), and songs you remove or delete aren't
  downloaded again (Restore brings one back). The playlist on YouTube or
  Spotify itself isn't changed.
- Genre tags in titles ("Song [lofi hip hop]") move into the genre field
  (Edit ▸ Move Genre Tags Out of Titles… tidies older downloads).

**Playlists from other services**
- **YouTube / YouTube Music:** paste a playlist link. The whole list is shown
  before anything downloads, then songs download one at a time with per-song
  progress, Cancel and Retry Failed.
- **Spotify:** paste a playlist or album link (the public page lists up to 100
  songs), or connect your account to import your own playlists in full.
- **Pandora, Apple Music and others:** import a CSV/TXT export from
  [Exportify](https://exportify.app), TuneMyMusic or Soundiiz.
- Spotify, Pandora and file imports are matched on YouTube Music by title,
  artist and length. Their own audio is DRM-protected and never downloaded.
  Weak matches are reported as "not found" rather than guessed.
- **Update each playlist on its own:** Check shows what's new, Update
  downloads only songs added since. There is deliberately no "update all".
  Songs removed upstream leave the playlist but stay in your library; a song
  in several playlists is downloaded once; live streams are skipped.
- Every song records its source (YouTube, YouTube Music, Spotify, Pandora…)
  and its link there.

**Audio quality**
- Opus by default. YouTube's own Opus streams are copied untouched; anything
  else is converted once at 256 kbps. "Original" (no conversion) and MP3 V0
  are options in Preferences.
- Optional YouTube Music sign-in: with a Premium account, downloads use
  Premium's higher-bitrate streams.

**Equalizer**
- 10-band parametric EQ: gain (±12 dB), frequency and Q per band, preamp,
  on/off, presets (Flat, Rock, Vocal, Bass Boost, Treble Cut, plus your own).
- Live response curve computed from GStreamer's own filter design, and tested
  against the element's measured output (within 0.05 dB).

**Themes**
- Eleven built in: RealPlayer Classic (default), One Dark Pro, Dracula,
  Monokai Pro, Night Owl, SynthWave '84, Cobalt2, Nord, Gruvbox Dark,
  GitHub Light and Catppuccin Latte.
- Themes are CSS files of variables that drive colours, spacing, sizes and the
  type scale. Customize one from View ▸ Theme; changes reload as you save. All
  built-in themes meet WCAG AA text contrast. See [docs/THEMES.md](docs/THEMES.md).

## Installing

Download the Flatpak bundle or the AppImage from the
[Releases](../../releases) page.

### Flatpak

Needs the Flathub remote (for the GNOME 50 runtime):

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

```bash
flatpak install --user Minuett-0.1.0-x86_64.flatpak
```

```bash
flatpak run io.github.yudhanjaya.Minuett
```

The Flatpak can read and write `~/Music` (downloads go to
`~/Music/Minuett/<playlist>/`). To use music stored elsewhere:

```bash
flatpak override --user --filesystem=/path/to/music io.github.yudhanjaya.Minuett
```

### AppImage

Bundles Python, GStreamer, ffmpeg and Deno. Needs glibc 2.39 or newer
(Ubuntu 24.04, Fedora 40, Debian 13, or later).

```bash
chmod +x Minuett-0.1.0-x86_64.AppImage
```

```bash
./Minuett-0.1.0-x86_64.AppImage
```

### Checking an install

Both packages can test themselves: every audio format is decoded through the
EQ, and yt-dlp's access to ffmpeg and a JavaScript runtime is confirmed.

```bash
flatpak run io.github.yudhanjaya.Minuett --self-test
```

## Using it

- **Add music:** File ▸ Import Folder as Playlist… (Ctrl+Shift+I), File ▸
  Add Music Folder…, or Playlists ▸ Import Playlist (Ctrl+I).
- **Play/pause:** double-click a song or press Enter; F2 edits the selected
  cell.
- **Keys:** Space play/pause · Ctrl+. stop · Ctrl+←/→ previous/next ·
  Ctrl+E edit tags · F2 edit cell · Delete delete · Ctrl+T toolbar mode ·
  F5 rescan.

### Optional sign-ins

Under **Accounts** at the bottom of the right sidebar. Everything works
without them.

- **YouTube Music:** reuses the sign-in of a browser you already use (Firefox,
  Chrome, Chromium, Brave, Edge, Vivaldi, Opera) or an exported
  `cookies.txt`. Minuett never sees your password. In the Flatpak, the dialog
  shows a one-line command that grants read-only access to just that
  browser's cookie folder. YouTube can restrict accounts it sees used by
  download tools; Minuett paces its requests, but the risk isn't zero.
- **Spotify:** Spotify only lets registered apps sign in, and since February
  2026 only lists the songs of playlists you own or collaborate on. Create a
  free app on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
  (its owner needs Premium), add the redirect URI shown in the dialog, and
  paste the Client ID. Sign-in uses PKCE; no client secret is stored.

Sign-in details are kept in `~/.config/minuett/accounts.json`, readable only
by you.

## Building from source

System packages (Debian/Ubuntu names):

```bash
sudo apt install python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav ffmpeg libxcb-cursor0
```

```bash
python3.12 -m venv --system-site-packages .venv
```

```bash
.venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/python -m minuett
```

Tests (headless; tones are generated with ffmpeg, audio goes to a fakesink):

```bash
.venv/bin/python -m pytest
```

Packages:

```bash
./packaging/flatpak/build.sh
```

```bash
./packaging/appimage/build.sh
```

The Flatpak build needs `org.flatpak.Builder` and `org.gnome.Sdk//50` from
Flathub. Python wheels are pinned in `packaging/flatpak/python-deps.json`;
regenerate with `python3 tools/gen_flatpak_deps.py` after changing
dependencies. Build the AppImage on the oldest distro you want to support.

## Project layout

```
minuett/
  core/          # Qt-free: player, library, downloader, equalizer, visualizer, accounts
  ui/            # PySide6 window, views, dialogs
    skin/        # base.qss, painted widgets, icons, themes/*.css
  selftest.py    # minuett --self-test
packaging/       # desktop file, metainfo, icon, flatpak/, appimage/
tools/           # packaging helpers
tests/
docs/            # PLAN.md (design notes), THEMES.md
```

## Credits

Minuett stands on these projects:

| Project | Used for | License |
|---|---|---|
| [Python](https://www.python.org) | language | PSF |
| [Qt 6](https://www.qt.io) / [PySide6](https://doc.qt.io/qtforpython-6/) | interface | LGPL-3.0 |
| [GStreamer](https://gstreamer.freedesktop.org) | playback, equalizer (`equalizer-nbands`), analyzer (`spectrum`) | LGPL-2.1+ |
| [PyGObject](https://pygobject.gnome.org) | GStreamer bindings | LGPL-2.1+ |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | YouTube and YouTube Music | Unlicense |
| [FFmpeg](https://ffmpeg.org) | audio extraction and conversion | LGPL-2.1+ / GPL |
| [Deno](https://deno.com) | JavaScript runtime yt-dlp needs for YouTube | MIT |
| [mutagen](https://github.com/quodlibet/mutagen) | reading and writing tags and cover art | GPL-2.0-or-later |
| [SQLite](https://sqlite.org) | library database | public domain |
| [watchdog](https://github.com/gorakhargosh/watchdog) | folder monitoring | Apache-2.0 |
| [SecretStorage](https://github.com/mitya57/secretstorage), [cryptography](https://cryptography.io) | reading browser sign-ins for YouTube Music | BSD-3-Clause / Apache-2.0 or BSD |
| [Flatpak](https://flatpak.org) with the GNOME runtime, [AppImage](https://appimage.org) | packaging | LGPL-2.1+ / MIT |

Theme palettes are Minuett's own interpretations of
[One Dark Pro](https://github.com/Binaryify/OneDark-Pro),
[Dracula](https://draculatheme.com), [Monokai Pro](https://monokai.pro),
[Night Owl](https://github.com/sdras/night-owl-vscode-theme),
[SynthWave '84](https://github.com/robb0wen/synthwave-vscode),
[Cobalt2](https://github.com/wesbos/cobalt2-vscode), [Nord](https://www.nordtheme.com),
[Gruvbox](https://github.com/morhetz/gruvbox),
[GitHub Theme](https://github.com/primer/github-vscode-theme) and
[Catppuccin](https://catppuccin.com), chosen from the most-installed list on
[vscodethemes.com](https://vscodethemes.com). The look is modelled on
RealPlayer 10 and Winamp. Minuett isn't affiliated with any of these, or with
YouTube, Spotify or Pandora.

## How it was made

Minuett was built with **Claude Opus 5.5** (Anthropic) in Claude Code, in a
single long session that ran from the design plan to packaging.

Token estimate for the session so far: the working context reached about
**870,000 tokens**. Counting every model call (each re-reads the
conversation, mostly from cache), the total processed is roughly
**100–150 million input tokens** and about **0.5 million output tokens**.
These are estimates from the session's usage readout, not a billing
statement.

## A note on downloading

Downloading from YouTube is only permitted in the cases its terms allow. Use
the downloader for content you have the right to keep.

## License

Copyright © 2026 Yudhanjaya Wijeratne

Minuett is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version. See [LICENSE](LICENSE).

GPL-2.0-or-later is the most permissive GPL licence available here: the
bundled tag library, mutagen, is GPL-2.0-or-later, and "or later" keeps
Minuett compatible with the Apache-2.0 components under GPL-3.0.
