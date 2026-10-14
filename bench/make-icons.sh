#!/usr/bin/env bash
# Renders the extension's icons (ext/icons/icon{16,32,48,128}.png) from the one
# master, web/icon.svg.
#
# macOS only, and deliberately so: sips comes with the system, and the
# alternative was asking everyone who touches the icon to install librsvg or
# ImageMagick. The outputs are committed, so nothing else ever runs this. Run it
# only when the drawing changes.
#
#   bench/make-icons.sh
#
# sips rasterises an SVG at the width/height on the <svg> tag, not at the viewBox,
# so the master is re-declared at 1024 first and everything is resampled down from
# that. Rendering each size from a 64-declared file gives a 64px image blown up.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SRC="$ROOT/web/icon.svg"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[ "$(uname)" = "Darwin" ] || { echo "make-icons.sh needs macOS (sips)" >&2; exit 1; }
[ -f "$SRC" ] || { echo "no master at $SRC" >&2; exit 1; }

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "Render the master at 1024"
python3 - "$SRC" "$TMP/icon-1024.svg" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
svg = open(src, encoding="utf-8").read()
# Only the two attributes on the <svg> tag move; the viewBox is what keeps the
# drawing's proportions, so it is left exactly as it is.
svg = re.sub(r'(<svg\b[^>]*?)\bwidth="[^"]*"', r'\1width="1024"', svg, count=1)
svg = re.sub(r'(<svg\b[^>]*?)\bheight="[^"]*"', r'\1height="1024"', svg, count=1)
open(dst, "w", encoding="utf-8").write(svg)
PY
sips -s format png "$TMP/icon-1024.svg" --out "$TMP/1024.png" >/dev/null
sips -g pixelWidth "$TMP/1024.png" | tail -1 | sed 's/^/  /'

say "Resample"
for n in 128 48 32 16; do
  cp "$TMP/1024.png" "$TMP/$n.png"
  sips -Z "$n" "$TMP/$n.png" >/dev/null
done
echo "  1024 128 48 32 16"

say "Extension PNGs"
mkdir -p "$ROOT/ext/icons"
for n in 16 32 48 128; do
  cp "$TMP/$n.png" "$ROOT/ext/icons/icon$n.png"
done
ls -l "$ROOT/ext/icons" | tail -4 | awk '{print "  ext/icons/" $9, $5, "bytes"}'

say "Done"
echo "  The master is web/icon.svg, and it is the page's favicon as it stands."
