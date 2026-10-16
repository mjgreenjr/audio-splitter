#!/usr/bin/env bash
# Install audio-splitter for the current user (Arch / Omarchy).
#
#   ./install.sh                  install or update
#   ./install.sh --uninstall      remove the app (your stems are kept)
#   ./install.sh --prefix DIR     install under DIR instead of ~/.local
#   ./install.sh --no-system-packages   skip the pacman step
#
# What it does: installs the system programs the app needs (only if missing),
# makes a private Python 3.12 environment, installs the pinned Python packages
# (PyTorch CPU build), downloads the Demucs models, copies the app, and adds a
# launcher plus an application-menu entry. You never create or activate an
# environment yourself. Start it with: audio-splitter [audio file]
#
# Layout (with the default prefix ~/.local):
#   ~/.local/share/audio-splitter/venv   private Python environment
#   ~/.local/share/audio-splitter/app    the program, plus config.json and logs/
#   ~/.local/bin/audio-splitter          launcher
#   ~/Music/audio-splitter/              stems/ (the launcher runs there)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${HOME}/.local"
SYSTEM_PACKAGES=1
UNINSTALL=0

# Programs and libraries the app needs from the system:
#   ffmpeg (ffplay plays, ffprobe measures), libsndfile (soundfile),
#   xcb-util-cursor, xcb-util-keysyms, xcb-util-wm, libxkbcommon and libxkbcommon-x11
#   (the libraries Qt's windowing plugin loads), uv (builds the Python 3.12 env).
PACMAN_PACKAGES=(ffmpeg libsndfile xcb-util-cursor xcb-util-keysyms xcb-util-wm libxkbcommon libxkbcommon-x11)
DEMUCS_MODELS=(htdemucs_6s htdemucs)

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) UNINSTALL=1 ;;
        --no-system-packages) SYSTEM_PACKAGES=0 ;;
        --prefix) [ $# -ge 2 ] || die "--prefix needs a directory"; PREFIX="$2"; shift ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

APP_HOME="$PREFIX/share/audio-splitter"
VENV="$APP_HOME/venv"
APP="$APP_HOME/app"
BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/audio-splitter"
DESKTOP_FILE="$PREFIX/share/applications/audio-splitter.desktop"
WORK_DIR="${HOME}/Music/audio-splitter"

[ "$(id -u)" -ne 0 ] || die "run this as your normal user, not root (it uses sudo only for pacman)"

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" -eq 1 ]; then
    say "Removing audio-splitter"
    rm -rf "$APP_HOME"
    rm -f "$LAUNCHER" "$DESKTOP_FILE"
    command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true
    say "Removed. Kept: $WORK_DIR (your stems) and the Demucs models in ~/.cache/huggingface."
    exit 0
fi

# ---------------------------------------------------------------- checks
[ -f "$SRC_DIR/main.py" ] && [ -d "$SRC_DIR/core" ] && [ -d "$SRC_DIR/ui" ] \
    || die "run install.sh from inside the audio-splitter folder"
[ -f "$SRC_DIR/requirements-lock.txt" ] || die "requirements-lock.txt is missing"

# ---------------------------------------------------------------- system packages
if [ "$SYSTEM_PACKAGES" -eq 1 ]; then
    command -v pacman >/dev/null || die "pacman not found: this installer is for Arch / Omarchy (use --no-system-packages to skip)"
    wanted=("${PACMAN_PACKAGES[@]}")
    command -v uv >/dev/null || wanted+=(uv)   # uv from mise or pipx counts
    missing=$(pacman -T "${wanted[@]}" || true)
    if [ -n "$missing" ]; then
        say "Installing system packages: $(echo "$missing" | tr '\n' ' ')"
        # shellcheck disable=SC2086
        sudo pacman -S --needed --noconfirm $missing
    else
        say "System packages already installed"
    fi
else
    say "Skipping system packages (--no-system-packages)"
fi
command -v uv >/dev/null || die "uv is not installed (sudo pacman -S uv)"
for prog in ffmpeg ffplay ffprobe; do
    command -v "$prog" >/dev/null || warn "$prog not found on PATH: playback needs it"
done

# ---------------------------------------------------------------- app files
say "Copying the app to $APP"
mkdir -p "$APP"
# Replace the code but keep config.json and logs/ from earlier installs.
rm -rf "$APP/core" "$APP/ui"
cp -r "$SRC_DIR/core" "$SRC_DIR/ui" "$APP/"
cp "$SRC_DIR/main.py" "$SRC_DIR/config.py" "$APP/"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------- python environment
if [ ! -x "$VENV/bin/python" ]; then
    say "Creating a private Python 3.12 environment (downloads Python if needed)"
    uv venv --python 3.12 "$VENV" --quiet
else
    say "Reusing the environment in $VENV"
fi

say "Checking Python packages (only missing or changed ones are installed; PyTorch CPU build, a few hundred MB on the first run)"
# The lock file is what the tests ran against; leave out the test-only packages.
runtime_lock="$(mktemp)"
trap 'rm -f "$runtime_lock"' EXIT
grep -v -i -E '^(pytest|pytest-qt|iniconfig|pluggy|pygments)==' "$SRC_DIR/requirements-lock.txt" > "$runtime_lock"
uv pip install --quiet --python "$VENV/bin/python" \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --index-strategy unsafe-best-match \
    -r "$runtime_lock"

# ---------------------------------------------------------------- demucs models
say "Checking the Demucs models (${DEMUCS_MODELS[*]}); only missing ones are downloaded, so the first split works at once"
# HF_HUB_VERBOSITY=error hides huggingface_hub's "unauthenticated requests" notice; a failed
# download still stops the install with an error.
HF_HUB_VERBOSITY=error "$VENV/bin/python" - "${DEMUCS_MODELS[@]}" <<'PY'
import sys
from demucs.pretrained import get_model
for name in sys.argv[1:]:
    get_model(name)
    print("  ready:", name)
PY

# ---------------------------------------------------------------- launcher
say "Writing the launcher $LAUNCHER"
mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP_FILE")"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# audio-splitter launcher (written by install.sh). Stems go to the working folder
# below unless the settings say otherwise.
# Usage: audio-splitter [audio file]
WORK_DIR="\${AUDIO_SPLITTER_WORKDIR:-$WORK_DIR}"
# The app starts in the working folder, so make a file argument absolute first.
args=()
for arg in "\$@"; do
    if [ -e "\$arg" ]; then args+=("\$(realpath "\$arg")"); else args+=("\$arg"); fi
done
mkdir -p "\$WORK_DIR"
cd "\$WORK_DIR"
exec "$VENV/bin/python" "$APP/main.py" "\${args[@]}"
EOF
chmod +x "$LAUNCHER"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Audio Splitter
Comment=Play audio files and split them into stems
Exec=$LAUNCHER
Icon=audio-x-generic
Terminal=false
Categories=AudioVideo;Audio;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true

# ---------------------------------------------------------------- self-check
say "Checking the installation"
QT_QPA_PLATFORM=offscreen "$VENV/bin/python" - "$APP" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import PyQt6.QtWidgets, pyqtgraph, soundfile, numpy, demucs, torch  # noqa: F401
from core.splitter import demucs_python
assert demucs_python() == sys.executable, "demucs is not available in the private environment"
print("  imports ok, torch", torch.__version__)
PY

say "Done. Start it from the application menu, or run: audio-splitter"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH, so 'audio-splitter' will not be found in a terminal. The menu entry still works." ;;
esac
echo "Stems are kept in $WORK_DIR/stems"
