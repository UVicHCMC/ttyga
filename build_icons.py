#!/usr/bin/env python3

# Note that the apt package librsvg2-bin is needed for this script to work.

import argparse
import re
import shutil
import subprocess
from pathlib import Path


SIZES = [16, 24, 32, 48, 64, 128, 256, 512]


def sanitize_svg(svg_text: str) -> str:
    # Strip width/height only from the root <svg> tag so rsvg-convert uses
    # the viewBox for sizing instead of the embedded pixel dimensions.
    # Leaving these attributes on child elements (e.g. background rects).
    def _strip(m):
        return re.sub(r'\s+(width|height)="[^"]*"', '', m.group(0), flags=re.I)
    return re.sub(r'<svg\b[^>]*>', _strip, svg_text, count=1, flags=re.I)


def ensure_command(cmd: str):
    if shutil.which(cmd) is None:
        raise RuntimeError(f"Required command not found: {cmd}")


def render_png(svg_path: Path, out_path: Path, size: int, dry_run: bool):
    if dry_run:
        print(f"  [dry-run] rsvg-convert {size}x{size} → {out_path}")
        return
    subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size),
         "-o", str(out_path), str(svg_path)],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate GNOME application icons from an SVG."
    )
    parser.add_argument("svg", help="Source SVG file")
    parser.add_argument("-n", "--name", default="app",
                        help="Application/icon name (default: app)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output directory (default: directory of the source SVG)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing anything")
    args = parser.parse_args()

    dry_run = args.dry_run

    if not dry_run:
        ensure_command("rsvg-convert")

    src = Path(args.svg).resolve()
    icon_name = args.name
    outdir = Path(args.output).resolve() if args.output else src.parent

    if not src.exists():
        raise FileNotFoundError(src)

    scalable_dir = outdir / "hicolor" / "scalable" / "apps"
    if dry_run:
        print(f"[dry-run] mkdir -p {scalable_dir}")
    else:
        scalable_dir.mkdir(parents=True, exist_ok=True)

    svg_clean = sanitize_svg(src.read_text())
    scalable_svg = scalable_dir / f"{icon_name}.svg"

    if dry_run:
        print(f"[dry-run] write sanitised SVG → {scalable_svg}")
    else:
        scalable_svg.write_text(svg_clean)

    for size in SIZES:
        size_dir = outdir / "hicolor" / f"{size}x{size}" / "apps"
        png_out = size_dir / f"{icon_name}.png"
        if not dry_run:
            size_dir.mkdir(parents=True, exist_ok=True)
        render_png(scalable_svg, png_out, size, dry_run)

    if not dry_run:
        icons_base = Path("~/.local/share/icons/hicolor").expanduser()
        print()
        print(f"Created icon set in: {outdir}")
        print()
        print("Install with:")
        print(f"  cp -r {outdir}/hicolor/. {icons_base}/")
        print(f"  gtk-update-icon-cache -f -t {icons_base}")
        print()
        print("Then reference the icon by name:")
        print(f"  Icon={icon_name}")


if __name__ == "__main__":
    main()
