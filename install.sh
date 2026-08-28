#!/usr/bin/env bash
# mimiwatch를 처음부터 설치합니다.
#
# 저장소만 받아서는 아무것도 돌지 않습니다. 전사는 transcribe.cpp를 직접
# 빌드해 얹고, 모델 네 개를 따로 내려받아야 합니다. 그 과정을 여기 모았습니다.
#
# 이미 끝난 단계는 건너뜁니다. 중간에 끊기면 그냥 다시 실행하십시오.
#
#   ./install.sh
#
# 윈도우는 install.ps1 을 쓰십시오. 거기서는 아무것도 빌드하지 않습니다 --
# transcribe.cpp와 llama.cpp 모두 미리 만들어진 win_amd64 휠이 있습니다.
# 자세한 것은 docs/WINDOWS.md.
#
# 환경변수로 위치를 바꿀 수 있습니다.
#   TRANSCRIBE_CPP_DIR   transcribe.cpp 체크아웃 (기본: 이 저장소의 형제 디렉터리)
#   MIMIWATCH_MODEL_DIR  모델 (기본: ~/.local/share/mimiwatch/models)
#   SKIP_GEMMA=1         번역용 Gemma(4.9GB)를 건너뜁니다. M2M-100만 씁니다.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TCPP="${TRANSCRIBE_CPP_DIR:-$(dirname "$HERE")/transcribe.cpp}"
MODELS="${MIMIWATCH_MODEL_DIR:-$HOME/.local/share/mimiwatch/models}"
PY="$HERE/.venv/bin/python"
PIP="$HERE/.venv/bin/pip"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[2m·\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# 대상: 파일명 URL 설명
fetch() {  # fetch <파일명> <URL> <설명>
  if [ -f "$MODELS/$1" ]; then skip "$3 있음"; return; fi
  printf '  %s 내려받는 중…\n' "$3"
  curl -fL --progress-bar -o "$MODELS/$1.part" "$2"
  mv "$MODELS/$1.part" "$MODELS/$1"
  ok "$3"
}

# ── 0. 준비물 ──────────────────────────────────────────────────────────
say "준비물 확인"
missing=()
for cmd in git cmake curl ffmpeg python3; do
  if command -v "$cmd" >/dev/null 2>&1; then ok "$cmd"; else missing+=("$cmd"); fi
done
if [ ${#missing[@]} -gt 0 ]; then
  printf '\n없는 것: %s\n' "${missing[*]}"
  printf 'macOS라면:  brew install %s\n' "${missing[*]}"
  die "준비물을 설치한 뒤 다시 실행하십시오."
fi
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
if [ -d "$TCPP/.git" ]; then
  skip "이미 있음: $TCPP"
else
  git clone --depth 1 https://github.com/handy-computer/transcribe.cpp "$TCPP"
  ok "받음: $TCPP"
fi
if [ -f "$TCPP/build/bin/transcribe-cli" ]; then
  skip "빌드 있음"
else
  cmake -S "$TCPP" -B "$TCPP/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$TCPP/build" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
  ok "빌드 완료"
fi
# CLI를 구간마다 부르면 매번 모델을 다시 읽어야 해서 라이브에 쓸 수 없습니다.
# 파이썬 바인딩은 모델을 한 번만 올리고 재사용합니다.
if "$PY" -c "import transcribe_cpp" >/dev/null 2>&1; then
  skip "파이썬 바인딩 설치됨"
else
  "$PIP" install -q "$TCPP/bindings/python"
  ok "파이썬 바인딩 설치"
fi

# ── 3. 모델 ────────────────────────────────────────────────────────────
say "모델"
mkdir -p "$MODELS"
HF=https://huggingface.co
GH=https://github.com/k2-fsa/sherpa-onnx/releases/download
fetch silero_vad.onnx \
  "$GH/asr-models/silero_vad.onnx" \
  "Silero VAD (632KB · 발화 구간 분할)"
fetch whisper-large-v3-turbo-Q8_0.gguf \
  "$HF/handy-computer/whisper-large-v3-turbo-gguf/resolve/main/whisper-large-v3-turbo-Q8_0.gguf" \
  "Whisper large-v3-turbo Q8_0 (845MB · 전사)"
# 낮은 사양용 대체 전사기. 3.5배 작고 훨씬 빠릅니다. 기본이 버거운
# 기계에서 「전사」 선택기로 고를 수 있게 항상 받아 둡니다.
fetch SenseVoiceSmall-Q8_0.gguf \
  "$HF/handy-computer/SenseVoiceSmall-gguf/resolve/main/SenseVoiceSmall-Q8_0.gguf" \
  "SenseVoice Small Q8_0 (241MB · 가벼운 전사)"
# 영어 전용 경량 모델. 74MB로 기본의 11분의 1인데 영어 품질은 사실상
# 같습니다(실측 35절). 다른 언어는 아예 거부하므로 영어 방송에만 씁니다.
fetch moonshine-base-Q8_0.gguf \
  "$HF/handy-computer/moonshine-base-gguf/resolve/main/moonshine-base-Q8_0.gguf" \
  "Moonshine base Q8_0 (74MB · 가벼운 영어 전사)"
if [ "${SKIP_GEMMA:-0}" != "1" ]; then
  fetch gemma-4-E4B_q4_0-it.gguf \
    "$HF/google/gemma-4-E4B-it-qat-q4_0-gguf/resolve/main/gemma-4-E4B_q4_0-it.gguf" \
    "Gemma 4 E4B q4_0 (4.9GB · 번역)"
fi

# M2M-100은 여러 파일이라 스냅샷으로 받습니다. Gemma를 건너뛴 설치에서는
# 이것이 유일한 번역기이므로 항상 받습니다.
if [ -d "$MODELS/mojicast-m2m100-ct2" ]; then
  skip "M2M-100 있음"
else
  printf '  M2M-100 (473MB · 대체 번역기) 내려받는 중…\n'
  "$PY" - "$MODELS" <<'PYGET'
import sys
from huggingface_hub import snapshot_download
snapshot_download("ishiki-emo/mojicast-m2m100-ct2",
                  local_dir=f"{sys.argv[1]}/mojicast-m2m100-ct2")
PYGET
  ok "M2M-100"
fi

# 화자 태그는 녹화본 전용이라 없어도 나머지는 돕니다.
fetch campplus_sv.onnx \
  "$GH/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx" \
  "CAM++ (27MB · 녹화본 화자 태그)"

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
        "whisper-large-v3-turbo-Q8_0.gguf": "전사"}
missing = [f"{v}: {k}" for k, v in need.items()
           if not os.path.exists(os.path.join(stream.model_dir(), k))]
if missing:
    print("  없음:\n    " + "\n    ".join(missing))
    raise SystemExit(1)
print("  모듈 적재 OK")
print("  필수 모델 OK")
PYCHECK
ok "설치 확인 완료"

printf '\n\033[1m설치가 끝났습니다.\033[0m\n\n  실행: ./run.sh\n  화면: http://localhost:8900\n\n'
printf '자세한 사용법은 README.md를 보십시오.\n'
