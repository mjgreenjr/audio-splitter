"""Settings tab: the folders, the stem loading time limit and the logging level."""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QRadioButton, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

from config import Config
from core.stemload import normalize_timeout


class SettingsView(QWidget):
    """Edits the settings in ``config``. Nothing changes until Save is pressed; ``saved`` is
    emitted afterwards so the window can apply them (the logging level)."""

    saved = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config

        self.recordings_edit = QLineEdit()
        self.recordings_edit.setToolTip("The Open dialog starts in this folder")
        self.stems_edit = QLineEdit()
        self.stems_edit.setToolTip("Where the stems of a split are written")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 3600)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setToolTip(
            "Loading a song's stems is stopped after this long. Raise it for very long songs "
            "or a slow disk.")
        self.debug_radio = QRadioButton("DEBUG")
        self.info_radio = QRadioButton("INFO")
        self.error_radio = QRadioButton("ERROR")
        self.logging_group = QButtonGroup(self)
        for radio in (self.debug_radio, self.info_radio, self.error_radio):
            self.logging_group.addButton(radio)
        self.logging_group.setExclusive(True)

        form = QFormLayout()
        form.addRow("Audio folder:", self.recordings_edit)
        form.addRow("Stems folder:", self.stems_edit)
        form.addRow("Stem loading timeout:", self.timeout_spin)
        radios = QHBoxLayout()
        for radio in (self.debug_radio, self.info_radio, self.error_radio):
            radios.addWidget(radio)
        form.addRow("Logging level:", radios)

        self.save_btn = QPushButton("Save")
        self.save_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.save_btn.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addStretch(1)  # keep everything at the top
        self.load_from_config()

    def load_from_config(self):
        """Show what is saved (also drops edits that were never saved)."""
        self.recordings_edit.setText(self.config["recordings_dir"])
        self.stems_edit.setText(self.config["stems_dir"])
        self.timeout_spin.setValue(int(normalize_timeout(self.config["stem_load_timeout_s"])))
        level = str(self.config["logging"]).upper()
        radio = {"DEBUG": self.debug_radio, "INFO": self.info_radio}.get(level, self.error_radio)
        radio.setChecked(True)

    def save(self):
        self.config["recordings_dir"] = self.recordings_edit.text()
        self.config["stems_dir"] = self.stems_edit.text()
        self.config["stem_load_timeout_s"] = self.timeout_spin.value()
        self.config["logging"] = ("DEBUG" if self.debug_radio.isChecked()
                                  else "INFO" if self.info_radio.isChecked() else "ERROR")
        self.config.save()
        self.saved.emit()
