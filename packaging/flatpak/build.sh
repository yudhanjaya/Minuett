#!/usr/bin/env bash
# Build the Flatpak and export a single-file bundle to dist/.
# Needs: flatpak, the org.flatpak.Builder app, org.gnome.Sdk//50.
set -euo pipefail
cd "$(dirname "$0")/../.."
APP=io.github.yudhanjaya.Antiphon
VERSION=$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
mkdir -p dist
flatpak run org.flatpak.Builder --user --force-clean --repo=build-repo \
    --state-dir=.flatpak-builder build-dir "packaging/flatpak/$APP.yml"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    build-repo "dist/Antiphon-$VERSION-x86_64.flatpak" "$APP"
echo "dist/Antiphon-$VERSION-x86_64.flatpak"
