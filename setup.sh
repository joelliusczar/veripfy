#!/usr/bin/env bash
# Set up a Python virtual environment for veripfy and check system dependencies.
#
#   ./setup.sh            create .venv (if missing) and install requirements
#   ./setup.sh --recreate delete and rebuild .venv from scratch
#
# Afterwards run ./veripfy (a launcher that uses the venv; no need to activate it).
# If no virtual environment can be created, the launcher falls back to the
# system Python, provided numpy is installed there.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PYTHON="${PYTHON:-python3}"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# Suggest the install command for this distro's package manager.
pkg_hint() {  # $1 = debian pkg, $2 = fedora pkg, $3 = arch pkg, $4 = opensuse pkg
    if command -v apt-get >/dev/null; then echo "sudo apt install $1"
    elif command -v dnf >/dev/null;   then echo "sudo dnf install $2"
    elif command -v pacman >/dev/null; then echo "sudo pacman -S $3"
    elif command -v zypper >/dev/null; then echo "sudo zypper install $4"
    else echo "install '$1' with your package manager"
    fi
}

missing=0

# --- Python ------------------------------------------------------------------
if ! command -v "$PYTHON" >/dev/null; then
    red "Python 3 not found."
    echo "  $(pkg_hint python3 python3 python python3)"
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    red "Python 3.10 or newer is required (found $("$PYTHON" -V 2>&1))."
    exit 1
fi
green "Found $("$PYTHON" -V 2>&1)"

# --- ffmpeg (system package; pip can't provide it) ---------------------------
for tool in ffmpeg ffprobe; do
    if command -v "$tool" >/dev/null; then
        green "Found $tool"
    else
        red "$tool not found. veripfy uses it to decode audio."
        missing=1
    fi
done
if [ "$missing" -eq 1 ]; then
    echo "  Install it with: $(pkg_hint ffmpeg ffmpeg ffmpeg ffmpeg)"
    echo "  (on Fedora, ffmpeg comes from RPM Fusion)"
fi

# --- Virtual environment -----------------------------------------------------
if [ "${1:-}" = "--recreate" ] && [ -d "$VENV" ]; then
    yellow "Removing existing $VENV"
    rm -rf "$VENV"
fi

use_venv=1
if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating virtual environment in $VENV"
    if "$PYTHON" -m venv "$VENV" >/dev/null 2>&1; then
        :
    elif rm -rf "$VENV" && command -v virtualenv >/dev/null \
            && virtualenv --quiet -p "$PYTHON" "$VENV"; then
        :
    else
        rm -rf "$VENV"
        use_venv=0
        yellow "Could not create a virtual environment (the venv module is unavailable)."
        echo "  On Debian/Ubuntu: sudo apt install python3-venv, then rerun ./setup.sh"
    fi
fi

if [ "$use_venv" -eq 1 ]; then
    echo "Installing Python packages"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r "$DIR/requirements.txt"
    green "Installed: $("$VENV/bin/python" -c 'import numpy; print("numpy", numpy.__version__)')"
elif "$PYTHON" -c 'import numpy' 2>/dev/null; then
    green "Using system Python instead: $("$PYTHON" -c 'import numpy; print("numpy", numpy.__version__)') is already installed"
else
    red "numpy is not installed and no virtual environment could be made."
    echo "  Install the venv module (above) or numpy itself:"
    echo "  $(pkg_hint python3-numpy python3-numpy python-numpy python3-numpy)"
    exit 1
fi

# --- Launcher ----------------------------------------------------------------
cat > "$DIR/veripfy" <<'LAUNCHER'
#!/usr/bin/env bash
# Runs veripfy with its virtual environment (created by setup.sh),
# or with the system Python if no virtual environment exists.
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$DIR/veripfy.py" "$@"
LAUNCHER
chmod +x "$DIR/veripfy" "$DIR/veripfy.py"

echo
if [ "$missing" -eq 1 ]; then
    yellow "Python environment ready, but ffmpeg still needs installing (see above)."
else
    green "Setup complete."
fi
echo "Run:  $DIR/veripfy --help"
echo "To run it from anywhere:  ln -s \"$DIR/veripfy\" ~/.local/bin/veripfy"
[ "$missing" -eq 0 ]
