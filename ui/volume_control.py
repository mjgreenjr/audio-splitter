"""A speaker button and a slider: the playback volume, 0-100 %."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QSlider, QToolButton, QWidget

SLIDER_WIDTH = 120
# (volume above which the icon applies, theme icon, text fallback)
_ICONS = [(66, "audio-volume-high", "🔊"), (33, "audio-volume-medium", "🔉"),
          (0, "audio-volume-low", "🔈"), (-1, "audio-volume-muted", "🔇")]


class VolumeControl(QWidget):
    """``changed(percent)`` is emitted when the user moves the slider or toggles the speaker
    (not by ``set_volume``). The speaker mutes, and unmutes back to the earlier level."""

    changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._before_mute = 100
        self.button = QToolButton()
        self.button.setAutoRaise(False)
        self.button.setStyleSheet("QToolButton{border:1px solid gray; border-radius:2px;}")
        self.button.clicked.connect(self._toggle_mute)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(SLIDER_WIDTH)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(self._moved)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.button)
        layout.addWidget(self.slider)
        self._refresh()

    def volume(self) -> int:
        return self.slider.value()

    def set_volume(self, percent: int):
        """Show this level without emitting ``changed``."""
        self.slider.blockSignals(True)
        self.slider.setValue(int(percent))
        self.slider.blockSignals(False)
        self._refresh()

    def _moved(self, value):
        if value > 0:
            self._before_mute = value
        self._refresh()
        self.changed.emit(value)

    def _toggle_mute(self):
        self.slider.setValue(0 if self.slider.value() > 0 else self._before_mute)

    def _refresh(self):
        value = self.slider.value()
        if value > 0:
            self._before_mute = value
        theme, text = next((t, x) for floor, t, x in _ICONS if value > floor)
        icon = QIcon.fromTheme(theme)
        if icon.isNull():
            self.button.setIcon(QIcon())
            self.button.setText(text)
        else:
            self.button.setText("")
            self.button.setIcon(icon)
        self.button.setToolTip("Mute" if value else "Unmute")
        self.slider.setToolTip(f"Volume {value}%")
