# audio-splitter

Desktop app (PyQt6) that loads an audio file, shows it as a waveform with a seekable marker, plays
it back, and splits it into stems with Demucs (vocals, drums, bass, guitar, piano, other; or 4
stems; or vocals plus everything else). The Split tab shows one waveform per stem with an on/off
toggle each, and plays the enabled stems together. Target platform is Omarchy (Arch) Linux only;
Windows is not planned.

Out of scope: recording or capturing audio; the app only opens files. Other programs can hand it a
file by passing the path as the first argument (`audio-splitter song.flac`).

## Run

```
source .venv/bin/activate
python main.py [audio file]
```

Python is **3.12** in `.venv` (created with `uv venv --python 3.12 .venv`). To rebuild:
`uv pip install --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match -r requirements.txt`
(or `-r requirements-lock.txt` for the exact known-good set). Stem separation uses Demucs via
`python -m demucs`, so it needs no PATH setup.

External programs the app shells out to: `ffplay` (playback), `ffprobe` (duration). Stem
separation runs `demucs` as a subprocess.

## Install (for users)

`./install.sh` (see `README.md`) installs for the current user without a hand-made venv: a private
Python 3.12 env in `~/.local/share/audio-splitter/venv`, the app copied to `.../app` (with its own
`config.json` and `logs/`), a launcher `~/.local/bin/audio-splitter [file]` that runs from
`~/Music/audio-splitter/` (so the relative `stems` default lands there and a relative file argument
is made absolute first), a menu entry, and the Demucs models pre-downloaded. It installs from
`requirements-lock.txt` minus the test packages, so **a new runtime dependency must go in both
`requirements.txt` and the lock file**. System Python on Arch is newer than 3.12 and Demucs is not
packaged for Arch, which is why the installer owns a 3.12 env instead of using system packages.
Tested into a throwaway `--prefix` (install, launch, offline split, re-install keeps config,
uninstall), and on a clean machine: a fresh `archlinux` Docker container with a normal user
(install including the `sudo pacman` branch, launch offscreen, a 6-stem split, re-install,
uninstall; about 4.5 minutes). That found the Qt libraries `libxkbcommon`, `libxkbcommon-x11`,
`xcb-util-keysyms` and `xcb-util-wm` missing from the package list; they are in it now. A
container has no desktop, so the window and the sound are not covered; a bare Arch machine needs
the full package list, an Omarchy one already has most of it.

## Layout

- `main.py` – entry point; sets up logging from `config.json`, opens `MainWindow` (optionally on a
  file given as the first argument).
- `config.py` / `config.json` (untracked) – tiny JSON-backed settings: `recordings_dir` (where the
  Open dialog starts), `stems_dir`, `split_mode`, `logging`.
