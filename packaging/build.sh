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
# 맥 arm64 는 만든 쪽 인덱스에 Metal 휠(0.3.35, py3-none)이 있습니다. 먼저 그것을 받아
# 소스 빌드(cmake, 5~10분)를 건너뜁니다. 없으면 아래 requirements 설치가 소스에서 만듭니다.
if [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  "$VENV/bin/pip" install -q --only-binary=:all: \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal llama-cpp-python \
    && echo "  llama-cpp-python: Metal 휠" || echo "  llama-cpp-python: Metal 휠이 없어 소스에서 빌드합니다"
fi
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

# 맥: PyInstaller 가 넣어 준 ad-hoc 서명을 다시, 확실하게 덮습니다. 동작 확인이 앱을
# 실행하면 macOS 가 캐시·provenance 속성을 붙이는데, 그 뒤에 압축하면 봉인이 어긋나
# "손상됨"이 될 수 있습니다. 서명 → 확인 → 압축 순서를 지키고, 서명을 마지막에 한 번 더
# 합니다. 공증(notarization)은 유료 Developer ID 가 필요해 하지 않습니다 -- 사용자는 첫
# 실행에 한 번 「그래도 열기」(또는 xattr) 를 거칩니다. docs/PACKAGING.md 참조.
if [ "$(uname)" = "Darwin" ]; then
  say "ad-hoc 서명"
  # --deep 은 낡았지만 onedir 의 수백 개 중첩 바이너리를 한 번에 덮는 가장 확실한 길입니다.
  # MIMIWATCH_CODESIGN 에 Developer ID 를 주면 그것으로 서명합니다(그때만 공증 가능).
  IDENT="${MIMIWATCH_CODESIGN:--}"
  codesign --force --deep --sign "$IDENT" "$ROOT/dist/mimiwatch.app"
  codesign --verify --deep --strict "$ROOT/dist/mimiwatch.app" && echo "  서명 검증 OK ($IDENT)"
fi

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
  # 동작 확인이 붙인 provenance·격리 속성을 떼어 냅니다. 서명은 이 속성과 무관하지만,
  # 이것이 남은 채 압축되면 받는 쪽에 불필요한 격리가 함께 갑니다.
  xattr -cr mimiwatch.app
  # 확인이 서명 뒤에 실행됐으니 봉인을 마지막으로 한 번 더 확인합니다.
  codesign --verify --deep --strict mimiwatch.app || { echo "서명이 깨졌습니다"; exit 1; }
  # ditto 는 확장 속성과 심볼릭 링크를 지켜 .app 을 그대로 담습니다. zip 은 그렇지 않습니다.
  ditto -c -k --keepParent mimiwatch.app "$OUT"
else
  OUT="mimiwatch-$VERSION-linux-$(uname -m).tar.gz"
  rm -f "$OUT"
  tar -czf "$OUT" mimiwatch
fi
ls -lh "$OUT"
printf '\n\033[1m끝났습니다:\033[0m dist/%s\n' "$OUT"
