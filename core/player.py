"""Audio playback engine: a single long-lived supervisor thread.

Design notes (learned the hard way):
- The worker thread owns at most one ffplay process at a time. There is no
  code path that spawns ffplay outside the supervisor loop, so overlapping
  ("double audio") playback is structurally impossible.
- All public control methods (play/pause/resume/seek/stop) are non-blocking:
  they take a lock, update state, wake the worker or kill the process, and
  return. The UI thread therefore never waits on the worker -> no ANR.
- The worker loop never blocks on I/O: stderr is drained with select() and
  the loop polls, so a stuck ffplay cannot wedge the thread either.
- Position/timer control crosses threads via queued signals, never via
  QTimer.singleShot() from the worker (which silently never fires: a worker
  thread has no event loop).
- ffplay's -ss option takes *seconds*; all offsets here are milliseconds and
  are converted by _ms_to_ffplay_ss() at the single spawn site.
- Several stems play as ONE ffplay: the enabled files are mixed by an amix
  filter graph (mix_graph). ffplay ignores -ss for filter input, so the start
  offset goes into each amovie as seek_point. Changing the enabled set
  (set_sources) restarts that one process from the current position, exactly
  like a seek, so it can never cause double audio either.
"""

import logging
import re
import select
import subprocess
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot, QTimer

log = logging.getLogger(__name__)


def _ms_to_ffplay_ss(position_ms: int) -> str:
    """Convert a millisecond offset to the seconds string ffplay -ss expects."""
    return f"{max(0, position_ms) / 1000:.3f}"


