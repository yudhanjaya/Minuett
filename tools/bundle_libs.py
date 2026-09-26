#!/usr/bin/env python3
"""Copy the shared-library dependencies of ELF files into an AppDir.

    bundle_libs.py APPDIR/usr/lib EXCLUDELIST FILE_OR_DIR...

Walks ``ldd`` output for every ELF file given (directories are searched
recursively), copying each resolved library into the target lib dir unless
the host is expected to provide it (the AppImage project's excludelist plus
a few additions). AppRun puts that dir on LD_LIBRARY_PATH.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

# In addition to the official excludelist: the C++ runtime and compiler
# support libs must come from the host (Mesa and other host libs loaded into
# the process depend on them), and PipeWire's client lib must match the host
# daemon's modules.
EXTRA_EXCLUDES = {
    "libstdc++.so.6", "libgcc_s.so.1", "libpipewire-0.3.so.0",
}
LDD_LINE = re.compile(r"^\s*(\S+)\s+=>\s+(\S+)\s+\(0x")


def is_elf(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def load_excludes(path: Path) -> set[str]:
    names = set(EXTRA_EXCLUDES)
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def deps(elf: Path) -> list[tuple[str, Path]]:
    out = subprocess.run(["ldd", str(elf)], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        m = LDD_LINE.match(line)
        if m and m.group(2) != "not":
            found.append((m.group(1), Path(m.group(2))))
    return found


def main() -> int:
    libdir, excl, *targets = sys.argv[1:]
    libdir_p = Path(libdir)
    libdir_p.mkdir(parents=True, exist_ok=True)
    appdir = libdir_p.resolve().parent.parent  # APPDIR/usr/lib -> APPDIR
    excludes = load_excludes(Path(excl))
    queue: list[Path] = []
    for t in map(Path, targets):
        if t.is_dir():
            queue += [p for p in t.rglob("*") if p.is_file() and is_elf(p)]
        elif t.exists():
            queue.append(t)
    seen: set[str] = set()
    copied = 0
    while queue:
        elf = queue.pop()
        for soname, real in deps(elf):
            if soname in seen or soname in excludes or soname.startswith("ld-linux"):
                continue
            seen.add(soname)
            if appdir in real.resolve().parents:
                # Already shipped inside the AppDir (e.g. PySide6's own Qt libs,
                # found via their $ORIGIN rpath); just follow its dependencies.
                queue.append(real)
                continue
            dest = libdir_p / soname
            if not dest.exists():
                shutil.copy2(real.resolve(), dest)
                dest.chmod(0o755)
                copied += 1
            queue.append(dest)
    print(f"bundled {copied} libraries into {libdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
