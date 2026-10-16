# Changelog

Notable changes to audio-splitter, newest first. The project has no version numbers yet, so
entries are grouped by date.

Everything before 2026-10-13 was written up afterwards from the commit messages of the project's
earlier development history (before this repository's first commit). It is condensed to playback,
stem splitting and installation.

## 2026-10-15

### Added
- A licence: GPL-3.0-or-later (`LICENSE`), with a note in the README about the Demucs models.
- Loading an audio file also loads the stems of an earlier split of it, if there are any. The stems
  of the selected stem count are used first, then those of another count.

### Fixed
- The installer now also installs the Qt libraries `libxkbcommon`, `libxkbcommon-x11`,
  `xcb-util-keysyms` and `xcb-util-wm`. On an Arch system without a desktop their absence stopped
  the app from starting (`libxkbcommon.so.0` not found).

## 2026-10-13

### Added
- Playback tab: load a file, see its waveform, play it, seek by clicking or dragging, stop, and
  jump to the start or end.
- `audio-splitter song.flac` opens a file at startup (the launcher makes a relative path absolute
  before the app starts in its own folder). A file that does not exist is reported in the status
  bar instead of crashing.

### Changed
- Load from File uses Qt's own file dialog, sized to the app window so its buttons stay on screen
  when the window is small, and opens in the audio folder.

## 2026-10-12

### Added
- Splitting into 6 stems with Demucs `htdemucs_6s` (vocals, drums, bass, guitar, piano, other), with
  a progress bar. A piano stem that is essentially silent is dropped.
- A stem-count selector: 6 stems, 4 stems, or 2 stems (vocals and everything else). Your choice is
  remembered.
- Split tab shows one waveform per stem on a shared time axis, with an on/off toggle for each stem
  and one shared marker (click or drag any row). The enabled stems play together as a single
  `ffplay` process; toggling a stem restarts it from the current position.
- Load Stems…: show stems that are already on disk, from a folder picked in a Qt folder dialog
  that is sized to the window.
- `install.sh` and a README: a private Python 3.12 environment, a launcher, an application-menu entry
  and the Demucs models downloaded up front, with `--uninstall`.
- The Playback and Split tabs show the full path of the loaded file in a read-only box.

### Changed
- Waveforms are drawn from about 2,000 min/max points instead of one point per sample. Loading a
  3-minute song went from 1.2 s and 9 million plot points to 0.2 s and 4,000.
- The app finds a Python that has Demucs even when it was started with another Python.
- The installer says its Demucs and Python-package steps are checks: only missing items are
  downloaded or installed.

### Fixed
- The position marker jumped forward and snapped back when `ffplay` restarted for a seek or a stem
  toggle.

## 2026-10-09

### Changed
- Replaced Spleeter with Demucs (`htdemucs`) for stem separation, moved to Python 3.12 and dropped
  the old version pins.
- Separated stems are written as FLAC.

### Added
- An automated test suite for the player, the UI and the splitter. Tests no longer touch the real
  settings file.

### Fixed
- Reading a file's duration with `ffprobe`.

### Removed
- Unused modules, old debug scripts and dead imports, and an experimental voice-separation attempt
  that never worked.

## 2026-10-08

### Changed
- Pinned Python and the dependencies of the Spleeter-based splitter, and added a requirements lock
  file and project notes (`CLAUDE.md`).
- Rebalanced log levels: INFO for user actions, DEBUG for seeking and polling, and splitter
  failures are logged.

## 2026-10-07

### Changed
- Rewrote the player as a single supervisor thread: one `ffplay` process at a time, and the controls
  never block the UI. The player is now persistent, and seeking while paused no longer starts
  playback.

### Added
- Click-to-seek on the waveform, and a wider marker that is easier to grab.
- Skip-to-start and skip-to-end transport buttons (⏮ ▶ ⏹ ⏭). Skip-forward uses the waveform's
  duration instead of an `ffprobe` fallback.

### Fixed
- Playback and scrubber out of sync: `ffplay -ss` takes seconds, the base offset was wrong, and the
  position timer was controlled from another thread.
- The play/pause button state did not follow when seeking restarted playback.
- The marker could be dragged past the end of the audio.

## 2026-10-01

### Added
- Initial version: waveform view, playback, and stem splitting (with Spleeter at the time).
- Player: duration detection, seeking, position tracking, error handling and crash recovery.
