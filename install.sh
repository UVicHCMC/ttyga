#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
DATA_DIR="$HOME/.local/share/ttyga"
CONFIG_DIR="$HOME/.config/ttyga"
ICONS_BASE="$HOME/.local/share/icons/hicolor"

ICON_SIZES="16x16 24x24 32x32 48x48 64x64 128x128 256x256 512x512"

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
    for size in $ICON_SIZES; do
        run rm -f "$ICONS_BASE/$size/apps/ca.greg.ttyga.png"
    done
    run rm -f "$ICONS_BASE/scalable/apps/ca.greg.ttyga.svg"
    run rm -f "$ICONS_BASE/scalable/apps/ttyga-watermark.svg"
    run rm -f "$CONFIG_DIR/ttyga_mono.svg"
    run rm -f "$ICONS_BASE/scalable/actions/ttyga-split-horiz-symbolic.svg"
    run rm -f "$ICONS_BASE/scalable/actions/ttyga-split-vert-symbolic.svg"
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

# Monochrome welcome icon — always update (not user data).
run install -m 644 "$SCRIPT_DIR/ttyga_mono.svg" "$CONFIG_DIR/ttyga_mono.svg"

# Seed profiles.yaml on first install only — never overwrite user data.
if "$DRY_RUN" || [ ! -f "$CONFIG_DIR/profiles.yaml" ]; then
    run install -m 644 "$SCRIPT_DIR/profiles.yaml.example" "$CONFIG_DIR/profiles.yaml"
    if ! "$DRY_RUN"; then
        echo "  Created $CONFIG_DIR/profiles.yaml from example — edit it to add your own profiles."
    fi
fi

# Raster icons
for size in $ICON_SIZES; do
    src="$SCRIPT_DIR/ttyga-icon-theme/hicolor/$size/apps/ttyga.png"
    dst="$ICONS_BASE/$size/apps/ca.greg.ttyga.png"
    run mkdir -p "$ICONS_BASE/$size/apps"
    run install -m 644 "$src" "$dst"
done

# Scalable app icons
run mkdir -p "$ICONS_BASE/scalable/apps"
run install -m 644 \
    "$SCRIPT_DIR/ttyga-icon-theme/hicolor/scalable/apps/ttyga.svg" \
    "$ICONS_BASE/scalable/apps/ca.greg.ttyga.svg"
run install -m 644 \
    "$SCRIPT_DIR/ttyga-icon-theme/hicolor/scalable/apps/ttyga-watermark.svg" \
    "$ICONS_BASE/scalable/apps/ttyga-watermark.svg"

# Scalable action icons
run mkdir -p "$ICONS_BASE/scalable/actions"
run install -m 644 \
    "$SCRIPT_DIR/ttyga-icon-theme/hicolor/scalable/actions/ttyga-split-horiz-symbolic.svg" \
    "$ICONS_BASE/scalable/actions/ttyga-split-horiz-symbolic.svg"
run install -m 644 \
    "$SCRIPT_DIR/ttyga-icon-theme/hicolor/scalable/actions/ttyga-split-vert-symbolic.svg" \
    "$ICONS_BASE/scalable/actions/ttyga-split-vert-symbolic.svg"
run install -m 644 \
    "$SCRIPT_DIR/ttyga-icon-theme/hicolor/scalable/actions/ttyga-split-quad-symbolic.svg" \
    "$ICONS_BASE/scalable/actions/ttyga-split-quad-symbolic.svg"

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

echo "Done. Make sure $BIN_DIR is on your PATH."
