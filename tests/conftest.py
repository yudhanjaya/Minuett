import shutil
import subprocess
from pathlib import Path

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
