#!/usr/bin/env bash
# Builds the macOS (and Linux) bundle.
#
#   packaging/build.sh              dist/mimiwatch.app (macOS) or dist/mimiwatch/ (Linux)
#   MIMIWATCH_VERSION=0.3.0 packaging/build.sh
#
# A build-only virtual environment (.venv-build) is created separately. The
# development .venv has things like pytest and ruff mixed in, and the bundle
# should hold only what is needed to run.
#
# On macOS llama-cpp-python is built from source (PyPI has no macOS wheel). On
# Apple Silicon Metal is on by default. cmake is needed: brew install cmake.
# transcribe.cpp has a wheel, so it is not built.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="${PYTHON:-python3}"
VENV="$ROOT/.venv-build"
VERSION="${MIMIWATCH_VERSION:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || echo 0.0.0)}"
export MIMIWATCH_VERSION="$VERSION"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "Build virtual environment ($VENV)"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# For macOS arm64 the maintainer's index has a Metal wheel (0.3.35, py3-none). Take
# that first and skip the source build (cmake, 5-10 minutes). Without it the
# requirements install below builds it from source.
if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  "$VENV/bin/pip" install -q --only-binary=:all: \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal llama-cpp-python \
    && echo "  llama-cpp-python: Metal wheel" || echo "  llama-cpp-python: no Metal wheel, building from source"
fi
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt" -r "$HERE/requirements-build.txt"
"$VENV/bin/pip" install -q -U "yt-dlp[default]" transcribe-cpp

say "Runtime check"
"$VENV/bin/python" - <<'PY'
import transcribe_cpp, llama_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi  # noqa: F401
print("  transcribe.cpp backends:", sorted({b.kind for b in transcribe_cpp.backends()}))
print("  llama-cpp-python:", llama_cpp.__version__, "· yt-dlp:", yt_dlp.version.__version__)
PY

say "PyInstaller"
rm -rf "$ROOT/dist/mimiwatch" "$ROOT/dist/mimiwatch.app"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$ROOT/dist" --workpath "$ROOT/build" "$HERE/mimiwatch.spec"

# macOS: sign again, firmly, over the ad-hoc signature PyInstaller already put in.
# When the smoke check runs the app, macOS attaches cache and provenance
# attributes, and archiving after that can leave the seal out of step -- it
# becomes "damaged". So keep the order sign -> verify -> archive, and sign once
# more at the end. Notarization needs a paid Developer ID, so we do not do it --
# the user goes through "Open anyway" (or xattr) once on the first run. See
# docs/PACKAGING.md.
if [ "$(uname)" = "Darwin" ]; then
  say "Ad-hoc signing"
  # --deep is deprecated, but it is the surest way to cover the hundreds of nested
  # binaries of an onedir bundle in one go. Give MIMIWATCH_CODESIGN a Developer ID
  # and it signs with that instead (only then is notarization possible).
  IDENT="${MIMIWATCH_CODESIGN:--}"
  codesign --force --deep --sign "$IDENT" "$ROOT/dist/mimiwatch.app"
  codesign --verify --deep --strict "$ROOT/dist/mimiwatch.app" && echo "  signature verified OK ($IDENT)"
fi

say "Smoke check (loading modules with the Python inside the bundle)"
if [ "$(uname)" = "Darwin" ]; then
  BIN="$ROOT/dist/mimiwatch.app/Contents/MacOS/mimiwatch"
else
  BIN="$ROOT/dist/mimiwatch/mimiwatch"
fi
"$BIN" --ytdlp --version | head -1

say "Archiving"
cd "$ROOT/dist"
if [ "$(uname)" = "Darwin" ]; then
  ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && ARCH=x64
  OUT="mimiwatch-$VERSION-macos-$ARCH.zip"
  rm -f "$OUT"
  # Strip off the provenance and quarantine attributes the smoke check attached.
  # The signature has nothing to do with these attributes, but archiving with them
  # still on carries a needless quarantine over to whoever receives it.
  xattr -cr mimiwatch.app
  # The check ran after the signing, so verify the seal one last time.
  codesign --verify --deep --strict mimiwatch.app || { echo "The signature is broken"; exit 1; }
  # ditto keeps the extended attributes and the symlinks, so the .app goes in as
  # it is. zip does not.
  ditto -c -k --keepParent mimiwatch.app "$OUT"
else
  OUT="mimiwatch-$VERSION-linux-$(uname -m).tar.gz"
  rm -f "$OUT"
  tar -czf "$OUT" mimiwatch
fi
ls -lh "$OUT"
printf '\n\033[1mDone:\033[0m dist/%s\n' "$OUT"