- `core/player.py` – `PlayerThread`, the playback engine (see below).
- `core/splitter.py` – `SplitterThread`: runs Demucs and writes 16-bit FLAC stems. The Split tab
  picks a mode (`MODES`, config key `split_mode`, default `"6"`): **6** = `htdemucs_6s` (vocals,
  drums, bass, guitar, piano, other) into `stems/htdemucs_6s/<track>/`; **4** = `htdemucs` into
  `stems/htdemucs/<track>/`; **2** = `htdemucs --two-stems vocals` (`vocals` + `no_vocals`) into
  `stems/two_stem/htdemucs/<track>/` (its own folder, since it shares the model with mode 4). In
  mode 6 the piano stem is deleted when its RMS is below `PIANO_MIN_RMS` (0.002). It reads demucs's
  tqdm output to emit `progress` (0-100). `demucs_python()` runs demucs with the current interpreter
  if it has demucs, else with the project's `.venv/bin/python`, so the app also works when started
  with the system Python (which has PyQt6 but no demucs). Output is not reproducible run to run
  (Demucs's random shift); that is accepted.
- `core/waveform.py` – `min_max_envelope`: reduces audio to ~2,000 min/max buckets for drawing (the
  main waveform and every stem row).
- `core/logger.py` – logging setup (logs to `logs/`).
- `ui/main_window.py` – the window: a **Playback** tab (path box, Load from File, waveform,
  transport buttons ⏮ ▶ ■ ⏭) and a **Split** tab (stem count selector, Load Stems…, path box,
  Split, progress bar, the stem view).
- `ui/stem_view.py` – Split tab: a row per stem (toggle + compact waveform), one shared marker, and
  the four transport buttons. It only emits signals; `MainWindow` drives the one player.
- `ui/waveform_view.py` – pyqtgraph waveform with draggable marker and click-to-seek; a compact mode
  (no axes, fixed shared scale) for the stem rows.
- `stems/`, `logs/`, `.venv/`, `config.json` are gitignored.

Both file pickers (Load from File, Load Stems…) are Qt's own dialogs, not the system ones, so their
size and wording are ours: as wide as the app window (480-900 px, so the buttons stay on screen when
the window is a quarter of the screen), "Open" / "Select Folder" buttons, a shared `AUDIO_FILTER`.
Loading a file (`_load_file`) also shows its earlier stems when `existing_stems()` finds them under
`stems_dir` (the selected mode's folder first, then the other modes'; matched by file name without
extension, like the split's own output folder). Load from File starts in `recordings_dir`; Load
Stems… starts in `stems_dir`, shows every flac/wav/ogg/mp3 in the chosen folder as a row
(unreadable files are skipped and counted in the status bar) and works with no file loaded.

## Playback engine rules (hard-won, don't undo)

`core/player.py` has a docstring with the full rationale. In short:

- One supervisor thread owns at most one `ffplay` process. Never spawn ffplay anywhere else, or
  "double audio" returns.
- Public controls (`play/pause/resume/seek/stop`) must stay non-blocking; the UI thread never waits
  on the worker.
- Cross-thread timer control goes through queued signals, never `QTimer.singleShot()` from the
  worker (it silently never fires).
- Positions are milliseconds everywhere; `ffplay -ss` takes seconds, so convert with
  `_ms_to_ffplay_ss()` at the single spawn site.
- The skip-forward button uses the waveform's duration, not an ffprobe fallback.
- The 100 ms position tick holds still while a restart is pending (`_play_request_ms` set). Without
  that, a tick landing while the old ffplay was torn down added the time already played to the new
  start, so the marker popped forward then snapped back (seen when toggling stems; plain seeks
  while playing had the same bug).
- Stems play as ONE ffplay: `PlayerThread([paths])` mixes them with an `amix` graph (`mix_graph`,
  `normalize=0` so levels sum). ffplay ignores `-ss` for filter input, so the start offset is
  `seek_point` inside each `amovie`. File names are escaped twice (`_lavfi_escape`): quoting alone
  breaks on `: ' \ =`. `set_sources()` (a stem toggle) restarts that one process from the current
  position, like a seek; with every stem off it plays silence to the end. There is still only one
  player: `MainWindow` replaces it when switching between the loaded file and the stems
  (`_drop_player`), and switching tabs stops playback.

## Testing

```
source .venv/bin/activate
pytest                 # fast suite (~20 s): config, player, UI, waveform, splitter helpers
pytest -m slow         # real Demucs separation (downloads the models the first time)
```

Tests live in `tests/`, never touch the real `config.json` (an autouse fixture redirects it) and run
Qt offscreen with `SDL_AUDIODRIVER=dummy`, so nothing is audible. `pytest.ini` pins `qt_api = pyqt6`
(PySide6 may also be installed and pytest-qt would otherwise pick it). `ffplay_pids()` in
`tests/conftest.py` anchors on `^ffplay ` (a shell script that merely mentions ffplay and the needle
used to be counted as a process). Player tests that look for stem playback use the `needle` fixture
(their own temp folder), so an app playing stems at the same time does not disturb them. Listening
checks (does it sound right, does dragging feel right) still need a manual run of the app.

## Workflow

- Commit directly on `main`. Do not create branches or pull requests unless asked. Commit when a
  piece of work is done and verified; push only when asked.
- Commit messages are short and imperative, describing the behavior change.
- Keep `CHANGELOG.md` up to date: add user-visible changes (added / changed / fixed / removed) under
  the newest date heading when you commit them. There are no version numbers yet; if the project
  starts using them, propose a scheme first.
- The project is GPL-3.0-or-later (`LICENSE`), because PyQt6 is GPL v3 (or a paid commercial
  licence). Moving to PySide6 (LGPL) would allow a more permissive licence. The Demucs code is MIT;
  no licence is stated for the model weights. They are downloaded by the installer from the
  upstream source and are not part of this repository, so keep it that way.

## Findings worth keeping

- **Piano threshold.** `PIANO_MIN_RMS` (0.002) checked on three songs: no piano 0.0003 (dropped); a
  full-band song 0.0164 (kept, stem judged clean); a voice-and-piano song 0.0092 (kept; there `other`
  was far louder at 0.1077, so much of the piano sits in `other`). Keep it unless a quiet-piano song
  loses its stem.
- **Voice and piano songs: use the 2-stem mode.** `no_vocals` is the whole mix without voices, so it
  holds all the piano (RMS within run-to-run noise of the 6-stem accompaniment summed).
- **Limit of the 6-stem mode on piano-led songs.** Most piano lands in `other`, and a little voice
  leaks into it. No help from: GPU instead of CPU (same result within run-to-run noise),
  `htdemucs_ft` then `htdemucs_6s` on its `other`. No other Demucs model has a piano stem.
  `htdemucs_6s` weights are 55 MB vs 84 MB for `htdemucs`; by the published SDR in
  audio-separator's model list it is a bit below `htdemucs` on drums (8.5 vs 9.4) and bass (10.1 vs
  11.6), but full-band songs sounded fine, so the one-pass 6-stem split stays. Untried:
  `BS-Roformer-SW` in `audio-separator` (unverified whether it has piano/guitar stems; would add a
  dependency).
- **Vocals by singer: researched, not feasible, dropped.** Lead/backing "karaoke" models (UVR /
  `audio-separator`, e.g. Mel-Roformer karaoke) assume one lead voice; when several sing together
  everything goes to the non-lead stem. No released model splits similar voices (the one relevant
  paper, SepACap, arXiv 2509.26580, a cappella only, has no released code or weights); RipX,
  SpectraLayers, LALAL.AI and Moises only do lead vs backing group. Revisit only if a per-singer
  model with released code appears.
- **GPU vs CPU** (a 3:23 song, `htdemucs_6s`): 34 s on CPU, 9.5 s on a Radeon RX 7900 XT (ROCm,
  torch 2.14 rocm build). The rule of thumb: stay on CPU PyTorch while a split takes about a minute
  or less, which keeps the install small.

## Known issues / ideas not built

- A crash on audio stop was only partially addressed.
- Dim the waveform of a disabled stem; read the stems off the UI thread (drawing six rows blocks
  ~1.5 s); a minutes:seconds time axis; zoom/scroll on the waveform; richer export options.
