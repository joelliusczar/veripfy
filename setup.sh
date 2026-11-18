#!/usr/bin/env bash
# Set up veripfy to run from this repository (for development).
# To install it on your PATH instead, use ./install.sh.
#
#   ./setup.sh            create .venv (if missing) and install requirements
#   ./setup.sh --recreate delete and rebuild .venv from scratch
#
# Afterwards run ./veripfy (a launcher that uses the venv; no need to activate it).
# If no virtual environment can be created, the launcher falls back to the
# system Python, provided numpy is installed there.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
. "$DIR/scripts/common.sh"

recreate=""
case "${1:-}" in
    --recreate) recreate="recreate" ;;
    "") ;;
    *) echo "usage: $0 [--recreate]" >&2; exit 2 ;;
esac

check_python
check_ffmpeg
ensure_venv "$DIR/.venv" "$DIR/requirements.txt" "$recreate" || true

write_file_atomic "$DIR/veripfy" 755 <<'LAUNCHER'
#!/usr/bin/env bash
# veripfy launcher (repository copy, created by setup.sh).
# Uses the repository's virtual environment, or the system Python if there is none.
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$DIR/veripfy.py" "$@"
LAUNCHER
chmod +x "$DIR/veripfy.py"

echo
if [ "$FFMPEG_MISSING" -eq 1 ]; then
    yellow "Python environment ready, but ffmpeg still needs installing (see above)."
else
    green "Setup complete."
fi
echo "Run:  $DIR/veripfy --help"
echo "To install it on your PATH:  $DIR/install.sh"
[ "$FFMPEG_MISSING" -eq 0 ]
