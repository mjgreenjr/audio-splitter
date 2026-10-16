# main.py
import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from core.logger import setup_logging
from config import Config

# Enable logging.  The application normally starts in "debug" mode only
# when the user passes a command‑line flag; we always enable a file handler
# here to make troubleshooting easier.  The log file is written to
# <workspace>/logs/app.log.
# Initialise logging based on the configuration file.  The configuration
# module provides a ``logging`` key which is a string such as ``DEBUG``.
setup_logging(Config()["logging"])

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Optional first argument: an audio file to open (``audio-splitter song.flac``).
    win = MainWindow(initial_file=sys.argv[1] if len(sys.argv) > 1 else None)
    win.show()
    sys.exit(app.exec())
