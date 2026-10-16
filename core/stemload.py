# core/stemload.py
"""Read stem files off the UI thread and reduce each to the points the Split tab draws."""
import logging
import math

from PyQt6.QtCore import QThread, pyqtSignal

from core.waveform import Cancelled, envelope_from_file

log = logging.getLogger(__name__)

# How long loading one song's stems may take before the window stops waiting (config key
# ``stem_load_timeout_s``). A 3-minute song takes about 1.5 s for six stems.
DEFAULT_TIMEOUT_S = 60.0


def normalize_timeout(value) -> float:
    """Seconds to wait; anything that is not a positive number falls back to the default."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return seconds if math.isfinite(seconds) and seconds > 0 else DEFAULT_TIMEOUT_S


class StemLoadThread(QThread):
    """Emits ``loaded`` with a list of ``(path, time_ms, amplitude)`` for every readable file.

    Unreadable files are logged and left out. After ``cancel()`` nothing is emitted: the thread
    stops at the next block and its result, if any, is dropped.
    """
    loaded = pyqtSignal(list)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self._paths = [str(p) for p in paths]
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        result = []
        for path in self._paths:
            if self._cancelled:
                return
            try:
                times, amplitude = envelope_from_file(path, cancelled=lambda: self._cancelled)
            except Cancelled:
                return
            except Exception:
                log.exception("Could not read stem %s", path)
                continue
            result.append((path, times, amplitude))
        if not self._cancelled:
            self.loaded.emit(result)
