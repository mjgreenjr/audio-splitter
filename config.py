# config.py
import json
from pathlib import Path

DEFAULT_CONFIG = {
    # Folder the Open dialog starts in
    "recordings_dir": "recordings",
    "stems_dir": "stems",
    # How many stems the Split tab makes: "6" (default), "4" or "2" (see core.splitter.MODES).
    "split_mode": "6",
    # Seconds to wait for a song's stems to load before giving up (raise it for very long songs
    # or a slow disk). Not a positive number: the default of 60 is used.
    "stem_load_timeout_s": 60,
    # Use a string value for the desired logging level.  The application
    # expects one of ``DEBUG``, ``INFO`` or ``ERROR``.  ``DEBUG`` is the
    # default to preserve the historic behaviour of having verbose output
    # during development.
    "logging": "DEBUG"
}

CONFIG_PATH = Path(__file__).parent / "config.json"

class Config:
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                self.data.update(json.load(f))

    def save(self):
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, value):
        self.data[key] = value