def normalize_volume(value) -> int:
    """A volume setting as whole percent 0-100; anything that is not a number is 100 (full)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 100
    return max(0, min(100, int(round(value))))


def _lavfi_escape(path: str) -> str:
    """Escape a file name for amovie's filename option inside a filter graph.

    ffmpeg parses it twice: first as a filter option (where \\ ' : = are
    special), then as part of the graph (where \\ ' [ ] , ; are special).
    """
    def esc(text, chars):
        return "".join("\\" + c if c in chars else c for c in text)
    return esc(esc(path, "\\':="), "\\'[],;")


def mix_graph(paths, start_ms: int, duration_ms: int) -> str:
    """ffplay lavfi graph that plays ``paths`` mixed together from ``start_ms``.

    Levels are summed, not averaged (normalize=0): stems that add up to the
    original song then sound like it. No paths gives silence that lasts to the
    end of the track, so position and "finished" behave as usual.
    """
    if not paths:
        remaining = max(0.01, (duration_ms - max(0, start_ms)) / 1000)
        return f"anullsrc=r=44100:cl=stereo:d={remaining:.3f}"
    seek = _ms_to_ffplay_ss(start_ms)
    inputs = [f"amovie={_lavfi_escape(str(p))}:seek_point={seek}" for p in paths]
    if len(inputs) == 1:
        return inputs[0]
    labelled = [f"{src}[a{i}]" for i, src in enumerate(inputs)]
    labels = "".join(f"[a{i}]" for i in range(len(inputs)))
    return ";".join(labelled) + f";{labels}amix=inputs={len(inputs)}:normalize=0:duration=longest"


class PlayerThread(QThread):
    position_ms = pyqtSignal(int)
    finished = pyqtSignal()  # natural end of file only (not pause/stop/seek)
    error = pyqtSignal(str)
    _timer_start_requested = pyqtSignal()
    _timer_stop_requested = pyqtSignal()

    def __init__(self, file_path, parent=None, volume=100):
        """``file_path`` is one audio file, or a non-empty list of stem files to mix.
        ``volume`` is ffplay's own level in percent (not the output device's)."""
        super().__init__(parent)
        if isinstance(file_path, (list, tuple)):
            if not file_path:
                raise ValueError("a stem player needs at least one file")
            self._mix = [str(p) for p in file_path]  # the enabled stems
            self.file_path = self._mix[0]             # used for the duration
        else:
            self._mix = None                          # plain single-file playback
            self.file_path = file_path

        self._volume = normalize_volume(volume)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_thread = False

        self._process = None          # the single supervised ffplay, if any
        self._play_request_ms = None  # pending (re)start offset
        self._playing = False         # an ffplay is currently active
        self._paused = False

        self._base_ms = 0
        self._position = 0
        self._start_time = None

        self._position_timer = QTimer(self)
        self._position_timer.timeout.connect(self._update_position)
        self._timer_start_requested.connect(self._start_position_timer)
        self._timer_stop_requested.connect(self._stop_position_timer)

        self.duration = self._get_duration()

    # ------------------------------------------------------------------
    # internal timer control (executed in the main thread via signals)
    # ------------------------------------------------------------------
    @pyqtSlot()
    def _start_position_timer(self):
        self._position_timer.start(100)

    @pyqtSlot()
    def _stop_position_timer(self):
        self._position_timer.stop()

    def _get_duration(self):
        """Duration of the audio file in milliseconds, via ffprobe."""
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries",
                   "format=duration", "-of", "default=nw=1:nk=1", self.file_path]
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=10)
            if result.returncode == 0:
                return int(float(result.stdout.strip()) * 1000)
        except Exception as e:
            log.debug("Could not determine duration for %s: %s",
                      self.file_path, e)
        return 10000000  # 10000 s fallback

    # ------------------------------------------------------------------
    # public control API -- all non-blocking, safe from any thread
    # ------------------------------------------------------------------
    @pyqtSlot(int)
    def play(self, start_ms):
        """(Re)start playback from start_ms."""
        log.info("play(%d)", start_ms)
        with self._lock:
            self._play_request_ms = max(0, int(start_ms))
            self._paused = False
        self._kill_process()  # defensive; the monitor also guards this
        self._wake.set()

    @pyqtSlot()
    def pause(self):
        log.info("pause()")
        with self._lock:
            if not self._playing or self._paused:
                return
            self._paused = True
            self._playing = False
        self._kill_process()
        self._timer_stop_requested.emit()
        # _position stays frozen; resume() continues from it.

    @pyqtSlot()
    def resume(self):
        log.info("resume()")
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            self._play_request_ms = self._position
        self._wake.set()

    @pyqtSlot(int)
    def seek(self, position_ms):
        """Move the playhead. Audio restarts only if currently playing."""
        position_ms = max(0, int(position_ms))
        log.debug("seek(%d)", position_ms)
        with self._lock:
            restart = self._playing and not self._paused
            self._position = position_ms
            self._base_ms = position_ms
            if restart:
                self._play_request_ms = position_ms
                self._paused = False
        # The marker follows the drag in every case; audio restarts only
        # when it was playing. Seeking while paused never autoplays.
        self.position_ms.emit(position_ms)
        if restart:
            self._kill_process()
            self._wake.set()

    @pyqtSlot(int)
    def set_volume(self, percent):
        """Change ffplay's volume (0-100). ffplay only takes it at start, so while playing this
        restarts the one process from the current position, like a seek; otherwise it applies
        at the next start."""
        with self._lock:
            self._volume = normalize_volume(percent)
            position = self._position
        self.seek(position)

    def volume(self):
        with self._lock:
            return self._volume

    @pyqtSlot(list)
    def set_sources(self, paths):
        """Change which stems are mixed. Restarts from the current position if playing."""
        with self._lock:
            if self._mix is None:
                raise RuntimeError("set_sources needs a player created with a list of stems")
            self._mix = [str(p) for p in paths]
            position = self._position
        # Same path as a seek: restarts the single ffplay only while playing.
        self.seek(position)

    @pyqtSlot()
    def stop(self):
        log.info("stop()")
        with self._lock:
            self._paused = False
            self._playing = False
            self._position = 0
            self._base_ms = 0
            self._play_request_ms = None
        self._kill_process()
        self._timer_stop_requested.emit()
        self.position_ms.emit(0)

    def shutdown(self):
        """Permanently stop the thread (app exit / file switch)."""
        log.debug("shutdown()")
        with self._lock:
            self._stop_thread = True
        self._wake.set()
        self._kill_process()
        self._timer_stop_requested.emit()
        self.wait()

    def get_position(self):
        with self._lock:
            return self._position

    def is_paused(self):
        with self._lock:
            return self._paused

    def is_playing(self):
        with self._lock:
            return self._playing and not self._paused

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------
    def run(self):
        log.debug("PlayerThread supervisor started for %s", self.file_path)
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                if self._stop_thread:
                    break
                start_ms = self._play_request_ms
                self._play_request_ms = None
            if start_ms is None:
                continue  # spurious wake (pause/stop/shutdown)
            self._launch(start_ms)
        log.debug("PlayerThread supervisor exiting")

    def _launch(self, start_ms):
        # Defensive: there must never be two ffplay processes.
        self._kill_process()
        with self._lock:
            mix = None if self._mix is None else list(self._mix)
            volume = self._volume
        if mix is None:
            source = ["-ss", _ms_to_ffplay_ss(start_ms), str(self.file_path)]
        else:
            source = ["-f", "lavfi", mix_graph(mix, start_ms, self.duration)]
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
               "-volume", str(volume)] + source
        log.info("Starting ffplay: %s", " ".join(cmd))
        # Spawn and register under one lock hold, so a concurrent stop()
        # either kills this exact process or provably happens-before it.
        err = None
        with self._lock:
            try:
                proc = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                                        stdout=subprocess.PIPE)
            except Exception as e:
                err = str(e)
                proc = None
                self._playing = False
            else:
                self._process = proc
                self._playing = True
                self._paused = False
                self._base_ms = start_ms
                self._position = start_ms
                self._start_time = None
        if proc is None:
            log.error("Could not start ffplay: %s", err)
            self._safe_emit_error(err or "could not start ffplay")
            return
        self._timer_start_requested.emit()
        self._monitor(proc)

    def _monitor(self, proc):
        """Watch one ffplay until it ends naturally or is superseded."""
        while True:
            if proc.poll() is not None:
                break
            with self._lock:
                if self._play_request_ms is not None or self._stop_thread:
                    break  # superseded: a fresh launch follows
                paused = self._paused
            if paused:
                break  # killed by pause(): idle, no finished signal
            self._drain_stderr(proc)
            time.sleep(0.05)
        # Reap; make sure nothing is left behind.
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        with self._lock:
            # If someone else (stop()/pause()/a newer launch) already cleared
            # or replaced our process handle, this death was not natural --
            # so it must not emit finished.
            proc_replaced = (self._process is not proc)
            if self._process is proc:
                self._process = None
            natural_end = (not self._paused
                           and self._play_request_ms is None
                           and not self._stop_thread
                           and not proc_replaced)
            self._playing = False
        self._timer_stop_requested.emit()
        if natural_end:
            log.info("Playback finished naturally")
            self.finished.emit()

    def _drain_stderr(self, proc):
        """Non-blocking stderr drain so the worker never wedges on I/O."""
        try:
            readable, _, _ = select.select([proc.stderr], [], [], 0)
            if readable:
                line = proc.stderr.readline()
                if line:
                    self._handle_stderr_line(line)
        except Exception:
            pass

    def _handle_stderr_line(self, stderr_line):
        try:
            error_msg = stderr_line.decode("utf-8", errors="ignore").strip()
        except Exception:
            return
        if not error_msg:
            return
        # Strip ANSI escape sequences (e.g. \x1b[2K) that produced empty dialogs.
        clean = re.sub(r"\x1b\[2K", "", error_msg)
        clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", clean)
        if not clean.strip():
            log.debug("Filtering out ANSI-only stderr message")
            return
        log.debug("ffplay stderr: %r", clean)
        if len(clean) < 10000:
            self._safe_emit_error(clean)

    def _kill_process(self):
        with self._lock:
            proc = self._process
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        except Exception as e:
            log.warning("Error killing ffplay: %s", e)
        with self._lock:
            if self._process is proc:
                self._process = None

    def _safe_emit_error(self, error_msg):
        """Emit error; signal emission is thread-safe (auto-queued)."""
        try:
            if error_msg and len(error_msg) < 10000:
                self.error.emit(error_msg)
        except Exception as e:
            log.warning("Error emitting error signal: %s", e)

    def _update_position(self):
        """100ms UI tick: base offset + wall-clock elapsed."""
        with self._lock:
            # While a restart is pending (seek or stem toggle) the old ffplay is
            # still being torn down: hold the position at the new start instead
            # of adding the time played since the old start (it used to jump).
            restarting = self._play_request_ms is not None
            if self._playing and not self._paused and not restarting:
                now = time.time()
                if self._start_time is None:
                    self._start_time = now
                else:
                    self._position = (self._base_ms
                                      + int((now - self._start_time) * 1000))
            else:
                self._start_time = None
            pos = self._position
        self.position_ms.emit(pos)
