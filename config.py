# config.py
"""Settings in ``config.toml``: a commented template that the program owns.

Every setting is listed with its default and a description. A line the user has uncommented
is theirs and is kept for good; commented lines are regenerated. The file carries the version
of the program that wrote it (``[meta] config_version``). When a newer minor or major version
starts, ``migrate`` rebuilds the file: a backup is written, set values are kept, renamed
settings are ported and unknown ones go to a "retired settings" ledger at the bottom, and the
rest of the template is regenerated. Comments the user typed are not kept; the file is
managed by the program.
"""
import json
import logging
import os
import tempfile
import tomllib
from pathlib import Path

from version import __version__

log = logging.getLogger(__name__)

# (key, default, description). The order is the order in the file. Every setting is applied as
# soon as it is saved; none needs a restart.
SETTINGS = [
    ("recordings_dir", "recordings",
     "Folder the Load from File dialog starts in."),
    ("stems_dir", "stems",
     "Where the stems of a split are written, and where Load Stems starts. A relative path is "
     "relative to the folder the program was started from."),
    ("split_mode", "6",
     'How many stems the Split tab makes: "6" (vocals, drums, bass, guitar, piano, other), '
     '"4" (without guitar and piano) or "2" (vocals and everything else). The Split tab '
     "remembers the choice here."),
    ("stem_load_timeout_s", 60,
     "Seconds to wait for a song or its stems to load before giving up (raise it for very "
     "long songs or a slow disk). Not a positive number: the default of 60 is used."),
    ("volume", 100,
     "Playback volume in percent, 0 (silent) to 100 (full). It is the volume of the app's own "
     "playback, not of the output device. The volume slider remembers its level here."),
    ("logging", "DEBUG",
     'How much is written to the log in logs/: "DEBUG" (most), "INFO" or "ERROR" (least).'),
]
DEFAULTS = {key: default for key, default, _ in SETTINGS}

# Renamed settings: old key -> (new key, port until this version). Until then the old value is
# moved to the new key when a file is migrated.
RENAMES = {}

CONFIG_PATH = Path(__file__).parent / "config.toml"
LEDGER_MARK = "# --- retired settings (managed by audio-splitter) ---"
_HEADER = [
    "# audio-splitter settings",
    "#",
    "# Every setting is listed below with its default, commented out. To change one, remove the",
    '# leading "# " and edit the value; the Settings tab does the same when you press Save.',
    "# The program rewrites this file when it is updated: what you set is kept, comments you add",
    "# yourself are not, and settings that no longer exist move to the list at the bottom.",
]


def parse_version(text):
    """``"1.10.2"`` as ``(1, 10, 2)``; anything that is not three whole numbers is ``(0, 0, 0)``."""
    try:
        parts = tuple(int(p) for p in str(text).split("."))
    except ValueError:
        return (0, 0, 0)
    return parts + (0,) * (3 - len(parts)) if 0 < len(parts) <= 3 else (0, 0, 0)


