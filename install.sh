#!/usr/bin/env bash
# Install veripfy for the current user, without root.
#
#   ./install.sh               install, or update an existing install
#   ./install.sh --recreate    also rebuild the virtual environment from scratch
#   ./install.sh --uninstall   remove the installed copy and launcher
#
# Files go to:
#   ~/.local/share/veripfy/    a copy of veripfy.py and its own virtual environment
#   ~/.local/bin/veripfy       the launcher you run
# Override with VERIPFY_HOME and VERIPFY_BIN_DIR (XDG_DATA_HOME is respected).
#
# Running it again is always safe: it copies the current veripfy.py over the
# installed one, reuses the virtual environment (rebuilding it only if it is
# broken), and only installs missing or changed Python packages. Nothing is
# ever installed into the system Python.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$SRC/scripts/common.sh"

APP="${VERIPFY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/veripfy}"
BIN_DIR="${VERIPFY_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/veripfy"
MARKER="# veripfy launcher"   # identifies launchers this script may replace

usage() { echo "usage: $0 [--recreate | --uninstall]" >&2; exit 2; }

# A launcher we're allowed to overwrite or remove: one written by this script
# or setup.sh (possibly via a symlink), or nothing at all.
ours() {
    [ ! -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ] && return 0
    grep -q "^$MARKER" "$LAUNCHER" 2>/dev/null
}

uninstall() {
    if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
        if ours; then
            rm -f "$LAUNCHER"
            green "Removed $LAUNCHER"
        else
            red "$LAUNCHER was not installed by veripfy; leaving it alone."
        fi
    fi
    if [ -f "$APP/veripfy.py" ]; then
        rm -rf "$APP"
        green "Removed $APP"
    elif [ -e "$APP" ]; then
        red "$APP doesn't look like a veripfy install; leaving it alone."
    fi
    green "veripfy uninstalled."
}

recreate=""
case "${1:-}" in
    --uninstall) uninstall; exit 0 ;;
    --recreate)  recreate="recreate" ;;
    "") ;;
    *) usage ;;
esac

if [ "$(id -u)" -eq 0 ] && [ -z "${VERIPFY_ALLOW_ROOT:-}" ]; then
    yellow "Running as root installs veripfy for root only. Run as your normal user instead,"
    yellow "or set VERIPFY_ALLOW_ROOT=1 to continue."
    exit 1
fi

if ! ours; then
    red "$LAUNCHER already exists and wasn't created by veripfy."
    echo "  Move it out of the way, or set VERIPFY_BIN_DIR to install somewhere else."
    exit 1
fi

check_python
check_ffmpeg

# Virtual environment, private to this install.
use_venv=1
ensure_venv "$APP/venv" "$SRC/requirements.txt" "$recreate" || use_venv=0
if [ "$use_venv" -eq 0 ]; then
    rm -rf "$APP/venv"
fi

# Copy the program. Each file is replaced in one step, so re-running after
# editing veripfy.py always installs the edited version.
write_file_atomic "$APP/veripfy.py" 644 < "$SRC/veripfy.py"
write_file_atomic "$APP/requirements.txt" 644 < "$SRC/requirements.txt"
write_file_atomic "$APP/source" 644 <<<"$SRC"

# Launcher with absolute paths baked in.
if [ "$use_venv" -eq 1 ]; then
    py_line="PY=$(printf '%q' "$APP/venv/bin/python")"
else
    py_line='PY="$(command -v python3)"'
fi
write_file_atomic "$LAUNCHER" 755 <<LAUNCHER
#!/usr/bin/env bash
$MARKER (installed by install.sh from $SRC)
$py_line
exec "\$PY" $(printf '%q' "$APP/veripfy.py") "\$@"
LAUNCHER

installed_version="$("$LAUNCHER" --version 2>&1 || true)"

echo
green "Installed $installed_version"
echo "  program:  $APP"
echo "  launcher: $LAUNCHER"

case ":$PATH:" in
    *":$BIN_DIR:"*) echo "Run:  veripfy --help" ;;
    *)
        yellow "$BIN_DIR is not on your PATH."
        echo "  Add this line to ~/.bashrc (or your shell's config), then open a new terminal:"
        echo "    export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac
if [ "$FFMPEG_MISSING" -eq 1 ]; then
    yellow "veripfy needs ffmpeg to run; install it as shown above."
fi
