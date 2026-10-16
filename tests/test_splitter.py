import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core import splitter
from core.splitter import (
    DEFAULT_MODE, MODES, PIANO_MIN_RMS, SplitterThread, normalize_mode, rms_level, track_dir,
)


def _tone(path, level, secs=1, sr=44100):
    t = np.arange(sr * secs) / sr
    data = level * np.sin(2 * np.pi * 440 * t)
    sf.write(path, np.column_stack([data, data]), sr, subtype="PCM_16")


def test_silent_piano_stem_is_dropped(tmp_path):
    _tone(tmp_path / "piano.flac", 0.0005)
    _tone(tmp_path / "guitar.flac", 0.3)
    SplitterThread._drop_silent_piano(tmp_path)
    assert not (tmp_path / "piano.flac").exists()
    assert (tmp_path / "guitar.flac").exists()


def test_audible_piano_stem_is_kept(tmp_path):
    _tone(tmp_path / "piano.flac", 0.1)
    assert rms_level(tmp_path / "piano.flac") > PIANO_MIN_RMS
    SplitterThread._drop_silent_piano(tmp_path)
    assert (tmp_path / "piano.flac").exists()


def test_missing_piano_stem_is_fine(tmp_path):
    SplitterThread._drop_silent_piano(tmp_path)  # must not raise


def test_progress_is_parsed_from_demucs_style_output(tmp_path):
    """tqdm redraws one line with \\r; progress must be reported as it goes, in order."""
    script = (
        "import sys, time\n"
        "for pct in (0, 10, 10, 55, 100):\n"
        "    sys.stderr.write(f' {pct}%|####| x/y [00:01<00:02]\\r'); sys.stderr.flush(); time.sleep(0.05)\n"
    )
    th = SplitterThread(Path("in.flac"), tmp_path)
    seen = []
    th.progress.connect(seen.append)
    code, _ = th._run_demucs([sys.executable, "-c", script])
    assert code == 0
    assert seen == [10, 55, 99]  # 0 is the starting value, repeats are skipped, 100 is held back


def test_failed_demucs_returns_its_output(tmp_path):
    th = SplitterThread(Path("in.flac"), tmp_path)
    code, tail = th._run_demucs([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
    assert code == 3 and "boom" in tail



def test_each_mode_runs_the_right_model_and_writes_to_its_own_folder(tmp_path):
    stems = tmp_path / "stems"
    cmds = {m: SplitterThread(Path("song.flac"), stems, m).command() for m in MODES}
    assert cmds["6"][cmds["6"].index("-n") + 1] == "htdemucs_6s"
    assert cmds["4"][cmds["4"].index("-n") + 1] == "htdemucs"
    assert "--two-stems" not in cmds["6"] and "--two-stems" not in cmds["4"]
    assert cmds["2"][cmds["2"].index("--two-stems") + 1] == "vocals"
    # 4 and 2 use the same model, so they must not share an output folder.
    dirs = {m: track_dir(stems, "song.flac", m) for m in MODES}
    assert len(set(dirs.values())) == 3
    assert dirs["6"] == stems / "htdemucs_6s" / "song"
    assert dirs["4"] == stems / "htdemucs" / "song"
    assert dirs["2"] == stems / "two_stem" / "htdemucs" / "song"
    for mode, cmd in cmds.items():
        out_root = Path(cmd[cmd.index("-o") + 1])
        assert out_root / "htdemucs_6s" / "song" == dirs[mode] or out_root / "htdemucs" / "song" == dirs[mode]


def test_unknown_mode_falls_back_to_the_default():
    assert normalize_mode("6") == "6"
    for bad in (None, "", "3", "six", 7):
        assert normalize_mode(bad) == DEFAULT_MODE
    assert SplitterThread(Path("a.flac"), Path("s"), "nonsense").mode == DEFAULT_MODE


@pytest.mark.slow
@pytest.mark.parametrize("mode, expected", [
    ("6", {"vocals", "drums", "bass", "guitar", "other"}),   # piano only if audible
    ("4", {"vocals", "drums", "bass", "other"}),
    ("2", {"vocals", "no_vocals"}),
])
def test_stems_are_produced(qtbot, tone_flac, tmp_path, mode, expected):
    out = tmp_path / "stems"
    out.mkdir()
    th = SplitterThread(Path(tone_flac), out, mode)
    errors, files, pcts = [], [], []
    th.error.connect(errors.append)
    th.finished.connect(files.extend)
    th.progress.connect(pcts.append)
    th.start()
    # Wake on either outcome so a failed run reports immediately.
    qtbot.waitUntil(lambda: bool(errors or files), timeout=600_000)
    assert not errors, errors
    names = {Path(f).stem for f in files}
    assert expected <= names <= expected | {"piano"}
    assert pcts and pcts[-1] == 100
    th.wait()


def test_python_with_demucs_is_used_as_is():
    from core.splitter import demucs_python
    assert demucs_python() == sys.executable  # the test venv has demucs


def test_without_demucs_the_project_venv_python_is_used(monkeypatch, tmp_path):
    fake = tmp_path / "python"
    fake.write_text("")
    monkeypatch.setattr(splitter.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(splitter, "PROJECT_PYTHON", fake)
    assert splitter.demucs_python() == str(fake)
    assert SplitterThread(Path("a.flac"), tmp_path).command()[0] == str(fake)


def test_without_demucs_or_a_venv_the_error_says_what_to_do(monkeypatch, tmp_path, qtbot):
    monkeypatch.setattr(splitter.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(splitter, "PROJECT_PYTHON", tmp_path / "nope")
    th = SplitterThread(Path("a.flac"), tmp_path)
    errors = []
    th.error.connect(errors.append)
    th.start()
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    assert "demucs is not installed" in errors[0] and ".venv" in errors[0]
    th.wait()
