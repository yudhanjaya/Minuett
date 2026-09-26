#!/usr/bin/env bash
# Build dist/Antiphon-<version>-x86_64.AppImage from this machine's Python 3.12,
# PyGObject and GStreamer, plus pip wheels, ffmpeg and Deno.
#
# Build on the oldest distro you want to support: the AppImage needs a host
# glibc at least as new as the build machine's. Debian/Ubuntu packages used:
#   python3.12 python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
#   gstreamer1.0-plugins-{base,good} gstreamer1.0-libav gstreamer1.0-pulseaudio
#   gstreamer1.0-alsa ffmpeg curl unzip
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
VERSION=$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
DENO_VERSION=2.9.7
DENO_SHA256=c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490
APP=io.github.yudhanjaya.Antiphon
PY=python3.12
ARCH=x86_64
TRIPLET=x86_64-linux-gnu

BUILD="$ROOT/build/appimage"
APPDIR="$BUILD/AppDir"
USR="$APPDIR/usr"
TOOLS="$BUILD/tools"
rm -rf "$APPDIR"
mkdir -p "$USR/bin" "$USR/lib" "$USR/libexec" "$TOOLS" "$ROOT/dist"

fetch() {  # url dest [sha256]
  [ -f "$2" ] || curl -fL --retry 3 -o "$2" "$1"
  if [ -n "${3:-}" ]; then echo "$3  $2" | sha256sum -c --quiet; fi
}

echo "==> tools"
fetch https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage \
      "$TOOLS/appimagetool"
chmod +x "$TOOLS/appimagetool"
fetch https://raw.githubusercontent.com/AppImageCommunity/pkg2appimage/master/excludelist \
      "$TOOLS/excludelist"

echo "==> python $($PY --version)"
cp -L "$(command -v $PY)" "$USR/bin/$PY"
PYLIB=$($PY -c 'import sysconfig;print(sysconfig.get_path("stdlib"))')
mkdir -p "$USR/lib/$PY"
# The standard library, minus what a music player never imports.
tar -C "$PYLIB" -cf - \
    --exclude=test --exclude=tests --exclude=idlelib --exclude=tkinter \
    --exclude=turtledemo --exclude=ensurepip --exclude=lib2to3 --exclude='__pycache__' \
    --exclude='site-packages' --exclude='dist-packages' . | tar -C "$USR/lib/$PY" -xf -

SITE="$USR/lib/python3/dist-packages"
mkdir -p "$SITE"
echo "==> PyGObject (from the system, built for this Python)"
cp -r /usr/lib/python3/dist-packages/gi "$SITE/"
rm -rf "$SITE/gi/__pycache__"

