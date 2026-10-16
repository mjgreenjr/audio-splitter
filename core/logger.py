# core/logger.py
import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def setup_logging(level: str = "DEBUG"):
    """Configure the root logger.

    The configuration now accepts a string representing the desired
    logging level (e.g. ``"DEBUG"``, ``"INFO"`` or ``"ERROR"``).  The
    function guarantees that a file handler writing to ``logs/app.log`` is
    always present.  The numeric level is derived from the string using
    :func:`logging.getLevelName`.

    Parameters
    ----------
    level: str
        Desired logging level.  Case‑insensitive.
    """

    # Avoid re‑configuring if logging has already been set up.
    if logging.getLogger().handlers:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    # Remove all handlers from all loggers to ensure only the file handler is used
    for logger_name in logging.root.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # Remove any existing handlers to ensure only the file handler is used
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)
    
    def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
        import traceback
        from PyQt6.QtWidgets import QMessageBox
        error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        # Safe approach to prevent Qt crash during message box display
        try:
            # Limit message length to prevent potential Qt issues
            if len(error_msg) > 10000:
                safe_error_msg = error_msg[:10000] + "..."
            else:
                safe_error_msg = error_msg
                
            # Use a more robust approach to ensure the dialog is properly created
            msg_box = QMessageBox(None)
            msg_box.setIcon(QMessageBox.Icon.Critical)  # Changed from QMessageBox.Critical to QMessageBox.Icon.Critical
            msg_box.setWindowTitle("Critical Error")
            msg_box.setText(f"An uncaught exception occurred:\n{safe_error_msg}")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)
            
            # Ensure we're in the main thread context before showing
            msg_box.exec()
            
        except Exception as e:
            logging.critical(f"Failed to display critical error dialog: {e}")
            # Fallback: log directly to console and file
            print(f"Critical Error: {error_msg}")
            logging.critical(f"Uncaught exception: {error_msg}")

    # Global exception handler for uncaught errors
    sys.excepthook = handle_uncaught_exception

    # The application originally logged to both stdout and a file.  The
    # user requested that logs be written only to the file.  To satisfy that
    # requirement we remove the ``StreamHandler`` and keep only the file
    # Suppress verbose logs from third-party libraries
    logging.getLogger('h5py').setLevel(logging.WARNING)

    # handler.
    handlers: list[logging.Handler] = []
    log_path = LOG_DIR / "app.log"
    handlers.append(logging.FileHandler(log_path, mode="a", encoding="utf-8"))
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
    )
