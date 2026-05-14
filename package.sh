#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VERSION="$(grep -m1 'APP_VERSION\s*=' "$SCRIPT_DIR/ttyga.py" | sed 's/.*"\(.*\)".*/\1/')"
OUTFILE="$SCRIPT_DIR/ttyga_${VERSION}.zip"
STAGING="$(mktemp -d)"
BUNDLE="$STAGING/ttyga"

trap 'rm -rf "$STAGING"' EXIT

echo "Packaging ttyga ${VERSION}..."

mkdir -p "$BUNDLE"

cp "$SCRIPT_DIR/ttyga.py"              "$BUNDLE/"
cp "$SCRIPT_DIR/install.sh"            "$BUNDLE/"
cp "$SCRIPT_DIR/TTYGA.md"             "$BUNDLE/"
cp "$SCRIPT_DIR/TTYGA_TECH.md"        "$BUNDLE/"
cp "$SCRIPT_DIR/profiles.yaml.example" "$BUNDLE/"

cp -r "$SCRIPT_DIR/ttyga-icon-theme"  "$BUNDLE/"

cd "$STAGING"
zip -r "$OUTFILE" ttyga

echo "Written: $OUTFILE"
