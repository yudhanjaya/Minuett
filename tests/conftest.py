import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Never touch the real ~/.config or ~/.local/share from tests (QSettings,
# themes, EQ state and the library DB all live under these).
_SANDBOX = Path(tempfile.mkdtemp(prefix="antiphon-tests-"))
os.environ["XDG_CONFIG_HOME"] = str(_SANDBOX / "config")
os.environ["XDG_DATA_HOME"] = str(_SANDBOX / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    "opus": ["-c:a", "libopus", "-b:a", "96k"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "ogg": ["-c:a", "libvorbis"],
}


def make_tone(path: Path, seconds: float = 1.0, **meta: str) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    ext = path.suffix.lstrip(".")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={seconds}", *CODECS[ext]]
    for k, v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run([*cmd, str(path)], check=True)
    return path


@pytest.fixture
def tone():
    return make_tone
