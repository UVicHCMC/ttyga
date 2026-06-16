#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
DATA_DIR="$HOME/.local/share/ttyga"
CONFIG_DIR="$HOME/.config/ttyga"
ICONS_BASE="$HOME/.local/share/icons/hicolor"

DRY_RUN=false
UNINSTALL=false

usage() {
    echo "Usage: $0 [--dry-run] [--uninstall]"
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=true ;;
        --uninstall) UNINSTALL=true ;;
        *) usage ;;
    esac
done

run() {
    if "$DRY_RUN"; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

if "$UNINSTALL"; then
    echo "Uninstalling ttyga..."
    run rm -f "$BIN_DIR/ttyga"
    run rm -f "$APPS_DIR/ca.greg.ttyga.desktop"
    run rm -f "$DATA_DIR/TTYGA.md"
    run rm -f "$DATA_DIR/TTYGA_TECH.md"
    run rmdir "$DATA_DIR" 2>/dev/null || true
    # Mirror the install: remove every icon the source theme provides.
    while IFS= read -r -d '' src; do
        run rm -f "$ICONS_BASE/${src#"$SCRIPT_DIR/ttyga-icon-theme/hicolor/"}"
    done < <(find "$SCRIPT_DIR/ttyga-icon-theme/hicolor" -type f -print0)
    run update-desktop-database "$APPS_DIR" 2>/dev/null || true
    run gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    echo "Done."
    exit 0
fi

# --- preflight checks -------------------------------------------------------

missing=()
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Vte','3.91'); from gi.repository import Gtk, Adw, Vte" 2>/dev/null \
    || missing+=("python3-gi / gir1.2-gtk-4.0 / gir1.2-adw-1 / gir1.2-vte-2.91")
python3 -c "import yaml" 2>/dev/null \
    || missing+=("python3-yaml")

if (( ${#missing[@]} > 0 )); then
    echo "Missing dependencies:"
    for m in "${missing[@]}"; do echo "  $m"; done
    echo "Install them, then re-run this script."
    exit 1
fi

# --- install ----------------------------------------------------------------

echo "Installing ttyga..."

run mkdir -p "$BIN_DIR" "$APPS_DIR" "$DATA_DIR" "$CONFIG_DIR"

run install -m 755 "$SCRIPT_DIR/ttyga.py"       "$BIN_DIR/ttyga"
run install -m 644 "$SCRIPT_DIR/TTYGA.md"       "$DATA_DIR/TTYGA.md"
run install -m 644 "$SCRIPT_DIR/TTYGA_TECH.md"  "$DATA_DIR/TTYGA_TECH.md"

# Seed profiles.yaml on first install only — never overwrite user data.
if "$DRY_RUN" || [ ! -f "$CONFIG_DIR/profiles.yaml" ]; then
    run install -m 644 "$SCRIPT_DIR/profiles.yaml.example" "$CONFIG_DIR/profiles.yaml"
    if ! "$DRY_RUN"; then
        echo "  Created $CONFIG_DIR/profiles.yaml from example — edit it to add your own profiles."
    fi
fi

# Icons — every file is stored under its published name, so the whole theme
# copies in verbatim. Adding an icon to ttyga-icon-theme/ needs no edit here.
run cp -r "$SCRIPT_DIR/ttyga-icon-theme/hicolor/." "$ICONS_BASE/"

if ! "$DRY_RUN"; then
    cat > "$APPS_DIR/ca.greg.ttyga.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=ttyga
Comment=Terminal with a profile sidebar
Exec=ttyga
Icon=ca.greg.ttyga
Terminal=false
Categories=System;TerminalEmulator;
StartupNotify=true
StartupWMClass=ca.greg.ttyga
EOF
else
    echo "[dry-run] write $APPS_DIR/ca.greg.ttyga.desktop"
fi

run update-desktop-database "$APPS_DIR" 2>/dev/null || true
run gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
run rm -rf "$HOME/.cache/ttyga"

echo "Done. Make sure $BIN_DIR is on your PATH."
