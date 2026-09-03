#!/usr/bin/env bash
# Installs mimiwatch from scratch.
#
# Cloning the repository alone runs nothing. The dependencies have to be
# installed and the models fetched separately. That whole process is gathered
# here.
#
# Steps that are already done are skipped. If it breaks off midway, just run it
# again.
#
#   ./install.sh
#
# **Nothing is built any more.** It used to fetch transcribe.cpp into a sibling
# directory and build it with CMake, but the `transcribe-cpp-native` wheel now
# exists for macOS (arm64 · x86_64), Linux and Windows alike, and the macOS
# wheel carries Metal. Neither cmake nor git is a prerequisite any more. To
# insist on something you built from source, give TRANSCRIBE_CPP_DIR -- only
# then does it install the bindings from that checkout, the way it used to.
#
# The models are fetched by modelhub.py. It reads the same list as "Engine
# management › Models and tools" on the screen, so whatever is skipped here can
# be fetched from the screen later.
#
# On Windows use install.ps1. For the details see docs/WINDOWS.md.
#
# Environment variables move the locations.
#   MIMIWATCH_MODEL_DIR  models (default: ~/.local/share/mimiwatch/models)
#   WITH_GEMMA=1         fetch Gemma (4.9GB) for translation as well. The default
#                        setup is the light CPU engines (SenseVoice Small +
#                        M2M-100), so it is not fetched by default. Picking it in
#                        "First-time setup" on the screen fetches it then.
#   TRANSCRIBE_CPP_DIR   (optional) a transcribe.cpp checkout you built yourself

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TCPP="${TRANSCRIBE_CPP_DIR:-}"
MODELS="${MIMIWATCH_MODEL_DIR:-$HOME/.local/share/mimiwatch/models}"
PY="$HERE/.venv/bin/python"
PIP="$HERE/.venv/bin/pip"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[2m·\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. Prerequisites ───────────────────────────────────────────────────
say "Prerequisites"
command -v python3 >/dev/null 2>&1 || die "No python3. On macOS: brew install python"
ok "python3"
# ffmpeg is not a prerequisite but a recommendation. Without it you can fetch a
# static build from "Models and tools" on the screen, and step 5 below says so.
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg"; else skip "no ffmpeg (guidance below)"; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 or newer is required."


# ── 1. Virtual environment ─────────────────────────────────────────────
say "Virtual environment"
if [ -x "$PY" ]; then skip "present"; else python3 -m venv "$HERE/.venv"; ok "created"; fi
"$PIP" install -q --upgrade pip
"$PIP" install -q -r "$HERE/requirements.txt"
# yt-dlp alone is raised on its own. It is already in requirements, but -r leaves
# what is installed alone, so re-running would not update it. When YouTube
# changes its extraction path an old copy gets no formats at all (issue #1), so
# this one line is what makes "re-install and it is fixed" hold.
"$PIP" install -q -U "yt-dlp[default]"
ok "Dependencies installed (yt-dlp $("$PY" -m yt_dlp --version 2>/dev/null | head -1))"

# ── 2. transcribe.cpp ──────────────────────────────────────────────────
say "transcribe.cpp (the transcription runtime)"
# Calling the CLI per segment reloads the model every time, so it cannot be used
# for live. The Python bindings load the model once and reuse it. One wheel is
# all it takes -- the macOS wheel carries CPU and Metal, the Linux wheel CPU and
# Vulkan.
if [ -n "$TCPP" ]; then
  # This means you want to use something you built yourself. Install the bindings
  # from that checkout.
  [ -d "$TCPP/bindings/python" ] || die "TRANSCRIBE_CPP_DIR has no bindings/python: $TCPP"
  "$PIP" install -q "$TCPP/bindings/python"
  ok "Python bindings (source: $TCPP)"
elif "$PY" -c "import transcribe_cpp" >/dev/null 2>&1; then
  skip "installed"
else
  "$PIP" install -q transcribe-cpp
  ok "wheel installed"
fi
"$PY" -c 'import transcribe_cpp as t; print("  Available backends:", ", ".join(sorted({b.kind for b in t.backends()})))' 2>/dev/null \
  || skip "Could not read the backend list (transcription runs on the CPU too)"

# ── 3. Models ──────────────────────────────────────────────────────────
say "Models"
# The list lives in one place, modelhub.py. It is the same table as "Models and
# tools" on the screen. What is already there is skipped, and a download that
# broke off resumes.
MH_ARGS=(download default)
[ "${WITH_GEMMA:-0}" = "1" ] && MH_ARGS+=(--with-gemma)
MIMIWATCH_MODEL_DIR="$MODELS" "$PY" "$HERE/modelhub.py" "${MH_ARGS[@]}" \
  || die "Could not fetch every model. Run it again and it resumes."

# ── 4. Settings ────────────────────────────────────────────────────────
say "Settings"
if [ -f "$HERE/backends.json" ]; then
  skip "backends.json present (not overwritten)"
else
  cp "$HERE/backends.example.json" "$HERE/backends.json"
  ok "backends.json created"
fi
mkdir -p "$HERE/data"

# ── 5. Check ───────────────────────────────────────────────────────────
say "Check"
MIMIWATCH_MODEL_DIR="$MODELS" "$PY" - "$HERE" <<'PYCHECK'
import os, sys
sys.path.insert(0, sys.argv[1])
import stream, tcpp_asr                                   # noqa: F401
import modelhub
# No file names are baked in. The required models follow the default engines of
# the current settings (whisper for an existing user, SenseVoice for a new
# install), so the list is what decides.
ov = modelhub.overview()
missing = [i["label"] for i in ov["items"]
           if i.get("required") and i["state"] not in ("ready", "system")]
if missing:
    print("  missing: " + ", ".join(missing))
    raise SystemExit(1)
print("  modules load OK")
print("  required models OK")
try:
    print("  ffmpeg:", stream.ffmpeg_cmd())
except FileNotFoundError:
    print('  no ffmpeg -- brew install ffmpeg, or fetch it from "Engine management > Models and tools" on the screen')
PYCHECK
ok "Install check passed"

printf '\n\033[1mInstallation finished.\033[0m\n\n  Run:    ./run.sh\n  Screen: http://localhost:8900\n\n'
printf 'For how to use it, see README.md.\n'
