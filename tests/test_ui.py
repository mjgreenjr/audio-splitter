from pathlib import Path

import pytest

from ui.main_window import MainWindow
from conftest import ffplay_pids


@pytest.fixture
def win(qtbot, monkeypatch, tmp_path):
    # Don't let tests block on modal dialogs.
    from PyQt6.QtWidgets import QMessageBox
    for fn in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, fn, lambda *a, **k: None)
    w = MainWindow()
    w.config["stems_dir"] = str(tmp_path / "stems")  # never the checkout's real stems/ folder
    qtbot.addWidget(w)
    yield w
    if w._player is not None:
        w._player.shutdown()
        w._player = None


def settle(win, qtbot):
    """Wait until the background stem load (if any) has finished and its rows are showing."""
    qtbot.waitUntil(lambda: not win.stems_loading(), timeout=10000)


def test_loading_a_file_fills_waveform_and_enables_controls(win, tone_wav):
    assert not win.play_btn.isEnabled()
    win._load_file(Path(tone_wav))
    assert win.waveform.has_data()
    assert 2900 <= win.waveform.duration_ms() <= 3100
    for b in (win.play_btn, win.stop_btn, win.skip_back_btn, win.skip_fwd_btn):
        assert b.isEnabled()


def test_loading_bad_file_does_not_crash(win, tmp_path):
    bad = tmp_path / "nope.wav"
    bad.write_bytes(b"not audio")
    win._load_file(bad)
    assert not win.waveform.has_data()


def test_play_then_stop_resets_marker(win, tone_wav, qtbot):
    win._load_file(Path(tone_wav))
    win._on_play_clicked()
    assert win.is_playing
    qtbot.waitUntil(lambda: len(ffplay_pids(tone_wav.name)) == 1, timeout=5000)
    win._on_stop_clicked()
    assert not win.is_playing
    assert win.waveform.marker.value() == 0
    qtbot.waitUntil(lambda: ffplay_pids(tone_wav.name) == [], timeout=5000)


def test_skip_forward_uses_waveform_duration(win, tone_wav):
    win._load_file(Path(tone_wav))
    player = win._ensure_player()
    seen = []
    player.seek = lambda ms: seen.append(ms)
    win._on_skip_forward()
    assert seen == [win.waveform.duration_ms()]
    assert seen[0] < 10_000_000  # not the ffprobe fallback


def test_loading_new_file_replaces_player(win, tone_wav, tmp_path):
    win._load_file(Path(tone_wav))
    first = win._ensure_player()
    other = tmp_path / "other.wav"
    other.write_bytes(tone_wav.read_bytes())
    win._load_file(other)
    assert win._player is None
    assert win._ensure_player() is not first


def test_current_file_is_shown_on_both_tabs(win, tone_wav):
    for edit in (win.file_edit_play, win.file_edit_split):
        assert edit.isReadOnly() and edit.text() == ""
    win._load_file(Path(tone_wav))
    for edit in (win.file_edit_play, win.file_edit_split):
        assert edit.text() == str(tone_wav)  # the full path
        assert edit.toolTip() == str(tone_wav)


def test_failed_load_keeps_showing_the_previous_file(win, tone_wav, tmp_path):
    win._load_file(Path(tone_wav))
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    win._load_file(bad)
    assert win.file_edit_play.text() == str(tone_wav)
    assert win.file_edit_split.text() == str(tone_wav)


def test_buttons_stay_short_and_the_path_box_takes_the_width(win):
    from PyQt6.QtWidgets import QSizePolicy, QTabWidget
    win.resize(1000, 600)
    win.show()
    tabs = win.centralWidget()
    assert isinstance(tabs, QTabWidget)
    for index, edit, button in ((0, win.file_edit_play, win.load_btn),
                                (1, win.file_edit_split, win.split_btn)):
        tabs.setCurrentIndex(index)
        win.repaint()
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
        assert button.width() < 160
        assert edit.width() > 4 * button.width()
        assert edit.x() < button.x()  # the box comes first, in front of the button


