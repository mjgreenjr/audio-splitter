"""Split tab: one waveform per stem, a toggle for each, and transport buttons.

The widget only draws and reports: it emits what the user asked for (seek,
play, stop, skip, a changed set of enabled stems) and MainWindow drives the
single player. All rows share one position: moving the marker in any row moves
it in every row.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget,
)

from core.waveform import envelope_from_file
from ui.waveform_view import WaveformWidget

log = logging.getLogger(__name__)

# Display order; any other stem name is listed after these, alphabetically.
STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"]
# Files picked up when the user loads a folder of stems.
AUDIO_EXTENSIONS = {".flac", ".wav", ".ogg", ".mp3"}
ROW_HEIGHT = 72
NAME_WIDTH = 96


def _stem_sort_key(path):
    name = Path(path).stem
    return (STEM_ORDER.index(name) if name in STEM_ORDER else len(STEM_ORDER), name)


class StemView(QWidget):
    seek = pyqtSignal(int)             # ms
    enabled_changed = pyqtSignal(list)  # paths of the enabled stems, in display order
    play_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    skip_back_clicked = pyqtSignal()
    skip_forward_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = []
        self._checks = []
        self._waves = []
        self.is_playing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._rows = QVBoxLayout()
        self._rows.setSpacing(2)
        outer.addLayout(self._rows)

        self._empty = QLabel("Split a file to see its stems here.")
        self._empty.setEnabled(False)
        outer.addWidget(self._empty)

        buttons = QHBoxLayout()
        self.skip_back_btn = self._button("⏮", "go-first", "Go to beginning")
        self.play_btn = self._button("▶", "media-playback-start", "Play/Pause")
        self.stop_btn = self._button("■", "media-playback-stop", "Stop")
        self.skip_fwd_btn = self._button("⏭", "go-last", "Go to end")
        for b in (self.skip_back_btn, self.play_btn, self.stop_btn, self.skip_fwd_btn):
            buttons.addWidget(b)
            b.setEnabled(False)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        self.skip_back_btn.clicked.connect(self.skip_back_clicked)
        self.play_btn.clicked.connect(self.play_clicked)
        self.stop_btn.clicked.connect(self.stop_clicked)
        self.skip_fwd_btn.clicked.connect(self.skip_forward_clicked)

    @staticmethod
    def _button(text, theme_icon, tooltip):
        btn = QToolButton()
        icon = QIcon.fromTheme(theme_icon)
        if icon.isNull():
            btn.setText(text)
        else:
            btn.setIcon(icon)
        btn.setIconSize(QSize(24, 24))
        btn.setToolTip(tooltip)
        btn.setAutoRaise(False)
        btn.setStyleSheet("QToolButton{border:1px solid gray; border-radius:2px;}")
        return btn

    # ------------------------------------------------------------------
    def set_stems(self, files):
        """Read the files here and now, and show one row each (replaces any earlier rows).

        Reading takes a moment, so the window itself loads in the background
        (``StemLoadThread``) and calls ``set_loaded``.
        """
        loaded = []
        for path in files:
            try:
                loaded.append((str(path), *envelope_from_file(path)))
            except Exception:
                log.exception("Could not read stem %s", path)
        self.set_loaded(loaded)

    def set_loaded(self, loaded):
        """Show one row per ``(path, time_ms, amplitude)`` (replaces any earlier rows)."""
        self.clear()
        loaded = sorted(loaded, key=lambda item: _stem_sort_key(item[0]))
        if not loaded:
            return
        peak = max(float(abs(amp).max()) for _, _, amp in loaded if len(amp))
        for i, (path, t, amp) in enumerate(loaded):
            row = QHBoxLayout()
            check = QCheckBox(Path(path).stem)
            check.setChecked(True)
            check.setFixedWidth(NAME_WIDTH)
            check.toggled.connect(self._toggled)
            wave = WaveformWidget(compact=True)
            wave.setFixedHeight(ROW_HEIGHT)
            wave.set_y_peak(peak)
            wave.set_waveform_data(t, amp)
            wave.show_time_axis(i == len(loaded) - 1)
            wave.seek.connect(self._row_seeked)
            row.addWidget(check)
            row.addWidget(wave, 1)
            self._rows.addLayout(row)
            self._paths.append(path)
            self._checks.append(check)
            self._waves.append(wave)
        self._empty.hide()
        # The bottom row carries the time axis, so it needs room for it.
        self._waves[-1].setFixedHeight(ROW_HEIGHT + 26)
        for b in (self.skip_back_btn, self.play_btn, self.stop_btn, self.skip_fwd_btn):
            b.setEnabled(True)

    def clear(self):
        for layout_index in reversed(range(self._rows.count())):
            item = self._rows.takeAt(layout_index)
            row = item.layout()
            while row is not None and row.count():
                w = row.takeAt(0).widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            if row is not None:
                row.deleteLater()
        self._paths, self._checks, self._waves = [], [], []
        self.set_playing(False)
        self._empty.show()
        for b in (self.skip_back_btn, self.play_btn, self.stop_btn, self.skip_fwd_btn):
            b.setEnabled(False)

    # ------------------------------------------------------------------
    def has_stems(self):
        return bool(self._paths)

    def all_paths(self):
        return list(self._paths)

    def enabled_paths(self):
        return [p for p, c in zip(self._paths, self._checks) if c.isChecked()]

    def duration_ms(self):
        return self._waves[0].duration_ms() if self._waves else 0

    def marker_ms(self):
        return int(self._waves[0].marker.value()) if self._waves else 0

    def update_position(self, ms):
        """Move every row's marker (no seek signal)."""
        for w in self._waves:
            w.update_position(ms)

    def set_playing(self, playing):
        self.is_playing = bool(playing)
        name = "media-playback-pause" if playing else "media-playback-start"
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            self.play_btn.setIcon(icon)
        else:
            self.play_btn.setText("⏸" if playing else "▶")

    # ------------------------------------------------------------------
    def _toggled(self, _checked):
        for check, wave in zip(self._checks, self._waves):
            wave.set_dimmed(not check.isChecked())
        self.enabled_changed.emit(self.enabled_paths())

    def _row_seeked(self, ms):
        self.update_position(ms)
        self.seek.emit(ms)