echo "==> Python dependencies"
DEPS=$($PY - <<'EOF'
import tomllib
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
print(" ".join(d.split("#")[0].strip() for d in deps if not d.lower().startswith("pygobject")))
EOF
)
$PY -m pip --version >/dev/null 2>&1 || { echo "pip for $PY is required"; exit 1; }
# shellcheck disable=SC2086
$PY -m pip install --quiet --no-compile --only-binary=:all: --target "$SITE" $DEPS
$PY -m pip install --quiet --no-compile --no-deps --target "$SITE" "$ROOT"
rm -rf "$SITE"/*.dist-info/RECORD "$SITE/bin"

echo "==> GObject introspection typelibs"
mkdir -p "$USR/lib/girepository-1.0"
for t in GLib-2.0 GObject-2.0 Gio-2.0 GModule-2.0 Gst-1.0 GstBase-1.0 GstAudio-1.0; do
  cp "/usr/lib/$TRIPLET/girepository-1.0/$t.typelib" "$USR/lib/girepository-1.0/"
done
# gi loads libgstreamer etc. by soname at runtime; ldd can't see that.
for lib in libgstreamer-1.0.so.0 libgstbase-1.0.so.0 libgstaudio-1.0.so.0 libgirepository-1.0.so.1; do
  cp -L "/usr/lib/$TRIPLET/$lib" "$USR/lib/"
done

echo "==> GStreamer plugins"
GST_SRC="/usr/lib/$TRIPLET/gstreamer-1.0"
mkdir -p "$USR/lib/gstreamer-1.0"
for p in coreelements playback typefindfunctions audioconvert audioresample volume \
         equalizer autodetect pulseaudio alsa opus vorbis ogg flac mpg123 isomp4 \
         matroska audioparsers id3demux apetag wavparse libav app audiotestsrc; do
  cp "$GST_SRC/libgst$p.so" "$USR/lib/gstreamer-1.0/"
done
cp "/usr/lib/$TRIPLET/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner" "$USR/libexec/"

echo "==> ffmpeg and Deno"
cp -L "$(command -v ffmpeg)" "$USR/bin/ffmpeg"
fetch "https://github.com/denoland/deno/releases/download/v$DENO_VERSION/deno-$ARCH-unknown-linux-gnu.zip" \
      "$TOOLS/deno-$DENO_VERSION.zip" "$DENO_SHA256"
unzip -o -q "$TOOLS/deno-$DENO_VERSION.zip" -d "$USR/bin"

echo "==> X11 helper libraries Qt needs but many systems lack"
# Qt >= 6.5's xcb platform plugin needs libxcb-cursor0, which isn't installed
# by default on several distros (Ubuntu among them). Bundle it from the
# distro package if the build machine doesn't have it either.
if ! ldconfig -p | grep -q "libxcb-cursor.so.0 "; then
  (cd "$TOOLS" && apt-get download libxcb-cursor0 >/dev/null)
  dpkg-deb -x "$TOOLS"/libxcb-cursor0_*.deb "$TOOLS/xcb-cursor"
  cp -L "$TOOLS"/xcb-cursor/usr/lib/$TRIPLET/libxcb-cursor.so.0 "$USR/lib/"
fi
QT="$SITE/PySide6/Qt"

echo "==> shared libraries"
LD_LIBRARY_PATH="$USR/lib" $PY "$ROOT/tools/bundle_libs.py" "$USR/lib" "$TOOLS/excludelist" \
    "$QT/plugins/platforms/libqxcb.so" "$QT/plugins/platforms/libqwayland.so" \
    "$QT/lib/libQt6XcbQpa.so.6" "$USR/lib/libxcb-cursor.so.0" \
    "$USR/bin/$PY" "$USR/bin/ffmpeg" "$USR/lib/$PY/lib-dynload" "$SITE/gi" \
    "$USR/lib/gstreamer-1.0" "$USR/libexec/gst-plugin-scanner" \
    "$USR/lib/libgstreamer-1.0.so.0" "$USR/lib/libgstbase-1.0.so.0" \
    "$USR/lib/libgstaudio-1.0.so.0" "$USR/lib/libgirepository-1.0.so.1"

echo "==> desktop integration"
BUILD_ID="$VERSION-$(date +%Y%m%d%H%M%S)"
sed "s/@BUILD_ID@/$BUILD_ID/" packaging/appimage/AppRun > "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "packaging/$APP.desktop" "$APPDIR/$APP.desktop"
cp "packaging/icons/$APP.svg" "$APPDIR/$APP.svg"
mkdir -p "$USR/share/applications" "$USR/share/metainfo" "$USR/share/icons/hicolor/scalable/apps"
cp "packaging/$APP.desktop" "$USR/share/applications/"
cp "packaging/$APP.metainfo.xml" "$USR/share/metainfo/$APP.appdata.xml"
cp "packaging/icons/$APP.svg" "$USR/share/icons/hicolor/scalable/apps/"

echo "==> AppImage"
OUT="$ROOT/dist/Antiphon-$VERSION-$ARCH.AppImage"
# Extract-and-run avoids needing FUSE on the build machine.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=$ARCH "$TOOLS/appimagetool" --no-appstream "$APPDIR" "$OUT" >/dev/null
echo "$OUT ($(du -h "$OUT" | cut -f1))"
