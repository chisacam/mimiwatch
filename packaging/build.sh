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
# llama-cpp-python on Apple Silicon. The source build takes minutes (cmake, all of
# llama.cpp), so a wheel is worth having -- but the maintainer's prebuilt Metal
# wheels cannot be relied on. 0.3.33, 0.3.34 and 0.3.35 each fail a CRC check, on a
# different member every time, and two downloads of 0.3.35 come back byte for byte
# identical: the published files are damaged, and no amount of retrying or caching
# fixes a file that is wrong at the source. The v0.6.0 build spent 2m35s of its
# 3m33s on the fallback while the log claimed there was no wheel at all.
#
# So: keep a wheel of our own. Look in the wheelhouse first, then upstream, and
# only build one when neither gives something whole -- and keep that build, so the
# next run does not repeat it. CI keeps the directory between runs; on a developer
# machine it simply persists.
#
# Everything is checked before it is trusted, ours included. That check is the one
# thing that would have found this in the first place.
WHEELHOUSE="${MIMIWATCH_WHEELHOUSE:-$ROOT/.wheels}"

whole() {
  [ -f "$1" ] && python3 -c \
    'import sys,zipfile; sys.exit(1 if zipfile.ZipFile(sys.argv[1]).testzip() else 0)' \
    "$1" 2>/dev/null
}
newest_wheel() { ls -t "$WHEELHOUSE"/llama_cpp_python-*.whl 2>/dev/null | head -1; }

if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  say "llama-cpp-python (Metal)"
  mkdir -p "$WHEELHOUSE"
  kept="$(newest_wheel)"
  if whole "$kept"; then
    "$VENV/bin/pip" install -q "$kept"
    echo "  from the wheelhouse: $(basename "$kept")"
  else
    if [ -n "$kept" ]; then
      echo "  the kept wheel is damaged, dropping it"
      rm -f "$WHEELHOUSE"/llama_cpp_python-*.whl
    fi
    dl_log="$(mktemp)"
    if "$VENV/bin/pip" download -q --only-binary=:all: --no-deps -d "$WHEELHOUSE" \
         --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal \
         llama-cpp-python >"$dl_log" 2>&1 && whole "$(newest_wheel)"; then
      "$VENV/bin/pip" install -q "$(newest_wheel)"
      echo "  from upstream: $(basename "$(newest_wheel)")"
    else
      rm -f "$WHEELHOUSE"/llama_cpp_python-*.whl
      echo "  no whole wheel upstream; building one and keeping it. Why it was not usable:"
      tail -6 "$dl_log" | sed 's/^/    /'
      "$VENV/bin/pip" wheel -q --no-deps -w "$WHEELHOUSE" llama-cpp-python
      "$VENV/bin/pip" install -q "$(newest_wheel)"
      echo "  built and kept: $(basename "$(newest_wheel)")"
    fi
    rm -f "$dl_log"
  fi
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
