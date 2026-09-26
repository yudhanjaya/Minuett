#!/usr/bin/env python3
"""Generate packaging/flatpak/python-deps.json: pinned wheels for the Flatpak.

flatpak-builder builds offline, so every Python dependency must be listed as
a source with a URL and sha256. This resolves Minuett's dependencies for the
runtime's Python (3.13, x86_64) with ``pip install --dry-run --report`` and
writes a flatpak-builder module that installs exactly those wheels.

PyGObject is not included: the GNOME runtime already provides it, built
against the runtime's own GStreamer.

    python3 tools/gen_flatpak_deps.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "flatpak" / "python-deps.json"
PYTHON_VERSION = "3.13"
# The GNOME 50 runtime ships glibc 2.42, so any manylinux tag up to that works.
PLATFORMS = [f"manylinux_2_{n}_x86_64" for n in range(42, 16, -1)] + ["manylinux2014_x86_64", "linux_x86_64"]
# Provided by the runtime (or not needed inside it).
SKIP = {"pygobject", "pycairo"}


def requirements() -> list[str]:
    import tomllib
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return [d for d in deps if d.split("[")[0].split(">")[0].split("=")[0].strip().lower() not in SKIP]


def main() -> int:
    reqs = requirements()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--ignore-installed",
               "--only-binary=:all:", "--python-version", PYTHON_VERSION,
               "--implementation", "cp", "--target", str(Path(tmp) / "t"),
               "--report", str(report)]
        for plat in PLATFORMS:
            cmd += ["--platform", plat]
        subprocess.run([*cmd, *reqs], check=True)
        data = json.loads(report.read_text())

    sources, names = [], []
    for item in data["install"]:
        name = item["metadata"]["name"]
        if name.lower() in SKIP:
            continue
        info = item["download_info"]
        sha = info["archive_info"]["hashes"]["sha256"]
        sources.append({"type": "file", "url": info["url"], "sha256": sha})
        names.append(f'{name}=={item["metadata"]["version"]}')

    module = {
        "name": "python-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --verbose --no-index --find-links=\"file://${PWD}\" "
            "--prefix=${FLATPAK_DEST} --no-build-isolation " + " ".join(sorted(names))
        ],
        "sources": sorted(sources, key=lambda s: s["url"]),
    }
    OUT.write_text(json.dumps(module, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(sources)} wheels")
    for n in sorted(names):
        print("  ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
