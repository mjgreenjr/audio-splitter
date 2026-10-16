"""Shared fixtures. Tests run Qt offscreen and with a silent SDL audio driver."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Tests never read or write the real config.toml (it holds the user's settings)."""
    import config
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path_factory.mktemp("cfg") / "config.toml")


@pytest.fixture(autouse=True)
def silent_ffplay(monkeypatch):
    """ffplay plays into SDL's dummy driver: real process, no sound."""
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def tone_wav(tmp_path):
    """A 3 s, 440 Hz stereo tone at 44.1 kHz."""
    sr, secs = 44100, 3
    t = np.arange(sr * secs) / sr
    data = 0.5 * np.sin(2 * np.pi * 440 * t)
    path = tmp_path / "tone.wav"
    sf.write(path, np.column_stack([data, data]), sr)
    return path


@pytest.fixture
def tone_flac(tmp_path):
    """The same 3 s tone, as 16-bit FLAC."""
    sr, secs = 44100, 3
    t = np.arange(sr * secs) / sr
    data = 0.5 * np.sin(2 * np.pi * 440 * t)
    path = tmp_path / "tone.flac"
    sf.write(path, np.column_stack([data, data]), sr, subtype="PCM_16")
    return path


def ffplay_pids(needle: str) -> list[int]:
    """PIDs of running ffplay processes whose command line contains needle.

    Anchored at the start so a shell whose script merely mentions ffplay and the
    needle is not counted.
    """
    out = subprocess.run(["pgrep", "-f", f"^ffplay .*{re.escape(needle)}"],
                         capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


@pytest.fixture
def needle(tmp_path):
    """Matches only this test's ffplay processes (its temp folder is in their command
    line), so a real app playing stems at the same time is not counted."""
    return str(tmp_path)
