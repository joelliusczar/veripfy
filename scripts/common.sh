# Shared helpers for setup.sh and install.sh. Source this file; don't run it.
# Never installs anything into the system Python: packages only ever go into
# a virtual environment.

PYTHON="${PYTHON:-python3}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    red()    { printf '\033[31m%s\033[0m\n' "$*"; }
    green()  { printf '\033[32m%s\033[0m\n' "$*"; }
    yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
else
    red()    { printf '%s\n' "$*"; }
    green()  { printf '%s\n' "$*"; }
    yellow() { printf '%s\n' "$*"; }
fi

# Suggest the install command for this distro's package manager.
pkg_hint() {  # $1 = debian pkg, $2 = fedora pkg, $3 = arch pkg, $4 = opensuse pkg
    if command -v apt-get >/dev/null; then echo "sudo apt install $1"
    elif command -v dnf >/dev/null;   then echo "sudo dnf install $2"
    elif command -v pacman >/dev/null; then echo "sudo pacman -S $3"
    elif command -v zypper >/dev/null; then echo "sudo zypper install $4"
    else echo "install '$1' with your package manager"
    fi
}

# Exit unless Python 3.10+ is available as $PYTHON.
check_python() {
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
}

# Report on ffmpeg/ffprobe; sets FFMPEG_MISSING=1 if either is absent.
check_ffmpeg() {
    FFMPEG_MISSING=0
    local tool
    for tool in ffmpeg ffprobe; do
        if command -v "$tool" >/dev/null; then
            green "Found $tool"
        else
            red "$tool not found. veripfy uses it to decode audio."
            FFMPEG_MISSING=1
        fi
    done
    if [ "$FFMPEG_MISSING" -eq 1 ]; then
        echo "  Install it with: $(pkg_hint ffmpeg ffmpeg ffmpeg ffmpeg)"
        echo "  (on Fedora, ffmpeg comes from RPM Fusion)"
    fi
}

# ensure_venv VENV_DIR REQUIREMENTS_FILE [recreate]
# Make sure VENV_DIR is a working virtual environment with the requirements
# installed. Reuses an existing one; rebuilds it if it is broken (for example
# after a Python upgrade) or if "recreate" is passed.
# Returns 0 if the venv is ready. Returns 1 if no venv could be made but the
# system Python already has the requirements (callers may fall back to it).
# Exits if neither is possible.
ensure_venv() {
    local venv="$1" req="$2" recreate="${3:-}"

    if [ -d "$venv" ] && { [ "$recreate" = "recreate" ] \
            || ! "$venv/bin/python" -c 'import sys' >/dev/null 2>&1; }; then
        if [ "$recreate" = "recreate" ]; then
            yellow "Removing existing virtual environment $venv"
        else
            yellow "Virtual environment $venv is broken; rebuilding it"
        fi
        rm -rf "$venv"
    fi

    if [ ! -x "$venv/bin/python" ]; then
        echo "Creating virtual environment in $venv"
        mkdir -p "$(dirname "$venv")"
        if "$PYTHON" -m venv "$venv" >/dev/null 2>&1; then
            :
        elif rm -rf "$venv" && command -v virtualenv >/dev/null \
                && virtualenv --quiet -p "$PYTHON" "$venv"; then
            :
        else
            rm -rf "$venv"
            yellow "Could not create a virtual environment (the venv module is unavailable)."
            echo "  On Debian/Ubuntu: sudo apt install python3-venv, then run this again"
            if "$PYTHON" -c 'import numpy' 2>/dev/null; then
                green "Using system Python instead: $("$PYTHON" -c 'import numpy; print("numpy", numpy.__version__)') is already installed"
                return 1
            fi
            red "numpy is not installed and no virtual environment could be made."
            echo "  Install the venv module (above) or numpy itself:"
            echo "  $(pkg_hint python3-numpy python3-numpy python-numpy python3-numpy)"
            exit 1
        fi
    fi

    echo "Installing Python packages"
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$req"
    green "Installed: $("$venv/bin/python" -c 'import numpy; print("numpy", numpy.__version__)')"
    return 0
}

# write_file_atomic DEST MODE < content
# Replace DEST in one step, so a re-run never leaves a half-written file and a
# running copy is not disturbed.
write_file_atomic() {
    local dest="$1" mode="$2" tmp
    mkdir -p "$(dirname "$dest")"
    tmp="$(mktemp "$(dirname "$dest")/.veripfy.XXXXXX")"
    cat > "$tmp"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$dest"
}
