from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import pyqtSignal, Qt
import numpy as np
import pyqtgraph as pg

# Tick steps for the time axis, in ms: (major, minor). The axis picks the smallest major step
# that leaves about one label per TICK_LABEL_PX.
_TICK_STEPS = [(s * 1000, m * 1000) for s, m in (
    (1, 0.5), (2, 1), (5, 1), (10, 2), (15, 5), (30, 10), (60, 10), (120, 30), (300, 60),
    (600, 120), (900, 300), (1800, 300), (3600, 600), (7200, 1800))]
TICK_LABEL_PX = 90


def format_time(ms) -> str:
    """Milliseconds as m:ss, or h:mm:ss from one hour (65000 -> "1:05")."""
    seconds = max(0, int(round(float(ms) / 1000)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class TimeAxis(pg.AxisItem):
    """A bottom axis for data in milliseconds, labelled m:ss with ticks on round times."""

    def tickStrings(self, values, scale, spacing):
        return [format_time(v) for v in values]

    def tickSpacing(self, minVal, maxVal, size):
        span = maxVal - minVal
        if span <= 0 or size <= 0:
            return []  # no range or no width yet (while the window is laid out): no ticks
        target = span / max(2.0, size / TICK_LABEL_PX)
        major, minor = next(((a, b) for a, b in _TICK_STEPS if a >= target), _TICK_STEPS[-1])
        return [(major, 0), (minor, 0)]


_PEN = pg.mkPen("b")
# A switched-off stem: a muted blue. Opaque on purpose, because a transparent pen piles up
# wherever the dense curve overlaps itself and looks solid again.
_PEN_DIMMED = pg.mkPen((45, 45, 110))


class WaveformWidget(QWidget):
    """
    A widget for displaying audio waveforms with a movable marker.

    This widget displays an audio waveform and allows users to seek through
    the audio in two ways:
      * dragging the red marker line, or
      * clicking anywhere on the waveform (click-to-seek).
    The plot itself is not interactive otherwise (no zooming or panning).
    """

    # Signal emitted when user seeks to a new position (milliseconds)
    seek = pyqtSignal(int)

    def __init__(self, parent=None, compact=False):
        """``compact`` is for stacked stem rows: no axes, fixed vertical scale,
        horizontal range locked to the audio so stacked rows line up in time."""
        super().__init__(parent)
        self._compact = compact
        self._y_peak = None  # fixed vertical half-range in compact mode

        # Create main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create plot widget
        self.plot_widget = pg.PlotWidget(axisItems={"bottom": TimeAxis("bottom")})
        layout.addWidget(self.plot_widget)

        # Disable mouse interaction for the plot (no zooming or panning)
        self.plot_widget.getViewBox().setMouseEnabled(x=False, y=False)
        self.plot_widget.getViewBox().setMenuEnabled(False)
        # pyqtgraph's "A" (auto-scale) button pops up when the mouse is over a plot; with zoom
        # disabled it does nothing, so hide it.
        self.plot_widget.hideButtons()

        # Click-to-seek: a click anywhere on the plot jumps the marker there.
        # This is far easier to hit than the thin marker line, especially
        # while it is moving during playback.
        self.plot_widget.scene().sigMouseClicked.connect(self._plot_clicked)

        # Create marker line. A slightly wider pen makes the 1px line much
        # easier to grab with the mouse (pyqtgraph's hover detection follows
        # the painted width).
        self.marker = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen("r", width=3))
        self.marker.setZValue(10)  # Ensure marker is on top

        # Connect marker movement to signal
        self.marker.sigDragged.connect(self._marker_dragged)

        # Add marker to plot
        self.plot_widget.addItem(self.marker)

        # Create waveform curve
        self.curve = self.plot_widget.plot(pen=_PEN)
        self._dimmed = False

        # Set default properties
        self.plot_widget.setLabel('left', 'Amplitude')
        self.plot_widget.setLabel('bottom', 'Time')
        self.plot_widget.showGrid(x=True, y=True)

        if compact:
            self.plot_widget.hideAxis('left')
            self.plot_widget.hideAxis('bottom')
            # Grid lines are drawn by the axes, and only the bottom row has one, so a grid would
            # show on that row alone.
            self.plot_widget.showGrid(x=False, y=False)

        # Initialize with no data
        self.clear()

    def set_y_peak(self, peak):
        """Compact mode: draw with a fixed vertical range of +-peak (so stems share a scale)."""
        self._y_peak = max(float(peak), 1e-6)
        self.plot_widget.setYRange(-self._y_peak, self._y_peak, padding=0.05)

    def set_dimmed(self, dimmed):
        """Draw the waveform faint (a stem that is switched off) or normally."""
        self._dimmed = bool(dimmed)
        self.curve.setPen(_PEN_DIMMED if self._dimmed else _PEN)

    def is_dimmed(self):
        return self._dimmed

    def show_time_axis(self, visible=True):
        """Compact mode: show the time axis (used on the bottom row only)."""
        self.plot_widget.showAxis('bottom', visible)

    def clear(self):
        """Clear the waveform display."""
        self.curve.setData([], [])
        self.marker.setPos(0)
        self.marker.show()

    def set_waveform_data(self, time_points, amplitude_points):
        """
        Set waveform data to display.

        Args:
            time_points: Array of time points (in milliseconds)
            amplitude_points: Array of amplitude values
        """
        if len(time_points) > 0 and len(amplitude_points) > 0:
            self.curve.setData(time_points, amplitude_points)
            if self._compact:
                self.plot_widget.setXRange(0, float(time_points[-1]), padding=0)
                if self._y_peak is None:
                    self.plot_widget.enableAutoRange(axis='y')
                else:
                    self.plot_widget.setYRange(-self._y_peak, self._y_peak, padding=0.05)
            else:
                self.plot_widget.enableAutoRange()

    def has_data(self):
        """Whether a waveform is currently loaded."""
        return (self.curve.xData is not None and len(self.curve.xData) > 0)

    def duration_ms(self):
        """Length of the loaded audio in milliseconds, or 0."""
        if not self.has_data():
            return 0
        return int(self.curve.xData[-1])

    def update_position(self, position_ms):
        """
        Update the marker position to indicate current playback position.

        Args:
            position_ms: Current playback position in milliseconds
        """
        self.marker.setPos(position_ms)

    def seek_to_ms(self, ms):
        """Move the marker to ``ms`` (clamped to the data range) and emit."""
        ms = max(0, min(int(ms), self.duration_ms()))
        self.marker.setPos(ms)
        self.seek.emit(ms)

    def _plot_clicked(self, event):
        """Click-to-seek: jump the marker to the clicked time."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.has_data():
            return
        view_box = self.plot_widget.getViewBox()
        data_pos = view_box.mapSceneToView(event.scenePos())
        self.seek_to_ms(data_pos.x())

    def _marker_dragged(self):
        """Handle marker drag events."""
        if not self.has_data():
            return
        # Clamp to the waveform range so the marker can't be dragged past
        # the end (or before the start) of the audio. Snap it back inside
        # the range so it visually stops at the edge instead of sitting
        # out past the waveform.
        raw_pos = int(self.marker.value())
        pos = max(0, min(raw_pos, self.duration_ms()))
        if pos != raw_pos:
            self.marker.setPos(pos)
        self.seek.emit(pos)
