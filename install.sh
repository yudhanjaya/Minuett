#!/usr/bin/env bash
# Install Minuett from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/yudhanjaya/Minuett/main/install.sh | bash
#
# Uses the Flatpak bundle when flatpak is available, otherwise the AppImage.
# To choose one, or to start Minuett after installing, pass the flags to bash:
#
#   curl -fsSL https://raw.githubusercontent.com/yudhanjaya/Minuett/main/install.sh | bash -s -- --appimage --run
#
# Linux on x86_64 only.
set -euo pipefail

REPO=yudhanjaya/Minuett
APP=io.github.yudhanjaya.Minuett
FLATHUB=https://dl.flathub.org/repo/flathub.flatpakrepo
MODE=auto
RUN=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: install.sh [--flatpak | --appimage] [--run]

Installs Minuett from the latest GitHub release, preferring Flatpak when
flatpak is installed and falling back to the AppImage. When run through curl,
pass the flags after "bash -s --".
EOF
}

for arg in "$@"; do
  case "$arg" in
    --flatpak)  MODE=flatpak ;;
    --appimage) MODE=appimage ;;
    --run)      RUN=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          die "install.sh: unknown option: $arg (see --help)" ;;
  esac
done

[ "$(uname -s)" = Linux ] || die "Minuett runs on Linux only."
[ "$(uname -m)" = x86_64 ] || die "Minuett is built for x86_64 only."
command -v curl >/dev/null || die "curl is required to download the release."

# The release asset URLs carry the file name, so matching on the URL is enough.
asset() {
  printf '%s\n' "$RELEASE" |
    grep -oE '"browser_download_url": "[^"]*'"$1"'"' |
    head -n1 |
    sed -E 's/"browser_download_url": "([^"]*)"/\1/'
}

RELEASE=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest")
FLATPAK_URL=$(asset '\.flatpak' || true)
APPIMAGE_URL=$(asset '\.AppImage' || true)

if [ "$MODE" = auto ]; then
  if command -v flatpak >/dev/null && [ -n "$FLATPAK_URL" ]; then
    MODE=flatpak
  else
    MODE=appimage
  fi
fi

if [ "$MODE" = flatpak ]; then
  command -v flatpak >/dev/null || die "flatpak is not installed. Run with --appimage instead."
  [ -n "$FLATPAK_URL" ] || die "the latest release has no Flatpak bundle."

  # The bundle carries the app but not the GNOME runtime, which comes from Flathub.
  if ! flatpak remote-add --if-not-exists --user flathub "$FLATHUB" 2>/dev/null; then
    say "warning: could not add the flathub remote; the GNOME runtime must already be installed."
  fi

  BUNDLE=$(basename "$FLATPAK_URL")
  say "==> downloading $BUNDLE"
  curl -fL --retry 3 -o "$TMP/$BUNDLE" "$FLATPAK_URL"
  say "==> installing (the GNOME runtime is fetched from Flathub on first run)"
  flatpak install --user --non-interactive "$TMP/$BUNDLE"

  say ""
  say "Done. Music goes in ~/Music; for music elsewhere:"
  say "  flatpak override --user --filesystem=/path/to/music $APP"
  say ""
  say "Run it with: flatpak run $APP"
  if [ "$RUN" = 1 ]; then flatpak run "$APP"; fi
  exit 0
fi

command -v fusermount3 >/dev/null || command -v fusermount >/dev/null || \
  say "note: FUSE is not available, so run the AppImage with --appimage-extract-and-run."

GLIBC=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
if [ -n "$GLIBC" ]; then
  MAJ=${GLIBC%%.*}; MIN=${GLIBC##*.}
  if [ "$MAJ" -lt 2 ] || { [ "$MAJ" -eq 2 ] && [ "$MIN" -lt 39 ]; }; then
    die "the AppImage needs glibc 2.39 or newer (you have $GLIBC). Flatpak is the better route here."
  fi
fi

[ -n "$APPIMAGE_URL" ] || die "the latest release has no AppImage."

DIR=${APPIMAGE_DIR:-$HOME/Applications}
mkdir -p "$DIR"
say "==> downloading $(basename "$APPIMAGE_URL")"
curl -fL --retry 3 -o "$DIR/Minuett.AppImage" "$APPIMAGE_URL"
chmod +x "$DIR/Minuett.AppImage"

# A name on PATH, so it can be started as "minuett".
if mkdir -p "$HOME/.local/bin" 2>/dev/null; then
  ln -sf "$DIR/Minuett.AppImage" "$HOME/.local/bin/minuett"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "note: add ~/.local/bin to PATH to run it as 'minuett'." ;; esac
fi

say ""
say "Done. Music goes in ~/Music."
say ""
say "Run it with: $DIR/Minuett.AppImage"
if [ "$RUN" = 1 ]; then "$DIR/Minuett.AppImage"; fi
