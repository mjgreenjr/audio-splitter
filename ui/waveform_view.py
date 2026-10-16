from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import pyqtSignal, Qt
import numpy as np
import pyqtgraph as pg

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
        self.plot_widget = pg.PlotWidget()
        layout.addWidget(self.plot_widget)

        # Disable mouse interaction for the plot (no zooming or panning)
        self.plot_widget.getViewBox().setMouseEnabled(x=False, y=False)
        self.plot_widget.getViewBox().setMenuEnabled(False)

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
        self.curve = self.plot_widget.plot(pen='b')

        # Set default properties
        self.plot_widget.setLabel('left', 'Amplitude')
        self.plot_widget.setLabel('bottom', 'Time (ms)')
        self.plot_widget.showGrid(x=True, y=True)

        if compact:
            self.plot_widget.hideAxis('left')
            self.plot_widget.hideAxis('bottom')
            self.plot_widget.showGrid(x=True, y=False)

        # Initialize with no data
        self.clear()

    def set_y_peak(self, peak):
        """Compact mode: draw with a fixed vertical range of +-peak (so stems share a scale)."""
        self._y_peak = max(float(peak), 1e-6)
        self.plot_widget.setYRange(-self._y_peak, self._y_peak, padding=0.05)

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