def test_split_shows_progress_and_blocks_a_second_split(win, tone_wav, tone_flac, monkeypatch, tmp_path, qtbot):
    from PyQt6.QtCore import QObject, pyqtSignal
    win._load_file(Path(tone_wav))
    win.config["stems_dir"] = str(tmp_path / "stems")

    class FakeSplitter(QObject):
        finished = pyqtSignal(list)
        error = pyqtSignal(str)
        progress = pyqtSignal(int)
        def __init__(self, *a, **k):
            super().__init__()
        def start(self):
            pass

    monkeypatch.setattr("ui.main_window.SplitterThread", FakeSplitter)
    win._start_split()
    assert not win.split_btn.isEnabled()
    win._splitter.progress.emit(40)
    assert win.split_progress.value() == 40
    import shutil
    stems = []
    for name in ("vocals", "guitar"):
        dest = tmp_path / f"{name}.flac"
        shutil.copy(tone_flac, dest)
        stems.append(str(dest))
    win._splitter.finished.emit(stems)
    settle(win, qtbot)
    assert win.split_btn.isEnabled()
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["vocals", "guitar"]


def _fake_splitter(monkeypatch):
    """Replace the splitter with one that records that it was started."""
    from PyQt6.QtCore import QObject, pyqtSignal
    started = []

    class FakeSplitter(QObject):
        finished = pyqtSignal(list)
        error = pyqtSignal(str)
        progress = pyqtSignal(int)
        def __init__(self, *a, **k):
            super().__init__()
        def start(self):
            started.append(True)

    monkeypatch.setattr("ui.main_window.SplitterThread", FakeSplitter)
    return started


def _answer_resplit(monkeypatch, button):
    """Stand in for the "Split again?" question; returns the list of questions asked."""
    from PyQt6.QtWidgets import QMessageBox
    asked = []

    def fake(parent, title, text, *args):
        asked.append(text)
        return button
    monkeypatch.setattr(QMessageBox, "question", fake)
    return asked