def toml_value(value):
    """A Python value as TOML text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "inf" if value == float("inf") else "-inf" if value == float("-inf") \
            else "nan" if value != value else repr(value)
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))   # a JSON string is a valid TOML basic string


def _wrap(text, width=96):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def render(values=None, ledger=(), version=None):
    """The text of the file: ``values`` (settings the user set) uncommented, the rest as
    commented defaults, then the version stamp and the retired-settings ledger."""
    values = values or {}
    version = version or __version__
    out = list(_HEADER)
    for key, default, description in SETTINGS:
        out.append("")
        out += [f"# {line}" for line in _wrap(description)]
        if key in values:
            out.append(f"{key} = {toml_value(values[key])}")
        else:
            out.append(f"# {key} = {toml_value(default)}")
    out += ["", "[meta]", "# Written by the program; do not edit.",
            f'config_version = "{version}"']
    if ledger:
        out += ["", LEDGER_MARK, *ledger]
    return "\n".join(out) + "\n"


def default_template():
    return render()


def _read_ledger(text):
    if LEDGER_MARK not in text:
        return []
    return [l for l in text.split(LEDGER_MARK, 1)[1].splitlines() if l.strip()]


def _write_atomic(path, text):
    path = Path(path)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def split_values(data):
    """Sort parsed file contents into ``(settings, ledger)``: known settings (renamed ones
    ported while the port lasts) and ledger lines for everything else."""
    data = {k: v for k, v in data.items() if k != "meta"}
    values, ledger = {}, []
    for old, (new, until) in RENAMES.items():
        if old in data and parse_version(__version__) < parse_version(until):
            value = data.pop(old)
            if new in data:
                ledger.append(f"# {old} = {toml_value(value)}   # replaced by {new}")
            else:
                data[new] = value
    for key, value in data.items():
        if key in DEFAULTS:
            values[key] = value
        else:
            ledger.append(f"# {key} = {toml_value(value)}   # not a setting of version {__version__}")
    return values, ledger


def migrate(path):
    """Rebuild ``path`` for this version if it was written by an older minor or major version.

    Returns what was done: ``"created"``, ``"migrated"``, ``"replaced"`` (the file could not be
    read), ``"newer"`` (written by a newer version: left alone) or ``None`` (nothing to do).
    """
    path = Path(path)
    if not path.exists():
        _write_atomic(path, default_template())
        return "created"
    text = path.read_text(encoding="utf-8")
    backup = path.with_name(path.name + ".bak")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        backup.write_text(text, encoding="utf-8")
        _write_atomic(path, default_template())
        log.error("%s could not be read (%s); it is replaced by a fresh one, the old one is "
                  "in %s", path, e, backup)
        return "replaced"
    meta = data.get("meta")
    stamp = parse_version(meta.get("config_version") if isinstance(meta, dict) else None)
    current = parse_version(__version__)
    if stamp[:2] > current[:2]:
        log.warning("%s was written by version %s, newer than this one (%s); leaving it alone",
                    path, ".".join(map(str, stamp)), __version__)
        return "newer"
    if stamp[:2] == current[:2]:
        return None
    values, ledger = split_values(data)
    kept_ledger = _read_ledger(text)
    backup.write_text(text, encoding="utf-8")
    _write_atomic(path, render(values, kept_ledger + ledger))
    log.info("Settings file migrated from %s to %s: %d kept, %d retired; backup in %s",
             ".".join(map(str, stamp)), __version__, len(values), len(ledger), backup)
    return "migrated"


def import_json(json_path, path):
    """One-time move from the old ``config.json`` to ``config.toml``: values that differ from
    the defaults are kept (the old file wrote every setting, so equal ones were never chosen),
    unknown ones go to the ledger. The old file is renamed ``config.json.bak``."""
    json_path = Path(json_path)
    try:
        old = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(old, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        log.error("%s could not be read (%s); starting from the defaults", json_path, e)
        old = {}
    values, ledger = split_values(old)
    values = {k: v for k, v in values.items() if v != DEFAULTS[k]}
    _write_atomic(path, render(values, ledger))
    if json_path.exists():
        json_path.replace(json_path.with_name(json_path.name + ".bak"))
    log.info("Settings moved from %s to %s: %d kept, %d retired", json_path, path,
             len(values), len(ledger))


class Config:
    """The settings, readable as ``config["key"]``. ``save()`` writes only the values that
    differ from the defaults (uncommented); everything else stays a commented default."""

    def __init__(self):
        self.data = DEFAULTS.copy()
        self._set = {}      # settings that are uncommented in the file
        self._ledger = []
        self._newer = False  # the file was written by a newer version: never rewritten
        self.load()

    def load(self):
        path = CONFIG_PATH
        try:
            legacy = path.with_name("config.json")
            if not path.exists() and legacy.exists():
                import_json(legacy, path)
            self._newer = migrate(path) == "newer"
            text = path.read_text(encoding="utf-8")
            file_values, unknown = split_values(tomllib.loads(text))
        except (OSError, tomllib.TOMLDecodeError):
            log.exception("Settings could not be read from %s; using the defaults", path)
            return
        self._set = file_values
        self._ledger = _read_ledger(text) + unknown
        self.data = {**DEFAULTS, **file_values}

    def save(self):
        if self._newer:
            log.error("%s was written by a newer version; not saving over it", CONFIG_PATH)
            return
        _write_atomic(CONFIG_PATH, render(self._set, self._ledger))

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, value):
        self.data[key] = value
        if key in DEFAULTS and value == DEFAULTS[key]:
            self._set.pop(key, None)   # back to the default: commented out again
        else:
            self._set[key] = value
