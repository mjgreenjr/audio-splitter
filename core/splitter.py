# core/splitter.py
import importlib.util
import logging
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

log = logging.getLogger(__name__)

# What the user can pick on the Split tab. Each mode is (label, demucs model,
# extra demucs arguments, subfolder of the stems dir that demucs writes into).
# demucs itself writes <subfolder>/<model>/<track>/<stem>.flac, so the 2-stem
# mode gets its own subfolder: it must not mix into the 4-stem results, which use
# the same model.
MODES = {
    "6": ("6 stems: vocals, drums, bass, guitar, piano, other", "htdemucs_6s", [], ""),
    "4": ("4 stems: vocals, drums, bass, other", "htdemucs", [], ""),
    "2": ("2 stems: vocals and everything else", "htdemucs",
          ["--two-stems", "vocals"], "two_stem"),
}
DEFAULT_MODE = "6"

# The piano stem is dropped when its RMS level is below this (full scale = 1.0).
# Measured on a song with no piano: 0.0002-0.0003; the other stems 0.007-0.07.
# Provisional until checked against a song that does have piano.
PIANO_MIN_RMS = 0.002

# demucs reports progress with tqdm on stderr, redrawn in place with "\r".
_PERCENT = re.compile(rb"(\d{1,3})%\|")


PROJECT_PYTHON = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"


def demucs_python() -> str:
    """The interpreter to run ``python -m demucs`` with.

    This one when it has demucs; otherwise the project's own ``.venv``, so the
    app can be started with any Python (the system one has PyQt6 but no demucs).
    """
    if importlib.util.find_spec("demucs") is not None:
        return sys.executable
    if PROJECT_PYTHON.exists():
        return str(PROJECT_PYTHON)
    raise FileNotFoundError(
        f"demucs is not installed for {sys.executable} and {PROJECT_PYTHON} does not exist. "
        "Start the app from the project's .venv (see CLAUDE.md).")


def normalize_mode(mode) -> str:
    """Unknown or missing values (e.g. an old config) fall back to the default."""
    return str(mode) if str(mode) in MODES else DEFAULT_MODE


def track_dir(stems_dir, input_path, mode) -> Path:
    """Folder demucs writes the stems of ``input_path`` into for ``mode``."""
    _, model, _, subfolder = MODES[normalize_mode(mode)]
    return Path(stems_dir) / subfolder / model / Path(input_path).stem


def rms_level(path) -> float:
    """RMS of a whole audio file, all channels, full scale = 1.0."""
    data, _ = sf.read(path, dtype="float32")
    return float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0


class SplitterThread(QThread):
    finished = pyqtSignal(list)  # list of file paths
    error = pyqtSignal(str)
    progress = pyqtSignal(int)  # 0-100

    def __init__(self, input_path: Path, stems_dir: Path, mode: str = DEFAULT_MODE, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.stems_dir = stems_dir
        self.mode = normalize_mode(mode)

    def command(self):
        """The demucs command line for this split."""
        _, model, extra, subfolder = MODES[self.mode]
        return [demucs_python(), "-m", "demucs", "-n", model, "--flac", *extra,
                "-o", str(Path(self.stems_dir) / subfolder), str(self.input_path)]

    @pyqtSlot()
    def run(self):
        log.info("SplitterThread started for %s (mode %s)", self.input_path, self.mode)
        try:
            cmd = self.command()
            log.info("Running demucs: %s", " ".join(cmd))
            self.progress.emit(0)
            returncode, tail = self._run_demucs(cmd)
            if returncode != 0:
                log.error("Splitter command failed (exit %s): %s", returncode, tail.strip())
                self.error.emit(f"Splitter failed: {tail}")
                return
            # demucs writes <out>/<model>/<track name>/<stem>.flac (16-bit, lossless)
            out_dir = track_dir(self.stems_dir, self.input_path, self.mode)
            if self.mode == "6":
                self._drop_silent_piano(out_dir)
            stem_files = sorted(str(p) for p in out_dir.glob("*.flac"))
            if not stem_files:
                raise FileNotFoundError(f"demucs produced no stems in {out_dir}")
            self.progress.emit(100)
            self.finished.emit(stem_files)
            log.info("SplitterThread finished successfully")
        except Exception as e:
            log.exception("Splitter failed")
            self.error.emit(str(e))

    def _run_demucs(self, cmd):
        """Run demucs, emitting progress; returns (exit code, last output for errors)."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        tail = deque(maxlen=4000)  # last bytes of output, shown if demucs fails
        pending = b""
        last = 0
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            tail.extend(chunk)
            pending += chunk
            # tqdm redraws with "\r", so split on both separators and keep the
            # unfinished piece for the next chunk.
            *lines, pending = re.split(rb"[\r\n]", pending)
            for line in lines:
                m = _PERCENT.search(line)
                if m:
                    pct = min(int(m.group(1)), 99)  # 100 is emitted once the files are in place
                    if pct != last:
                        last = pct
                        self.progress.emit(pct)
        proc.wait()
        return proc.returncode, bytes(tail).decode(errors="replace")

    @staticmethod
    def _drop_silent_piano(track_dir: Path):
        piano = track_dir / "piano.flac"
        if not piano.exists():
            return
        level = rms_level(piano)
        if level < PIANO_MIN_RMS:
            log.info("Dropping piano stem: RMS %.4f below %.4f", level, PIANO_MIN_RMS)
            piano.unlink()
        else:
            log.info("Keeping piano stem: RMS %.4f", level)
