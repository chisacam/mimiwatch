#!/usr/bin/env bash
# 맥(과 리눅스)용 묶음을 만듭니다.
#
#   packaging/build.sh              dist/mimiwatch.app (맥) 또는 dist/mimiwatch/ (리눅스)
#   MIMIWATCH_VERSION=0.3.0 packaging/build.sh
#
# 빌드 전용 가상환경(.venv-build)을 따로 만듭니다. 개발용 .venv 에는 pytest·ruff 같은
# 것이 섞여 있고, 묶음에는 실행에 필요한 것만 들어가야 합니다.
#
# 맥에서 llama-cpp-python 은 소스에서 빌드됩니다(PyPI 에 맥 휠이 없습니다). Apple
# Silicon 에서는 Metal 이 기본으로 켜집니다. cmake 가 필요합니다: brew install cmake.
# transcribe.cpp 는 휠이 있으므로 빌드하지 않습니다.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="${PYTHON:-python3}"
VENV="$ROOT/.venv-build"
VERSION="${MIMIWATCH_VERSION:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || echo 0.0.0)}"
export MIMIWATCH_VERSION="$VERSION"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "빌드 가상환경 ($VENV)"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt" -r "$HERE/requirements-build.txt"
"$VENV/bin/pip" install -q -U "yt-dlp[default]" transcribe-cpp

say "런타임 확인"
"$VENV/bin/python" - <<'PY'
import transcribe_cpp, llama_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi  # noqa: F401
print("  transcribe.cpp 백엔드:", sorted({b.kind for b in transcribe_cpp.backends()}))
print("  llama-cpp-python:", llama_cpp.__version__, "· yt-dlp:", yt_dlp.version.__version__)
PY

say "PyInstaller"
rm -rf "$ROOT/dist/mimiwatch" "$ROOT/dist/mimiwatch.app"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$ROOT/dist" --workpath "$ROOT/build" "$HERE/mimiwatch.spec"

say "동작 확인 (묶음 안의 파이썬으로 모듈 적재)"
if [ "$(uname)" = "Darwin" ]; then
  BIN="$ROOT/dist/mimiwatch.app/Contents/MacOS/mimiwatch"
else
  BIN="$ROOT/dist/mimiwatch/mimiwatch"
fi
"$BIN" --ytdlp --version | head -1

say "압축"
cd "$ROOT/dist"
if [ "$(uname)" = "Darwin" ]; then
  ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && ARCH=x64
  OUT="mimiwatch-$VERSION-macos-$ARCH.zip"
  rm -f "$OUT"
  # ditto 는 확장 속성과 심볼릭 링크를 지켜 .app 을 그대로 담습니다. zip 은 그렇지 않습니다.
  ditto -c -k --keepParent mimiwatch.app "$OUT"
else
  OUT="mimiwatch-$VERSION-linux-$(uname -m).tar.gz"
  rm -f "$OUT"
  tar -czf "$OUT" mimiwatch
fi
ls -lh "$OUT"
printf '\n\033[1m끝났습니다:\033[0m dist/%s\n' "$OUT"
