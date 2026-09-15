#!/usr/bin/env bash
# Renders every icon the project ships from the one master, web/icon.svg.
#
# macOS only, and deliberately so: sips and iconutil come with the system, and
# the alternative was asking everyone who touches the icon to install librsvg or
# ImageMagick. The outputs are committed, so a build -- including the Windows one
# on a runner that has neither tool -- never runs this. Run it only when the
# drawing changes.
#
#   packaging/make-icons.sh
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

[ "$(uname)" = "Darwin" ] || { echo "make-icons.sh needs macOS (sips, iconutil)" >&2; exit 1; }
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
for n in 512 256 128 64 48 32 16; do
  cp "$TMP/1024.png" "$TMP/$n.png"
  sips -Z "$n" "$TMP/$n.png" >/dev/null
done
echo "  1024 512 256 128 64 48 32 16"

say "macOS .icns"
SET="$TMP/mimiwatch.iconset"
mkdir -p "$SET"
cp "$TMP/16.png"   "$SET/icon_16x16.png"
cp "$TMP/32.png"   "$SET/icon_16x16@2x.png"
cp "$TMP/32.png"   "$SET/icon_32x32.png"
cp "$TMP/64.png"   "$SET/icon_32x32@2x.png"
cp "$TMP/128.png"  "$SET/icon_128x128.png"
cp "$TMP/256.png"  "$SET/icon_128x128@2x.png"
cp "$TMP/256.png"  "$SET/icon_256x256.png"
cp "$TMP/512.png"  "$SET/icon_256x256@2x.png"
cp "$TMP/512.png"  "$SET/icon_512x512.png"
cp "$TMP/1024.png" "$SET/icon_512x512@2x.png"
iconutil -c icns "$SET" -o "$HERE/icon.icns"
ls -l "$HERE/icon.icns" | awk '{print "  packaging/icon.icns", $5, "bytes"}'

say "Windows .ico"
# Written by hand because Pillow is not a dependency of this project and adding
# one for six PNGs in a container would be the wrong trade. An .ico is a 6-byte
# header, a 16-byte entry per image, then the images; PNG entries have been
# understood since Vista.
python3 - "$HERE/icon.ico" "$TMP" <<'PY'
import struct, sys
out, tmp = sys.argv[1], sys.argv[2]
sizes = [16, 32, 48, 64, 128, 256]
blobs = [open(f"{tmp}/{n}.png", "rb").read() for n in sizes]
offset = 6 + 16 * len(sizes)
header = struct.pack("<HHH", 0, 1, len(sizes))
entries, body = b"", b""
for n, blob in zip(sizes, blobs):
    # 0 in the width/height byte means 256 -- the field is one byte wide.
    entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32,
                           len(blob), offset)
    offset += len(blob)
    body += blob
open(out, "wb").write(header + entries + body)
PY
ls -l "$HERE/icon.ico" | awk '{print "  packaging/icon.ico", $5, "bytes"}'

say "Extension PNGs"
mkdir -p "$ROOT/ext/icons"
for n in 16 32 48 128; do
  cp "$TMP/$n.png" "$ROOT/ext/icons/icon$n.png"
done
ls -l "$ROOT/ext/icons" | tail -4 | awk '{print "  ext/icons/" $9, $5, "bytes"}'

say "Done"
echo "  The master is web/icon.svg, and it is the page's favicon as it stands."