def test_splitting_a_song_that_was_split_before_asks_first(win, tone_wav, tone_flac, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    started = _fake_splitter(monkeypatch)
    _make_track_stems(tmp_path, tone_flac, "htdemucs_6s", ("vocals", "drums"))
    win._load_file(Path(tone_wav))
    asked = _answer_resplit(monkeypatch, QMessageBox.StandardButton.Yes)
    win._start_split()
    assert len(asked) == 1 and "tone.wav" in asked[0] and "2 stems" in asked[0]
    assert started and not win.split_btn.isEnabled()


def test_declining_the_question_leaves_the_stems_and_does_not_split(win, tone_wav, tone_flac, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    started = _fake_splitter(monkeypatch)
    folder = _make_track_stems(tmp_path, tone_flac, "htdemucs_6s", ("vocals", "drums"))
    win._load_file(Path(tone_wav))
    _answer_resplit(monkeypatch, QMessageBox.StandardButton.No)
    win._start_split()
    assert not started and win.split_btn.isEnabled()
    assert sorted(p.name for p in folder.iterdir()) == ["drums.flac", "vocals.flac"]


def test_splitting_in_a_mode_with_no_stems_does_not_ask(win, tone_wav, tone_flac, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    started = _fake_splitter(monkeypatch)
    _make_track_stems(tmp_path, tone_flac, "htdemucs", ("vocals", "drums"))   # a 4-stem split only
    win._load_file(Path(tone_wav))
    asked = _answer_resplit(monkeypatch, QMessageBox.StandardButton.No)
    win.split_mode_combo.setCurrentIndex(win.split_mode_combo.findData("6"))
    win._start_split()
    assert not asked and started


def test_failed_split_reenables_the_button(win, tone_wav, monkeypatch, tmp_path):
    from PyQt6.QtCore import QObject, pyqtSignal
    win._load_file(Path(tone_wav))
    win.config["stems_dir"] = str(tmp_path / "stems")

    class FakeSplitter(QObject):
        finished = pyqtSignal(list)
        error = pyqtSignal(str)
        progress = pyqtSignal(int)
        def __init__(self, *a, **k):
            super().__init__()
        def start(self):
            pass

    monkeypatch.setattr("ui.main_window.SplitterThread", FakeSplitter)
    win._start_split()
    win._splitter.progress.emit(30)
    win._splitter.error.emit("Splitter failed: boom")
    assert win.split_btn.isEnabled()
    assert win.split_progress.value() == 0


# --- Split tab: one waveform per stem, shared marker, mixed playback ---------

@pytest.fixture
def stem_files(tone_flac, tmp_path):
    import shutil
    folder = tmp_path / "stems_here"   # its own folder: nothing else in it
    folder.mkdir()
    paths = []
    for name in ("other", "vocals", "guitar"):  # deliberately not in display order
        dest = folder / f"{name}.flac"
        shutil.copy(tone_flac, dest)
        paths.append(str(dest))
    return paths


def test_stem_rows_follow_display_order_and_start_enabled(win, stem_files):
    win.stem_view.set_stems(stem_files)
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["vocals", "guitar", "other"]
    assert win.stem_view.enabled_paths() == win.stem_view.all_paths()
    assert win.stem_view.play_btn.isEnabled()


def test_stem_view_is_disabled_until_there_are_stems(win, stem_files):
    assert not win.stem_view.play_btn.isEnabled()
    win.stem_view.set_stems(stem_files)
    win.stem_view.clear()
    assert not win.stem_view.has_stems() and not win.stem_view.play_btn.isEnabled()


def test_toggle_reports_the_enabled_stems(win, stem_files):
    win.stem_view.set_stems(stem_files)
    seen = []
    win.stem_view.enabled_changed.connect(seen.append)
    win.stem_view._checks[1].setChecked(False)  # guitar
    assert [Path(p).stem for p in seen[-1]] == ["vocals", "other"]


def test_one_marker_is_shared_by_every_row(win, stem_files):
    win.stem_view.set_stems(stem_files)
    seeks = []
    win.stem_view.seek.connect(seeks.append)
    win.stem_view._waves[0].seek_to_ms(1200)  # click in the first row
    assert seeks == [1200]
    assert all(int(w.marker.value()) == 1200 for w in win.stem_view._waves)
    win.stem_view.update_position(2500)       # the player reports a position
    assert all(int(w.marker.value()) == 2500 for w in win.stem_view._waves)


def test_rows_share_the_same_time_axis(win, stem_files):
    win.resize(1000, 700)
    win.show()
    win.centralWidget().setCurrentIndex(1)
    win.stem_view.set_stems(stem_files)
    win.repaint()
    ranges = {tuple(round(v, 3) for v in w.plot_widget.viewRange()[0]) for w in win.stem_view._waves}
    assert len(ranges) == 1, ranges
    assert len({w.plot_widget.getViewBox().width() for w in win.stem_view._waves}) == 1


def test_stem_playback_is_one_ffplay_and_toggles_restart_it(needle, win, stem_files, qtbot):
    from conftest import ffplay_pids
    win.centralWidget().setCurrentIndex(1)
    win.stem_view.set_stems(stem_files)
    win.stem_view.play_btn.click()
    qtbot.waitUntil(lambda: len(ffplay_pids(needle)) == 1, timeout=5000)
    assert win.stem_view.is_playing
    for i in (0, 1, 0, 1):
        win.stem_view._checks[i].toggle()
        assert len(ffplay_pids(needle)) <= 1
        qtbot.wait(120)
    qtbot.wait(400)
    assert len(ffplay_pids(needle)) == 1
    win.stem_view.stop_btn.click()
    qtbot.waitUntil(lambda: ffplay_pids(needle) == [], timeout=5000)
    assert not win.stem_view.is_playing


def test_switching_tabs_stops_playback(needle, win, stem_files, qtbot):
    from conftest import ffplay_pids
    tabs = win.centralWidget()
    tabs.setCurrentIndex(1)
    win.stem_view.set_stems(stem_files)
    win.stem_view.play_btn.click()
    qtbot.waitUntil(lambda: len(ffplay_pids(needle)) == 1, timeout=5000)
    tabs.setCurrentIndex(0)
    qtbot.waitUntil(lambda: ffplay_pids(needle) == [], timeout=5000)
    assert not win.stem_view.is_playing


def test_file_and_stem_playback_never_overlap(win, stem_files, tone_wav, qtbot):
    from conftest import ffplay_pids
    win._load_file(Path(tone_wav))
    win.stem_view.set_stems(stem_files)
    win.centralWidget().setCurrentIndex(1)
    win.stem_view.play_btn.click()
    qtbot.waitUntil(lambda: len(ffplay_pids(str(Path(stem_files[0]).parent / 'vocals.flac'))) == 1, timeout=5000)
    # Back on the Playback tab (stops the stems), then play the loaded file.
    win.centralWidget().setCurrentIndex(0)
    win.play_btn.click()
    qtbot.waitUntil(lambda: len(ffplay_pids(str(tone_wav))) >= 1, timeout=5000)
    qtbot.wait(300)
    assert ffplay_pids(str(Path(stem_files[0]).parent / 'vocals.flac')) == []
    assert len(ffplay_pids(str(tone_wav))) == 1
    win._on_stop_clicked()
    qtbot.waitUntil(lambda: ffplay_pids(str(tone_wav)) == [], timeout=5000)


# --- stem count selector -----------------------------------------------------

def test_split_mode_defaults_to_six_and_old_or_bad_values_fall_back(win):
    assert win.split_mode_combo.currentData() == "6"
    assert win.split_mode_combo.count() == 3


def test_choosing_a_split_mode_is_remembered_and_used(win, tone_wav, monkeypatch, tmp_path):
    from PyQt6.QtCore import QObject, pyqtSignal
    import json
    import config as cfg
    seen = {}

    class FakeSplitter(QObject):
        finished = pyqtSignal(list)
        error = pyqtSignal(str)
        progress = pyqtSignal(int)
        def __init__(self, *a, **k):
            super().__init__()
            seen.update(k)
        def start(self):
            pass

    monkeypatch.setattr("ui.main_window.SplitterThread", FakeSplitter)
    win._load_file(Path(tone_wav))
    win.config["stems_dir"] = str(tmp_path / "stems")
    win.split_mode_combo.setCurrentIndex(win.split_mode_combo.findData("2"))
    assert json.loads(cfg.CONFIG_PATH.read_text())["split_mode"] == "2"
    win._start_split()
    assert seen["mode"] == "2"


def test_split_mode_in_config_is_restored_at_startup(qtbot, monkeypatch):
    import config as cfg
    cfg.CONFIG_PATH.write_text('{"split_mode": "4"}')
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.split_mode_combo.currentData() == "4"
    cfg.CONFIG_PATH.write_text('{"split_mode": "bogus"}')
    w2 = MainWindow()
    qtbot.addWidget(w2)
    assert w2.split_mode_combo.currentData() == "6"


# --- loading stems that are already on disk ----------------------------------

def _make_track_stems(tmp_path, tone_flac, subdir, names, track="tone"):
    """Stems of ``track`` as a split would leave them under tmp_path/stems/<subdir>/<track>."""
    import shutil
    folder = tmp_path / "stems" / subdir / track
    folder.mkdir(parents=True)
    for name in names:
        shutil.copy(tone_flac, folder / f"{name}.flac")
    return folder


def test_loading_a_file_also_loads_its_existing_stems(win, tone_wav, tone_flac, tmp_path, qtbot):
    _make_track_stems(tmp_path, tone_flac, "htdemucs_6s", ("vocals", "drums", "bass"))
    win._load_file(Path(tone_wav))
    settle(win, qtbot)
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["vocals", "drums", "bass"]
    assert "3 stems found" in win.statusBar().currentMessage()


def test_loading_a_file_with_no_stems_leaves_the_split_tab_alone(win, tone_wav, tone_flac, tmp_path, qtbot):
    other = _make_track_stems(tmp_path, tone_flac, "htdemucs_6s", ("vocals",), track="another song")
    win._load_file(Path(tone_wav))
    settle(win, qtbot)
    assert win.stem_view.all_paths() == []
    assert win.statusBar().currentMessage() == "Loaded: tone.wav"
    assert other.is_dir()


def test_existing_stems_of_the_selected_mode_win_over_other_modes(win, tone_wav, tone_flac, tmp_path, qtbot):
    _make_track_stems(tmp_path, tone_flac, "htdemucs_6s", ("vocals", "drums", "bass"))
    _make_track_stems(tmp_path, tone_flac, "two_stem/htdemucs", ("vocals", "no_vocals"))
    win.config["split_mode"] = "2"
    win._load_file(Path(tone_wav))
    settle(win, qtbot)
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["vocals", "no_vocals"]


def test_stems_of_another_mode_are_used_when_the_selected_mode_has_none(win, tone_wav, tone_flac, tmp_path, qtbot):
    _make_track_stems(tmp_path, tone_flac, "htdemucs", ("vocals", "drums", "bass", "other"))
    win.config["split_mode"] = "6"
    win._load_file(Path(tone_wav))
    settle(win, qtbot)
    assert len(win.stem_view.all_paths()) == 4


def test_unreadable_existing_stems_do_not_break_loading_the_file(win, tone_wav, tmp_path, qtbot):
    folder = tmp_path / "stems" / "htdemucs_6s" / "tone"
    folder.mkdir(parents=True)
    (folder / "vocals.flac").write_bytes(b"not audio")
    win._load_file(Path(tone_wav))
    settle(win, qtbot)
    assert win.waveform.has_data()
    assert win.stem_view.all_paths() == []


def _pick_folder(monkeypatch, folder, seen=None):
    """Stand in for the folder dialog: choose ``folder`` ("" = cancel)."""
    def fake(self, start):
        if seen is not None:
            seen["directory"] = start
        return str(folder) if folder else ""
    monkeypatch.setattr(MainWindow, "_choose_stems_folder", fake)


def test_load_stems_shows_every_audio_file_in_the_folder(win, stem_files, monkeypatch, qtbot):
    folder = Path(stem_files[0]).parent
    (folder / "notes.txt").write_text("not audio")
    _pick_folder(monkeypatch, folder)
    win.load_stems_btn.click()
    settle(win, qtbot)
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["vocals", "guitar", "other"]
    assert win.stem_view.play_btn.isEnabled()


def test_a_disabled_stem_is_drawn_faint_and_comes_back_when_re_enabled(win, stem_files):
    win.stem_view.set_stems(stem_files)
    waves = win.stem_view._waves
    assert not any(w.is_dimmed() for w in waves)
    win.stem_view._checks[1].setChecked(False)
    assert [w.is_dimmed() for w in waves] == [False, True, False]
    win.stem_view._checks[1].setChecked(True)
    assert not any(w.is_dimmed() for w in waves)


def test_new_stems_start_undimmed_after_others_were_switched_off(win, stem_files):
    win.stem_view.set_stems(stem_files)
    win.stem_view._checks[0].setChecked(False)
    win.stem_view.set_stems(stem_files)
    assert not any(w.is_dimmed() for w in win.stem_view._waves)


def test_cancelling_the_folder_dialog_changes_nothing(win, stem_files, monkeypatch):
    win.stem_view.set_stems(stem_files)
    before = win.stem_view.all_paths()
    _pick_folder(monkeypatch, "")
    win.load_stems_btn.click()
    assert win.stem_view.all_paths() == before


def test_folder_without_audio_is_reported_and_keeps_the_current_stems(win, stem_files, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    win.stem_view.set_stems(stem_files)
    before = win.stem_view.all_paths()
    empty = tmp_path / "empty"
    empty.mkdir()
    _pick_folder(monkeypatch, empty)
    win.load_stems_btn.click()
    assert shown == ["No stems found"]
    assert win.stem_view.all_paths() == before


def test_unreadable_audio_is_skipped_not_fatal(win, stem_files, monkeypatch, qtbot):
    folder = Path(stem_files[0]).parent
    (folder / "broken.flac").write_bytes(b"this is not audio")
    _pick_folder(monkeypatch, folder)
    win.load_stems_btn.click()
    settle(win, qtbot)
    assert "broken" not in [Path(p).stem for p in win.stem_view.all_paths()]
    assert len(win.stem_view.all_paths()) == 3
    assert "1 unreadable" in win.statusBar().currentMessage()


def test_dialog_starts_in_the_stems_folder(win, stem_files, monkeypatch, tmp_path):
    seen = {}
    win.config["stems_dir"] = str(tmp_path)
    _pick_folder(monkeypatch, "", seen)
    win.load_stems_btn.click()
    assert seen["directory"] == str(tmp_path)


def test_loading_other_stems_replaces_the_stem_player(win, stem_files, monkeypatch, qtbot, needle):
    from conftest import ffplay_pids
    win.centralWidget().setCurrentIndex(1)
    win.stem_view.set_stems(stem_files)
    win.stem_view.play_btn.click()
    qtbot.waitUntil(lambda: len(ffplay_pids(needle)) == 1, timeout=5000)
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    assert win._player is None  # the old player (and its ffplay) is gone
    qtbot.waitUntil(lambda: ffplay_pids(needle) == [], timeout=5000)
    assert not win.stem_view.is_playing


def test_folder_dialog_asks_for_a_folder_and_has_a_sensible_size(win, tmp_path):
    from PyQt6.QtWidgets import QFileDialog
    dlg = win._make_folder_dialog(str(tmp_path))
    assert dlg.fileMode() == QFileDialog.FileMode.Directory
    assert dlg.testOption(QFileDialog.Option.DontUseNativeDialog)  # so its size and wording are ours
    assert not dlg.testOption(QFileDialog.Option.ShowDirsOnly)      # stem files stay visible
    assert dlg.labelText(QFileDialog.DialogLabel.Accept) == "Select Folder"
    assert dlg.labelText(QFileDialog.DialogLabel.FileName) == "Folder:"
    assert "stems" in dlg.windowTitle()


def test_folder_dialog_is_never_wider_than_the_app_window(win, tmp_path):
    sizes = {}
    for width, height in ((700, 600), (1500, 900), (300, 200)):
        win.resize(width, height)
        dlg = win._make_folder_dialog(str(tmp_path))
        sizes[(width, height)] = (dlg.width(), dlg.height())
    assert sizes[(700, 600)] == (700, 560)     # follows a small window
    assert sizes[(1500, 900)] == (900, 560)    # capped on a big one
    assert sizes[(300, 200)] == (480, 380)     # but never unusably small


# --- Load from File uses the same kind of dialog -----------------------------

def _pick_file(monkeypatch, path, seen=None):
    """Stand in for the file dialog: choose ``path`` ("" = cancel)."""
    def fake(self, start):
        if seen is not None:
            seen["directory"] = start
        return str(path) if path else ""
    monkeypatch.setattr(MainWindow, "_choose_audio_file", fake)


def test_load_button_opens_the_chosen_file(win, tone_wav, monkeypatch):
    _pick_file(monkeypatch, tone_wav)
    win.load_btn.click()
    assert win._current_file == Path(tone_wav)
    assert win.waveform.has_data()


def test_cancelling_the_file_dialog_loads_nothing(win, monkeypatch):
    _pick_file(monkeypatch, "")
    win.load_btn.click()
    assert win._current_file is None


def test_file_dialog_starts_in_the_recordings_folder(win, monkeypatch, tmp_path):
    seen = {}
    win.config["recordings_dir"] = str(tmp_path)
    _pick_file(monkeypatch, "", seen)
    win.load_btn.click()
    assert seen["directory"] == str(tmp_path)
    win.config["recordings_dir"] = str(tmp_path / "does-not-exist")
    win.load_btn.click()
    assert seen["directory"] == ""


def test_file_dialog_is_a_file_picker_sized_to_the_window(win, tmp_path):
    from PyQt6.QtWidgets import QFileDialog
    win.resize(700, 600)
    dlg = win._make_file_dialog(str(tmp_path))
    assert dlg.fileMode() == QFileDialog.FileMode.ExistingFile
    assert dlg.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert dlg.labelText(QFileDialog.DialogLabel.Accept) == "Open"
    assert (dlg.width(), dlg.height()) == (700, 560)
    win.resize(300, 200)
    small = win._make_file_dialog(str(tmp_path))
    assert (small.width(), small.height()) == (480, 380)
    win.resize(1600, 1000)
    big = win._make_file_dialog(str(tmp_path))
    assert (big.width(), big.height()) == (900, 560)


def test_both_dialogs_filter_the_same_audio_types(win, tmp_path):
    files = win._make_file_dialog(str(tmp_path)).nameFilters()
    folders = win._make_folder_dialog(str(tmp_path)).nameFilters()
    assert files == folders
    assert all(ext in files[0] for ext in ("*.flac", "*.wav", "*.mp3", "*.ogg"))


# --- the Playback tab and opening a file at startup --------------------------

def test_tabs_are_playback_and_split(win):
    tabs = win.centralWidget()
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Playback", "Split"]


def test_file_given_at_startup_is_loaded(qtbot, tone_wav):
    w = MainWindow(initial_file=str(tone_wav))
    qtbot.addWidget(w)
    assert w._current_file == Path(tone_wav)
    assert w.file_edit_play.text() == str(tone_wav)
    assert w.waveform.has_data() and w.play_btn.isEnabled()
    if w._player is not None:
        w._player.shutdown()


def test_missing_startup_file_is_reported_not_fatal(qtbot, tmp_path):
    w = MainWindow(initial_file=str(tmp_path / "nope.flac"))
    qtbot.addWidget(w)
    assert w._current_file is None
    assert "File not found" in w.statusBar().currentMessage()


def test_no_startup_file_leaves_the_window_empty(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert w._current_file is None and not w.play_btn.isEnabled()


# --- loading stems in the background: busy pointer, lock, timeout ------------

@pytest.fixture
def slow_loader(monkeypatch):
    """Make every stem load wait for ``gate`` (or for its own cancel) before it starts reading."""
    import threading
    import time
    from core.stemload import StemLoadThread
    gate = threading.Event()
    real_run = StemLoadThread.run

    def slow_run(self):
        while not gate.is_set() and not self._cancelled:
            time.sleep(0.005)
        real_run(self)

    monkeypatch.setattr(StemLoadThread, "run", slow_run)
    yield gate
    gate.set()


def _wait_cursor():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    cur = QApplication.overrideCursor()
    return cur is not None and cur.shape() == Qt.CursorShape.WaitCursor


def _no_override_cursor():
    from PyQt6.QtWidgets import QApplication
    return QApplication.overrideCursor() is None


def test_a_slow_load_shows_a_busy_pointer_then_locks_the_window(win, stem_files, slow_loader, monkeypatch, qtbot):
    import ui.main_window as mw
    monkeypatch.setattr(mw, "STEM_LOCK_DELAY_MS", 30)
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    assert win.stems_loading() and _wait_cursor()           # at once ...
    assert win.centralWidget().isEnabled()                   # ... but not locked yet
    qtbot.waitUntil(lambda: not win.centralWidget().isEnabled(), timeout=3000)
    assert not win._toolbar.isEnabled()
    slow_loader.set()
    settle(win, qtbot)
    assert _no_override_cursor()
    assert win.centralWidget().isEnabled() and win._toolbar.isEnabled()
    assert len(win.stem_view.all_paths()) == 3


def test_a_quick_load_never_locks_the_window(win, stem_files, monkeypatch, qtbot):
    locked = []
    monkeypatch.setattr(MainWindow, "_lock_window", lambda self: locked.append(True))
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    settle(win, qtbot)
    assert len(win.stem_view.all_paths()) == 3
    assert not locked and _no_override_cursor() and win.centralWidget().isEnabled()


def test_a_load_that_takes_too_long_is_given_up_with_an_explanation(win, stem_files, slow_loader, monkeypatch, qtbot):
    from PyQt6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: shown.append((title, text)))
    win.stem_view.set_stems(stem_files[:1])               # what is showing must survive the timeout
    before = win.stem_view.all_paths()
    win.config["stem_load_timeout_s"] = 0.05
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)
    (title, text), = shown
    assert "timed out" in title
    assert "0.05 seconds" in text and "Stem loading timeout" in text and "Settings" in text
    assert "stem_load_timeout_s" in text
    assert not win.stems_loading() and _no_override_cursor() and win.centralWidget().isEnabled()
    slow_loader.set()                                       # the stopped load must not appear late
    qtbot.waitUntil(lambda: not win._stale_loaders, timeout=5000)
    assert win.stem_view.all_paths() == before


def test_a_newer_load_replaces_one_still_running(win, stem_files, tone_flac, slow_loader, monkeypatch, qtbot, tmp_path):
    import shutil
    other = tmp_path / "other_stems"
    other.mkdir()
    shutil.copy(tone_flac, other / "bass.flac")
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    first = win._stem_loader
    _pick_folder(monkeypatch, other)
    win.load_stems_btn.click()
    assert win._stem_loader is not first
    slow_loader.set()
    settle(win, qtbot)
    qtbot.waitUntil(lambda: not win._stale_loaders, timeout=5000)
    assert [Path(p).stem for p in win.stem_view.all_paths()] == ["bass"]
    assert _no_override_cursor()


def test_closing_the_window_during_a_load_stops_it_cleanly(win, stem_files, slow_loader, monkeypatch, qtbot):
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    loader = win._stem_loader
    win.close()
    assert not loader.isRunning() and not win.stems_loading()
    assert _no_override_cursor()


def test_the_timeout_is_a_setting_with_a_default(win):
    import config as cfg
    from ui.main_window import SettingsDialog
    from core.stemload import DEFAULT_TIMEOUT_S
    assert cfg.DEFAULT_CONFIG["stem_load_timeout_s"] == DEFAULT_TIMEOUT_S
    dlg = SettingsDialog(win.config)
    assert dlg.timeout_spin.value() == int(DEFAULT_TIMEOUT_S)
    dlg.timeout_spin.setValue(120)
    dlg.accept()
    assert win.config["stem_load_timeout_s"] == 120
    assert '"stem_load_timeout_s": 120' in cfg.CONFIG_PATH.read_text()


def test_a_nonsense_timeout_in_the_config_falls_back_to_the_default(win, stem_files, monkeypatch, qtbot):
    from core.stemload import DEFAULT_TIMEOUT_S
    win.config["stem_load_timeout_s"] = "soon"
    seen = []
    monkeypatch.setattr(win._timeout_timer, "start", lambda ms: seen.append(ms))
    _pick_folder(monkeypatch, Path(stem_files[0]).parent)
    win.load_stems_btn.click()
    settle(win, qtbot)
    assert seen == [int(DEFAULT_TIMEOUT_S * 1000)]
