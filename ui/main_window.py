# ui/main_window.py
import logging
import sys
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QProgressBar, QLabel, QFileDialog,
    QMessageBox, QTabWidget, QDialog,
    QLineEdit, QScrollArea, QToolButton, QSizePolicy,
)
from PyQt6.QtGui import QCursor, QIcon
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QSize
from core.splitter import MODES, SplitterThread, existing_stems, normalize_mode, stems_in
from core.stemload import StemLoadThread, normalize_timeout
from core.player import PlayerThread, normalize_volume
from ui.settings_view import SettingsView
from ui.stem_view import AUDIO_EXTENSIONS, StemView
from ui.volume_control import VolumeControl
from ui.waveform_view import WaveformWidget
from config import Config
from core.logger import setup_logging

log = logging.getLogger(__name__)

# File dialogs list these; built from the same set the stems loader accepts.
AUDIO_FILTER = "Audio files (" + " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS)) + ")"


# While stems load in the background the pointer is a busy one at once; the window only locks
# (and greys out) if the load is still going after this long, so quick loads do not flash.
STEM_LOCK_DELAY_MS = 250
# A moving volume slider changes ffplay's level (a restart of the one process) this long after
# it stops, not on every step.
VOLUME_APPLY_DELAY_MS = 150


class MainWindow(QMainWindow):
    def __init__(self, initial_file=None):
        """``initial_file``: an audio file to open at startup (e.g. from the command line)."""
        super().__init__()
        self.setWindowTitle("Audio Splitter")
        self.resize(800, 600)

        self.config = Config()
        setup_logging(self.config["logging"])
        self._volume = normalize_volume(self.config["volume"])
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.timeout.connect(self._apply_volume)

        self._setup_tabs()

        self._current_file = None
        self._player = None
        self._player_is_stems = False  # the one player serves either the loaded file or the stems
        self._splitter = None
        # Stems are read on a worker thread (see _show_stems). Loaders that were given up on stay
        # referenced until they finish: a QThread must not be destroyed while it runs.
        self._loader = None
        self._loader_then = None
        self._loader_what = ""
        self._stale_loaders = set()
        self._busy_cursor = False
        self._locked = False
        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.timeout.connect(self._lock_window)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._load_timed_out)

        if initial_file:
            self._open_initial_file(Path(initial_file))

    def _open_initial_file(self, path: Path):
        if path.is_file():
            self._load_file(path)
        else:
            log.warning("File given on the command line not found: %s", path)
            self.statusBar().showMessage(f"File not found: {path}", 8000)

    @staticmethod
    def _transport_button(text, theme_icon, tooltip):
        """An icon-only transport button (text fallback if the theme icon is missing)."""
        btn = QToolButton()
        icon = QIcon.fromTheme(theme_icon)
        if icon.isNull():
            btn.setText(text)
        else:
            btn.setIcon(icon)
        btn.setIconSize(QSize(24, 24))
        btn.setToolTip(tooltip)
        btn.setCheckable(False)
        btn.setAutoRaise(False)
        btn.setEnabled(False)  # disabled until a file is loaded
        btn.setStyleSheet("QToolButton{border:1px solid gray; border-radius:2px;}")
        return btn

    def _setup_tabs(self):
        tabs = QTabWidget(self)
        self.setCentralWidget(tabs)

        # Playback tab: load a file, see its waveform, listen to it.
        play_widget = QWidget()
        play_layout = QVBoxLayout(play_widget)

        # The file we are working on: shown (read-only) in front of the button,
        # with a duplicate on the Split tab.
        self.load_btn = QPushButton("Load from File")
        self.load_btn.clicked.connect(self._load_file_dialog)
        self.file_edit_play = self._make_file_edit()
        play_layout.addLayout(self._file_row(self.file_edit_play, self.load_btn))

        self.waveform = WaveformWidget()
        # The widget scales its content, so a fixed height is fine.
        self.waveform.setFixedHeight(200)
        play_layout.addWidget(self.waveform)

        # The scrubber is integrated into the waveform widget: its ``seek``
        # signal restarts playback from the requested position.
        self.waveform.seek.connect(self._handle_seek)

        # Transport buttons, left to right: beginning, play, stop, end.
        self.skip_back_btn = self._transport_button("⏮", "media-skip-backward", "Go to beginning")
        self.play_btn = self._transport_button("▶", "media-playback-start", "Play/Pause")
        self.stop_btn = self._transport_button("■", "media-playback-stop", "Stop")
        self.skip_fwd_btn = self._transport_button("⏭", "media-skip-forward", "Go to end")
        self.skip_back_btn.clicked.connect(self._on_skip_backward)
        self.play_btn.clicked.connect(self._on_play_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.skip_fwd_btn.clicked.connect(self._on_skip_forward)
        self.is_playing = False
        self.current_position_ms = 0
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)
        button_layout.setContentsMargins(0, 0, 0, 0)
        for b in (self.skip_back_btn, self.play_btn, self.stop_btn, self.skip_fwd_btn):
            button_layout.addWidget(b)
        button_layout.addStretch(1)
        self.volume_control = VolumeControl()
        button_layout.addWidget(self.volume_control)
        play_layout.addLayout(button_layout)
        # Keep the layout anchored to the top.
        play_layout.addStretch(1)

        # The tab goes in a scroll area so overflowing contents can be scrolled.
        play_scroll = QScrollArea()
        play_scroll.setWidgetResizable(True)
        play_scroll.setWidget(play_widget)
        tabs.addTab(play_scroll, "Playback")

        # The split tab also gets a scroll area for consistency.
        split_widget = QWidget()
        split_layout = QVBoxLayout(split_widget)

        # How many stems to make: 6 (default), 4, or 2 (vocals + everything else).
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Stems:"))
        self.split_mode_combo = QComboBox()
        self.split_mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.split_mode_combo.setMinimumContentsLength(10)
        for key, (label, *_rest) in MODES.items():
            self.split_mode_combo.addItem(label, key)
        self.split_mode_combo.setCurrentIndex(
            self.split_mode_combo.findData(normalize_mode(self.config["split_mode"])))
        self.split_mode_combo.currentIndexChanged.connect(self._split_mode_changed)
        mode_row.addWidget(self.split_mode_combo, 1)
        # Open stems that are already on disk (from an earlier split or another tool).
        self.load_stems_btn = QPushButton("Load Stems…")
        self.load_stems_btn.setToolTip("Show the stems from a folder (for example an earlier split)")
        self.load_stems_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.load_stems_btn.clicked.connect(self._load_stems_dialog)
        mode_row.addWidget(self.load_stems_btn, 0)
        split_layout.addLayout(mode_row)

        self.split_btn = QPushButton("Split")
        self.split_btn.clicked.connect(self._start_split)
        self.file_edit_split = self._make_file_edit()
        split_layout.addLayout(self._file_row(self.file_edit_split, self.split_btn))

        self.split_progress = QProgressBar()
        self.split_progress.setRange(0, 100)
        split_layout.addWidget(self.split_progress)

        # One waveform per stem with a toggle each, plus transport buttons.
        self.stem_view = StemView()
        split_layout.addWidget(self.stem_view)
        self.stem_view.seek.connect(self._stem_seek)
        self.stem_view.enabled_changed.connect(self._stems_toggled)
        self.stem_view.play_clicked.connect(self._on_stem_play_clicked)
        self.stem_view.stop_clicked.connect(self._on_stem_stop_clicked)
        self.stem_view.skip_back_clicked.connect(self._on_stem_skip_backward)
        self.stem_view.skip_forward_clicked.connect(self._on_stem_skip_forward)
        # One volume for both tabs (there is one player); each control shows the same level.
        for control in (self.volume_control, self.stem_view.volume_control):
            control.set_volume(self._volume)
            control.changed.connect(self._volume_moved)
        split_layout.addStretch(1)

        split_scroll = QScrollArea()
        split_scroll.setWidgetResizable(True)
        split_scroll.setWidget(split_widget)
        tabs.addTab(split_scroll, "Split")

        # Settings tab: edited in place, applied by its Save button.
        self.settings_view = SettingsView(self.config)
        self.settings_view.saved.connect(self._settings_saved)
        tabs.addTab(self.settings_view, "Settings")
        self._settings_tab = tabs.count() - 1
        self._shown_tab = 0       # the tab being shown; leaving Settings may ask first
        self._last_play_tab = 0
        # Only one of Playback and Split plays at a time: switching between them stops playback.
        tabs.currentChanged.connect(self._tab_changed)

    @staticmethod
    def _make_file_edit():
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText("No file loaded")
        return edit

    @staticmethod
    def _file_row(edit, button):
        """Path box taking all the spare width, then a button only as wide as its text."""
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(button, 0)
        return row

    def _show_current_file(self):
        text = str(self._current_file) if self._current_file else ""
        for edit in (self.file_edit_play, self.file_edit_split):
            edit.setText(text)
            edit.setToolTip(text)
            edit.setCursorPosition(len(text))  # show the end: the file name matters most

    def _make_file_dialog(self, start: str):
        """Qt's own file picker (the system one cannot be sized or relabelled)."""
        dlg = QFileDialog(self, "Open an audio file", start)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dlg.setNameFilter(AUDIO_FILTER)
        dlg.setLabelText(QFileDialog.DialogLabel.Accept, "Open")
        self._size_dialog(dlg)
        return dlg

    def _choose_audio_file(self, start: str) -> str:
        """Ask the user for an audio file; returns "" if they cancel."""
        dlg = self._make_file_dialog(start)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            return dlg.selectedFiles()[0]
        return ""

    @pyqtSlot()
    def _load_file_dialog(self):
        start = Path(self.config["recordings_dir"])
        path = self._choose_audio_file(str(start) if start.is_dir() else "")
        if path:
            self._load_file(Path(path))

    def _load_file(self, path: Path):
        """Load an audio file and display its waveform (read on a worker thread)."""
        log.info("Loading file: %s", path)
        self._start_load([path], lambda result: self._file_loaded(path, result), "the file")

    def _file_loaded(self, path: Path, result):
        """The background read of ``path`` is over: show it, or say it could not be read."""
        self.waveform.clear()
        if result:
            _, time_points, amplitude_points = result[0]
            self.waveform.set_waveform_data(time_points, amplitude_points)
            self._current_file = path
            self._show_current_file()
            # Drop any player bound to the previous file; it is recreated
            # lazily by _ensure_player().
            self._drop_player()
            self.is_playing = False
            self.stem_view.set_playing(False)
            self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
            log.info("Loaded %s (%.1fs)", path.name, self.waveform.duration_ms() / 1000)
            self.statusBar().showMessage(f"Loaded: {path.name}")
            self._load_existing_stems(path)
        else:
            log.error("Failed to load audio file %s", path)
            QMessageBox.critical(self, "Error", f"Failed to load audio file: {path.name}")

        # Enable playback controls once a file is loaded via dialog
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.skip_back_btn.setEnabled(True)
        self.skip_fwd_btn.setEnabled(True)

    def _load_existing_stems(self, path: Path):
        """Put the stems of an earlier split of ``path`` on the Split tab, if there are any."""
        try:
            files = existing_stems(self.config["stems_dir"], path, self.config["split_mode"])
            if not files:
                return

            def shown(loaded):
                if loaded:
                    log.info("Found %d existing stems for %s", loaded, path.name)
                    self.statusBar().showMessage(f"Loaded: {path.name} ({loaded} stems found)")
            self._show_stems(files, then=shown)
        except Exception:
            log.exception("Could not load existing stems for %s", path)

    def _ensure_player(self):
        """Return the single persistent player, (re)created for the file.

        The player thread is started once and lives until the file changes
        or the window closes; playback control never recreates it.
        """
        if self._player is not None and (
                self._player_is_stems or self._player.file_path != str(self._current_file)):
            self._drop_player()
        if self._player is None:
            self._player = PlayerThread(str(self._current_file), volume=self._volume)
            self._player_is_stems = False
            self._player.position_ms.connect(self.waveform.update_position)
            self._player.finished.connect(self._playback_finished)
            self._player.error.connect(self._handle_error)
            self._player.start()
        return self._player

    def _drop_player(self):
        """Shut the current player down (there is only ever one)."""
        if self._player is not None:
            self._player.shutdown()
            self._player = None
        self._player_is_stems = False

    # ---------------------------------------------------------------------
    # Split tab: playing the enabled stems together
    # ---------------------------------------------------------------------
    def _ensure_stem_player(self):
        """The single player, as a stem mixer for the stems on the Split tab.

        Same rule as for files: there is never a second player, so switching
        between a file and stems replaces the player instead of adding one.
        """
        if self._player is not None and not self._player_is_stems:
            self._drop_player()
        if self._player is None:
            stems = self.stem_view.all_paths()
            self._player = PlayerThread(stems, volume=self._volume)
            self._player_is_stems = True
            self._player.set_sources(self.stem_view.enabled_paths())
            self._player.position_ms.connect(self.stem_view.update_position)
            self._player.finished.connect(self._stem_playback_finished)
            self._player.error.connect(self._handle_error)
            self._player.start()
        return self._player

    @pyqtSlot()
    def _on_stem_play_clicked(self):
        if not self.stem_view.has_stems():
            return
        player = self._ensure_stem_player()
        if self.stem_view.is_playing:
            player.pause()
            self.stem_view.set_playing(False)
            self.statusBar().showMessage("Paused", 3000)
        else:
            if player.is_paused():
                player.resume()
            else:
                player.play(self.stem_view.marker_ms())
            self.stem_view.set_playing(True)
            self.statusBar().showMessage("Playing stems...")

    @pyqtSlot()
    def _on_stem_stop_clicked(self):
        if self._player is not None and self._player_is_stems:
            self._player.stop()
        self.stem_view.update_position(0)
        self.stem_view.set_playing(False)
        self.statusBar().showMessage("Stopped", 3000)

    @pyqtSlot()
    def _on_stem_skip_backward(self):
        if self.stem_view.has_stems():
            self._ensure_stem_player().seek(0)

    @pyqtSlot()
    def _on_stem_skip_forward(self):
        if self.stem_view.has_stems():
            player = self._ensure_stem_player()
            player.seek(self.stem_view.duration_ms() or player.duration)

    @pyqtSlot(int)
    def _stem_seek(self, ms):
        if self.stem_view.has_stems():
            self._ensure_stem_player().seek(ms)

    @pyqtSlot(list)
    def _stems_toggled(self, enabled_paths):
        # Takes effect at once: the one ffplay restarts from the current
        # position with the new mix (only while playing).
        if self._player is not None and self._player_is_stems:
            self._player.set_sources(enabled_paths)

    @pyqtSlot()
    def _stem_playback_finished(self):
        self.stem_view.set_playing(False)
        self.statusBar().showMessage("Playback finished", 3000)

    @pyqtSlot(int)
    def _tab_changed(self, index):
        """Stop playback when switching between Playback and Split, so they never play together.

        A visit to Settings leaves playback alone (what counts is the last of the two playing tabs
        that was shown). Leaving Settings with unsaved edits asks first: Save, Discard or Cancel.
        """
        previous, self._shown_tab = self._shown_tab, index
        if previous == self._settings_tab and not self._leave_settings():
            tabs = self.centralWidget()          # stay: put the tab back without another event
            tabs.blockSignals(True)
            tabs.setCurrentIndex(previous)
            tabs.blockSignals(False)
            self._shown_tab = previous
            return
        if index == self._settings_tab:
            self.settings_view.load_from_config()
            return
        switched = index != self._last_play_tab
        self._last_play_tab = index
        if not switched or self._player is None:
            return
        self._player.stop()
        self.is_playing = False
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        self.stem_view.set_playing(False)
        self.statusBar().showMessage("Stopped", 3000)  # not "Playing..." any more

    def _leave_settings(self) -> bool:
        """True when it is fine to leave the Settings tab: nothing unsaved, or the user chose
        Save or Discard. False means stay."""
        if not self.settings_view.has_unsaved_changes():
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, "Unsaved settings",
            "You changed settings without saving them.\n\nSave them before leaving?",
            buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
        if answer == buttons.Save:
            self.settings_view.save()
            return True
        return answer == buttons.Discard

    @pyqtSlot(int)
    def _volume_moved(self, value):
        self._volume = value
        for control in (self.volume_control, self.stem_view.volume_control):
            if control.volume() != value:
                control.set_volume(value)
        self._volume_timer.start(VOLUME_APPLY_DELAY_MS)

    @pyqtSlot()
    def _apply_volume(self):
        """The slider has settled: remember the level and give it to the player."""
        self._save_volume()
        if self._player is not None:
            self._player.set_volume(self._volume)

    def _save_volume(self):
        self.config["volume"] = self._volume
        self.config.save()

    @pyqtSlot()
    def _settings_saved(self):
        setup_logging(self.config["logging"])
        self.statusBar().showMessage("Settings saved", 3000)

    @pyqtSlot()
    def _on_play_clicked(self):
        if not self._current_file:
            QMessageBox.warning(self, "No file", "Load a file first.")
            return

        player = self._ensure_player()
        if self.is_playing:
            # Pause playback; the playhead stays frozen for resume().
            player.pause()
            self.is_playing = False
            self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
            self.statusBar().showMessage("Paused", 3000)
        else:
            # Resume a pause from its frozen position, otherwise start from
            # the marker.
            if player.is_paused():
                player.resume()
            else:
                player.play(int(self.waveform.marker.value()))
            self.is_playing = True
            self.play_btn.setIcon(QIcon.fromTheme("media-playback-pause"))
            self.statusBar().showMessage("Playing...")

    @pyqtSlot()
    def _on_stop_clicked(self):
        if not self._current_file:
            return

        # Stop any ongoing playback (non-blocking; the thread stays alive).
        if self._player:
            self._player.stop()

        # Reset to beginning position
        self.waveform.marker.setPos(0)

        # Update UI state
        self.is_playing = False
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        self.statusBar().showMessage("Stopped", 3000)

    @pyqtSlot()
    def _on_skip_backward(self):
        """Jump to the beginning (marker to 0; restarts audio if playing)."""
        if not self._current_file:
            return
        self._ensure_player().seek(0)

    @pyqtSlot()
    def _on_skip_forward(self):
        """Jump to the end (marker to end; finishes playback if playing)."""
        if not self._current_file:
            return
        player = self._ensure_player()
        # The waveform knows the true length of the loaded audio. The
        # player's ffprobe-based duration can fall back to a bogus 10M ms
        # when probing fails, which used to fling the marker far past the
        # actual end.
        end_ms = self.waveform.duration_ms() or player.duration
        player.seek(end_ms)

    # ---------------------------------------------------------------------
    # Scrubber integration
    # ---------------------------------------------------------------------
    @pyqtSlot(int)
    def _handle_seek(self, ms: int):
        """Handle a seek request emitted from the waveform widget.

        The waveform emits ``seek`` when the user drags the integrated
        marker.  :meth:`PlayerThread.seek` restarts audio only if it is
        currently playing; while paused or stopped it just moves the
        marker, so a paused drag never autoplays.
        """
        if not self._current_file:
            return
        self._ensure_player().seek(ms)

    # ---------------------------------------------------------------------
    # Error handling
    # ---------------------------------------------------------------------
    @pyqtSlot(str)
    def _handle_error(self, message: str):
        """Display an error message received from a worker thread.

        Args:
            message: The error text emitted by :class:`PlayerThread` or
                :class:`SplitterThread`.
        """
        # Never show an empty dialog.
        try:
            if message is None:
                log.debug("_handle_error: ignoring None message")
                return
            if not isinstance(message, str):
                message = str(message)
            if not message or not message.strip():
                log.debug("_handle_error: ignoring empty message")
                return
        except Exception as e:
            log.warning("_handle_error: validation failed: %s", e)
            return
        log.error("Worker thread error: %s", message)
        QMessageBox.critical(self, "Error", message)

    @pyqtSlot()
    def _playback_finished(self):
        self.is_playing = False
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        self.statusBar().showMessage("Playback finished", 3000)

    @pyqtSlot()
    def _start_split(self):
        if not self._current_file:
            QMessageBox.warning(self, "No file", "Load a file first.")
            return
        stems_dir = Path(self.config["stems_dir"])
        mode = self.split_mode_combo.currentData()
        old = stems_in(stems_dir, self._current_file, mode)
        if old and not self._confirm_resplit(len(old)):
            return
        stems_dir.mkdir(parents=True, exist_ok=True)
        self._splitter = SplitterThread(self._current_file, stems_dir, mode=mode)
        self._splitter.progress.connect(self.split_progress.setValue)
        self._splitter.finished.connect(self._split_finished)
        self._splitter.error.connect(self._split_failed)
        log.info("Split started: %s -> %s", self._current_file, stems_dir)
        self.split_progress.setValue(0)
        self.split_btn.setEnabled(False)  # one split at a time
        self._splitter.start()
        self.statusBar().showMessage("Splitting...")

    def _confirm_resplit(self, count: int) -> bool:
        """Splitting again writes into the same folder, replacing the stems already there."""
        answer = QMessageBox.question(
            self, "Split again?",
            f"{self._current_file.name} has already been split into {count} stems. "
            "Splitting again replaces them.\n\nSplit again?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    @pyqtSlot(int)
    def _split_mode_changed(self, _index):
        self.config["split_mode"] = self.split_mode_combo.currentData()
        self.config.save()

    @pyqtSlot(str)
    def _split_failed(self, message):
        self.split_btn.setEnabled(True)
        self.split_progress.setValue(0)
        self._handle_error(message)

    @pyqtSlot(list)
    def _split_finished(self, files):
        log.info("Split finished: %d files", len(files))
        self.split_btn.setEnabled(True)
        self._show_stems(files)
        self.statusBar().showMessage(f"Split finished: {len(files)} files", 3000)

    def loading(self) -> bool:
        return self._loader is not None

    def _show_stems(self, files, then=None):
        """Put these stem files on the Split tab (replacing any earlier ones).

        ``then(count)`` runs once the rows are showing (not after a timeout or when another load
        replaces this one). Reading happens in the background, see ``_start_load``.
        """
        if self._player is not None and self._player_is_stems:
            self._drop_player()  # its stems are being replaced
            self.stem_view.set_playing(False)

        def show(result):
            self.stem_view.set_loaded(result)
            if then is not None:
                then(len(self.stem_view.all_paths()))
        self._start_load(files, show, "the stems")

    def _start_load(self, files, done, what):
        """Read audio files on a worker thread and call ``done(result)`` with what was readable.

        This can take a few seconds for long songs. Until it finishes the pointer is a busy one,
        the window locks if it takes more than STEM_LOCK_DELAY_MS, and after the
        ``stem_load_timeout_s`` setting the wait is given up with an explanation (``what`` names
        what was being loaded). A newer load replaces one still running.
        """
        self._abandon_load()
        loader = StemLoadThread([str(f) for f in files])
        loader.loaded.connect(self._load_finished)
        loader.finished.connect(self._loader_ended)
        self._loader = loader
        self._loader_then = done
        self._loader_what = what
        self._begin_busy()
        loader.start()

    @pyqtSlot(list)
    def _load_finished(self, result):
        if self.sender() is not self._loader:
            return  # a load that was given up on
        loader, then = self._loader, self._loader_then
        self._loader = self._loader_then = None
        self._stale_loaders.add(loader)  # still winding down; released in _loader_ended
        self._end_busy()
        then(result)

    @pyqtSlot()
    def _loader_ended(self):
        self._stale_loaders.discard(self.sender())

    def _abandon_load(self):
        """Stop waiting for the stems being loaded, if any; their result is dropped."""
        loader = self._loader
        if loader is None:
            return
        loader.loaded.disconnect(self._load_finished)
        loader.cancel()
        self._stale_loaders.add(loader)
        self._loader = self._loader_then = None
        self._end_busy()

    def _begin_busy(self):
        if not self._busy_cursor:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            self._busy_cursor = True
        self._lock_timer.start(STEM_LOCK_DELAY_MS)
        seconds = normalize_timeout(self.config["stem_load_timeout_s"])
        self._timeout_timer.start(max(1, int(seconds * 1000)))

    def _end_busy(self):
        self._lock_timer.stop()
        self._timeout_timer.stop()
        if self._locked:
            self.centralWidget().setEnabled(True)
            self._locked = False
        if self._busy_cursor:
            QApplication.restoreOverrideCursor()
            self._busy_cursor = False

    @pyqtSlot()
    def _lock_window(self):
        """Nothing can be clicked while the stems load (the title bar still closes the window)."""
        if self._loader is not None and not self._locked:
            self.centralWidget().setEnabled(False)
            self._locked = True

    @pyqtSlot()
    def _load_timed_out(self):
        if self._loader is None:
            return
        seconds = normalize_timeout(self.config["stem_load_timeout_s"])
        what = self._loader_what
        log.warning("Loading %s took longer than %g s; gave up", what, seconds)
        self._abandon_load()
        self.statusBar().showMessage(f"Loading {what} timed out", 8000)
        QMessageBox.warning(
            self, f"Loading {what} timed out",
            f"Loading {what} took longer than {seconds:g} seconds, so it was stopped.\n\n"
            "If your songs are long or the disk is slow, raise \"Loading timeout\" in "
            "Settings (the key is stem_load_timeout_s in config.toml) and load them again.")

    def _make_folder_dialog(self, start: str):
        """Qt's own folder picker, so we control its wording and size.

        The system dialog (used by getExistingDirectory) cannot be sized or
        relabelled. Audio files are listed, greyed out, so a folder that holds
        stems is easy to recognise.
        """
        dlg = QFileDialog(self, "Choose the folder that holds the stems", start)
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, False)
        dlg.setNameFilter(AUDIO_FILTER)
        dlg.setLabelText(QFileDialog.DialogLabel.Accept, "Select Folder")
        dlg.setLabelText(QFileDialog.DialogLabel.FileName, "Folder:")
        self._size_dialog(dlg)
        return dlg

    def _size_dialog(self, dlg):
        """No wider than this window (it may be a quarter of the screen), so the
        dialog's buttons stay on screen without dragging it around."""
        dlg.resize(max(480, min(900, self.width())), max(380, min(560, self.height())))

    def _choose_stems_folder(self, start: str) -> str:
        """Ask the user for a folder; returns "" if they cancel."""
        dlg = self._make_folder_dialog(start)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            return dlg.selectedFiles()[0]
        return ""

    @pyqtSlot()
    def _load_stems_dialog(self):
        start = Path(self.config["stems_dir"])
        folder = self._choose_stems_folder(str(start) if start.is_dir() else "")
        if folder:
            self._load_stems_folder(Path(folder))

    def _load_stems_folder(self, folder: Path):
        """Show every audio file in ``folder`` as a stem."""
        try:
            files = sorted(p for p in folder.iterdir()
                           if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
        except OSError as e:
            log.exception("Cannot read stems folder %s", folder)
            QMessageBox.warning(self, "Cannot open folder", str(e))
            return
        if not files:
            QMessageBox.information(self, "No stems found",
                                    f"There are no audio files in {folder}.")
            return

        def shown(loaded):
            if loaded == 0:
                QMessageBox.warning(self, "No stems loaded",
                                    f"None of the audio files in {folder} could be read.")
                return
            skipped = len(files) - loaded
            note = f" ({skipped} unreadable, skipped)" if skipped else ""
            log.info("Loaded %d stems from %s%s", loaded, folder, note)
            self.statusBar().showMessage(f"Loaded {loaded} stems from {folder.name}{note}", 5000)
        self._show_stems([str(p) for p in files], then=shown)

    def closeEvent(self, event):
        if self._shown_tab == self._settings_tab and not self._leave_settings():
            event.ignore()  # the user chose Cancel on the unsaved settings
            return
        if self._volume_timer.isActive():
            self._volume_timer.stop()
            self._save_volume()
        self._abandon_load()
        for loader in list(self._stale_loaders):
            loader.wait(3000)  # it stops at its next block
        if self._player is not None:
            self._player.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(initial_file=initial)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
