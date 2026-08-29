#!/usr/bin/env bash
# mimiwatch를 처음부터 설치합니다.
#
# 저장소만 받아서는 아무것도 돌지 않습니다. 의존성을 깔고 모델을 따로 내려받아야
# 합니다. 그 과정을 여기 모았습니다.
#
# 이미 끝난 단계는 건너뜁니다. 중간에 끊기면 그냥 다시 실행하십시오.
#
#   ./install.sh
#
# **더는 아무것도 빌드하지 않습니다.** 예전에는 transcribe.cpp를 형제 디렉터리에
# 받아 CMake로 빌드했는데, 이제 `transcribe-cpp-native` 휠이 맥(arm64·x86_64)·
# 리눅스·윈도우에 전부 있고 맥 휠에는 Metal이 들어 있습니다. cmake도 git도
# 준비물에서 빠졌습니다. 소스에서 빌드한 것을 꼭 쓰려면 TRANSCRIBE_CPP_DIR을
# 주십시오 -- 그때만 예전처럼 그 체크아웃의 바인딩을 깝니다.
#
# 모델은 modelhub.py가 받습니다. 화면의 「엔진 관리 › 모델·도구」와 같은 목록을
# 보므로, 여기서 건너뛴 것은 나중에 화면에서 받을 수 있습니다.
#
# 윈도우는 install.ps1 을 쓰십시오. 자세한 것은 docs/WINDOWS.md.
#
# 환경변수로 위치를 바꿀 수 있습니다.
#   MIMIWATCH_MODEL_DIR  모델 (기본: ~/.local/share/mimiwatch/models)
#   WITH_GEMMA=1         번역용 Gemma(4.9GB)도 함께 받습니다. 기본 설정은 가벼운 CPU
#                        엔진(SenseVoice Small + M2M-100)이라 기본으로는 받지 않습니다.
#                        화면의 「초기 설정」에서 골라도 그때 받습니다.
#   TRANSCRIBE_CPP_DIR   (선택) 직접 빌드한 transcribe.cpp 체크아웃

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

# ── 0. 준비물 ──────────────────────────────────────────────────────────
say "준비물 확인"
command -v python3 >/dev/null 2>&1 || die "python3가 없습니다. macOS라면: brew install python"
ok "python3"
# ffmpeg는 준비물이 아니라 권장입니다. 없으면 화면의 「모델·도구」에서 정적 빌드를
# 받을 수 있고, 아래 5단계가 그것을 안내합니다.
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg"; else skip "ffmpeg 없음 (아래에서 안내)"; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 이상이 필요합니다."


# ── 1. 가상환경 ────────────────────────────────────────────────────────
say "가상환경"
if [ -x "$PY" ]; then skip "있음"; else python3 -m venv "$HERE/.venv"; ok "생성"; fi
"$PIP" install -q --upgrade pip
"$PIP" install -q -r "$HERE/requirements.txt"
# yt-dlp만 따로 올립니다. requirements에 이미 있지만, -r 은 이미 깔린 것을
# 그대로 두므로 다시 실행해도 판올림이 되지 않습니다. 유튜브가 추출 경로를
# 바꾸면 낡은 판은 포맷을 하나도 받지 못하므로(이슈 #1), 이 한 줄이
# "다시 설치하면 고쳐진다"를 성립시킵니다.
"$PIP" install -q -U yt-dlp
ok "의존성 설치 (yt-dlp $("$PY" -m yt_dlp --version 2>/dev/null | head -1))"

# ── 2. transcribe.cpp ──────────────────────────────────────────────────
say "transcribe.cpp (전사 런타임)"
# CLI를 구간마다 부르면 매번 모델을 다시 읽어야 해서 라이브에 쓸 수 없습니다.
# 파이썬 바인딩은 모델을 한 번만 올리고 재사용합니다. 휠 하나로 끝납니다 --
# 맥 휠에는 CPU와 Metal이, 리눅스 휠에는 CPU와 Vulkan이 들어 있습니다.
if [ -n "$TCPP" ]; then
  # 직접 빌드한 것을 쓰겠다는 뜻입니다. 그 체크아웃의 바인딩을 깝니다.
  [ -d "$TCPP/bindings/python" ] || die "TRANSCRIBE_CPP_DIR에 bindings/python이 없습니다: $TCPP"
  "$PIP" install -q "$TCPP/bindings/python"
  ok "파이썬 바인딩 (소스: $TCPP)"
elif "$PY" -c "import transcribe_cpp" >/dev/null 2>&1; then
  skip "설치됨"
else
  "$PIP" install -q transcribe-cpp
  ok "휠 설치"
fi
"$PY" -c 'import transcribe_cpp as t; print("  쓸 수 있는 백엔드:", ", ".join(sorted({b.kind for b in t.backends()})))' 2>/dev/null \
  || skip "백엔드 목록을 읽지 못했습니다 (전사는 CPU로도 돕니다)"

# ── 3. 모델 ────────────────────────────────────────────────────────────
say "모델"
# 목록은 modelhub.py 한 곳에 있습니다. 화면의 「모델·도구」와 같은 표입니다.
# 이미 있는 것은 건너뛰고, 받다 끊긴 것은 이어 받습니다.
MH_ARGS=(download default)
[ "${WITH_GEMMA:-0}" = "1" ] && MH_ARGS+=(--with-gemma)
MIMIWATCH_MODEL_DIR="$MODELS" "$PY" "$HERE/modelhub.py" "${MH_ARGS[@]}" \
  || die "모델을 다 받지 못했습니다. 다시 실행하면 이어 받습니다."

# ── 4. 설정 ────────────────────────────────────────────────────────────
say "설정"
if [ -f "$HERE/backends.json" ]; then
  skip "backends.json 있음 (덮어쓰지 않습니다)"
else
  cp "$HERE/backends.example.json" "$HERE/backends.json"
  ok "backends.json 생성"
fi
mkdir -p "$HERE/data"

# ── 5. 확인 ────────────────────────────────────────────────────────────
say "확인"
MIMIWATCH_MODEL_DIR="$MODELS" "$PY" - "$HERE" <<'PYCHECK'
import os, sys
sys.path.insert(0, sys.argv[1])
import stream, tcpp_asr                                   # noqa: F401
need = {"silero_vad.onnx": "구간 분할",
        "SenseVoiceSmall-Q8_0.gguf": "전사 (기본 · 가벼운 CPU 엔진)"}
missing = [f"{v}: {k}" for k, v in need.items()
           if not os.path.exists(os.path.join(stream.model_dir(), k))]
if missing:
    print("  없음:\n    " + "\n    ".join(missing))
    raise SystemExit(1)
print("  모듈 적재 OK")
print("  필수 모델 OK")
try:
    print("  ffmpeg:", stream.ffmpeg_cmd())
except FileNotFoundError:
    print("  ffmpeg 없음 -- brew install ffmpeg, 또는 화면의 「엔진 관리 › 모델·도구」에서 받으십시오")
PYCHECK
ok "설치 확인 완료"

printf '\n\033[1m설치가 끝났습니다.\033[0m\n\n  실행: ./run.sh\n  화면: http://localhost:8900\n\n'
printf '자세한 사용법은 README.md를 보십시오.\n'
