"""transcribe.cpp의 GGUF 모델을 hayamimi 라이브 경로에 끼워 넣는 어댑터.

hayamimi의 run_stream/Refiner는 RoutedASR 객체를 받아 몇 개의 메서드만
호출합니다. 여기서는 언어가 고정된 단일 모델로 그 표면을 흉내 냅니다.
_identify_lang이 고정 언어를 그대로 돌려주므로 Refiner의 언어 재판정
분기는 항상 "변경 없음"으로 닫히고, _get/_decode_full/_replace/ko_spacer는
호출되지 않습니다.
"""
from __future__ import annotations

import os
import sys
import threading

import numpy as np
from transcribe_cpp.errors import OutputTruncated, UnsupportedRequest

import stream

# 반복 폭주 판정: 4-gram 다양도가 이 값 아래면 환각으로 봅니다. 실측에서
# 정상 구간은 0.92~0.98, Fun-ASR의 폭주 구간은 0.06이었습니다.
NGRAM_N = 4
DIVERSITY_FLOOR = 0.35
MIN_CHARS_TO_JUDGE = 40


def ngram_diversity(text: str, n: int = NGRAM_N) -> float:
    """서로 다른 n-gram 비율. 같은 구절을 반복하면 0에 가까워집니다."""
    s = "".join(text.split())
    if len(s) < n:
        return 1.0
    grams = [s[i:i + n] for i in range(len(s) - n + 1)]
    return len(set(grams)) / len(grams)


def looks_hallucinated(text: str) -> bool:
    t = text.strip()
    if len(t) < MIN_CHARS_TO_JUDGE:
        return False
    return ngram_diversity(t) < DIVERSITY_FLOOR


def resolve_device(want: str) -> str:
    """쓸 백엔드를 정합니다.

    `auto`는 있는 것 중 가장 빠른 것을 고릅니다 -- GPU가 있으면 GPU입니다.
    `cpu`는 GPU가 있어도 CPU로 돌립니다. 그 편이 나은 기계가 있습니다:
    내장 그래픽은 시스템 메모리를 CPU와 나눠 쓰고 대역폭도 좁아, 코어가
    넉넉한 노트북에서는 CPU가 더 빠르거나 최소한 다른 일을 방해하지
    않습니다. 전사와 번역이 같은 작은 iGPU를 다투는 것도 피할 수 있습니다.

    `vulkan`/`metal`/`cuda`/`rocm`처럼 딱 집어 줄 수도 있습니다. 없는 것을
    집으면 세우지 않고 auto로 물러납니다 -- 설정 한 줄 때문에 전사가 아예
    안 되는 것보다 낫습니다.
    """
    import transcribe_cpp as tc

    want = (want or "auto").strip().lower()
    if want in ("", "auto", "gpu"):
        # 'gpu'라는 정책은 없습니다. auto가 이미 GPU를 먼저 고릅니다.
        return "auto"
    try:
        if tc.backend_available(want):
            return want
    except Exception:
        pass
    print(f"[asr] '{want}' 백엔드를 쓸 수 없어 auto로 돌아갑니다",
          file=sys.stderr)
    return "auto"


class TranscribeCppASR:
    """RoutedASR 자리에 들어가는 단일 언어 어댑터."""

    def __init__(self, model_path: str, lang: str, threads: int = 4,
                 label: str = "", device: str = "auto"):
        import transcribe_cpp as tc

        self.device = resolve_device(device)
        self.forced_lang = lang
        self.min_switch_s = 0.0
        # os.path.basename을 씁니다. "/"로만 자르면 윈도우의 역슬래시
        # 경로에서 전체 경로가 통째로 화면의 엔진 이름이 됩니다.
        self.label = label or os.path.basename(model_path)
        self.hallucinations = 0

        self.threads = threads
        self._model = tc.Model(model_path, backend=self.device)
        self._session = self._model.session(n_threads=threads)
        self._check_language()
        print(f"[asr] {self.label} · {self.device} · {threads}스레드",
              file=sys.stderr, flush=True)
        # 바인딩 세션은 동시 호출을 보장하지 않습니다. 라이브 경로는
        # 빠른 패스와 정제 패스가 서로 다른 스레드에서 들어오므로
        # 직렬화합니다.
        self._lock = threading.Lock()

    def swap(self, spec: dict | None):
        """돌아가는 세션에서 전사 모델을 갈아 끼웁니다.

        **객체를 바꾸지 않고 속만 바꿉니다.** `run_stream` 은 asr 을 지역
        변수로 받아 들고 있고 `Refiner` 도 따로 참조를 쥐고 있어서, 세션의
        `_asr` 을 새 객체로 갈아 끼워 봐야 돌고 있는 루프는 옛 것을 계속
        씁니다. 속을 바꾸면 그 참조들이 저절로 새 모델을 가리킵니다.

        예전에는 이 자리에서 세션을 통째로 다시 시작했습니다. 그러면 세션
        id가 바뀌고, 자막은 세션 id로 저장되므로 **그때까지의 스크립트가
        화면에서 사라졌습니다.** 한 영상 안에서 자막은 이어져야 합니다.

        새 모델을 다 세우고 나서 바꿉니다. 언어를 지원하지 않는 등으로
        실패하면 쓰던 것이 그대로 남습니다 -- 바꾸려다 방송을 잃는 것이
        가장 나쁩니다.
        """
        import transcribe_cpp as tc

        r = resolve_asr(spec, self.forced_lang)
        model = tc.Model(r["path"], backend=r["device"])
        session = model.session(n_threads=r["threads"])
        self._probe_language(session, r["label"])
        # 해독 한 번이 끝나기를 기다렸다 바꿉니다. transcribe 도 같은 자물쇠를
        # 쥐므로, 반쯤 바뀐 상태로 해독이 들어가는 일은 없습니다.
        with self._lock:
            self._model, self._session = model, session
            self.device, self.threads, self.label = r["device"], r["threads"], r["label"]
        print(f"[asr] 갈아 끼움 -> {self.label} · {self.device} · {self.threads}스레드",
              file=sys.stderr, flush=True)
        return {"label": self.label, "device": self.device, "threads": self.threads}

    def _check_language(self):
        """이 모델이 이 언어를 아는지 시작할 때 물어봅니다.

        영어 전용 모델(moonshine 등)에 일본어를 물리면 해독할 때마다
        UnsupportedRequest 가 납니다. 그것을 그냥 두면 자막이 몇 줄 나오지
        않다가 세션이 끝나고, 로그에는 같은 예외가 여러 줄 쌓입니다.
        재시도해도 결과가 달라질 수 없는 실패이므로 여기서 잘라 냅니다 --
        방송을 20초 받아 본 뒤가 아니라, 시작하는 순간에 압니다.

        무음 0.1초면 충분합니다. moonshine 기준 50밀리초쯤 듭니다.
        """
        self._probe_language(self._session, self.label)

    def _probe_language(self, session, label: str):
        if not self.forced_lang:
            return              # 자동 판별에 맡긴 경우는 물어볼 것이 없습니다
        try:
            session.run(np.zeros(1600, dtype=np.float32),
                        language=self.forced_lang)
        except UnsupportedRequest as exc:
            raise RuntimeError(
                f"{label} 모델은 '{self.forced_lang}' 언어를 "
                f"지원하지 않습니다. 원본 언어를 바꾸거나 다른 전사 엔진을 "
                f"고르십시오. ({exc})") from exc
        except Exception:
            # 다른 실패는 여기서 판단하지 않습니다. 무음 한 조각으로
            # 모델 전체를 단정할 근거가 없습니다.
            pass

    # --- RoutedASR가 노출하는 속성들 -------------------------------------
    @property
    def punct(self):
        return None

    @property
    def ko_spacer(self):
        return None

    def resident_models(self) -> list[str]:
        return [self.label]

    def reset_session(self):
        pass

    # --- 언어 판정: 고정이므로 판정하지 않습니다 -------------------------
    def _identify_lang(self, samples: np.ndarray, sample_rate: int) -> str:
        return self.forced_lang

    def identify(self, samples: np.ndarray, sample_rate: int) -> str:
        return self.forced_lang

    def _replace(self, text: str) -> str:
        return text

    def partial(self, samples: np.ndarray, sample_rate: int,
                lang_hint: str | None = None) -> str:
        # forced_lang이 설정되면 run_stream은 partial을 호출하지 않습니다.
        return ""

    # --- 본 전사 ----------------------------------------------------------
    def transcribe(self, samples: np.ndarray, sample_rate: int,
                   known_lang: str | None = None,
                   speech_s: float | None = None,
                   live: bool = True) -> dict:
        import time

        if sample_rate != 16000:
            raise ValueError(f"16kHz만 지원합니다 (받은 값 {sample_rate})")

        pcm = np.ascontiguousarray(samples, dtype=np.float32)
        t0 = time.perf_counter()
        try:
            with self._lock:
                result = self._session.run(pcm, language=self.forced_lang)
        except OutputTruncated:
            # 생성 상한에 닿았다는 것은 몇 초짜리 조각에서 256토큰을 뽑아
            # 냈다는 뜻이고, 그런 조각은 사람의 발화가 아니라 같은 말을
            # 반복하는 폭주입니다. 아래 다양도 검사가 잡아낼 것과 같은
            # 현상이 예외로 먼저 튀어나온 것이므로 같게 처리합니다.
            self.hallucinations += 1
            print(f"[환각 차단] {self.label} 생성 상한 초과 "
                  f"({len(samples) / sample_rate:.1f}초 조각)", flush=True)
            return {"text": "", "lang": self.forced_lang, "tier": self.label,
                    "lid_ms": 0.0,
                    "decode_ms": (time.perf_counter() - t0) * 1000,
                    "probe_ms": 0.0}
        decode_ms = (time.perf_counter() - t0) * 1000

        text = (result.text or "").strip()
        if looks_hallucinated(text):
            self.hallucinations += 1
            print(f"[환각 차단] {self.label} 다양도 "
                  f"{ngram_diversity(text):.2f}: {text[:50]}", flush=True)
            text = ""

        return {"text": text, "lang": self.forced_lang, "tier": self.label,
                "lid_ms": 0.0, "decode_ms": decode_ms, "probe_ms": 0.0}


# 언어별 기본 배치.
#
# whisper-large-v3-turbo 하나로 전부 처리합니다. 다국어 모델이라 언어를
# 골라 넣을 수도, 비워 두고 스스로 판별하게 할 수도 있습니다.
#
# 언어별 전문 모델을 붙이는 안은 실측으로 접었습니다. 일본어에서 Fun-ASR는
# 솔로 방송을 7% 더 받아 적었지만, 합방에서 -l ja 고정을 무시하고
# 베트남어·인도네시아어를 뱉고 내부 토큰(!sil)까지 흘렸습니다. 한국어에서
# SenseVoice는 `데이터독`을 `데이터`로 줄였고, 양자화를 F32까지 올려도
# 그대로였습니다. 한 종류의 방송만 잘 보는 모델보다 전부 견디는 모델이
# 낫습니다. 근거는 measurements/RESULTS.md 11~14장에 있습니다.
MODEL_DIR = os.environ.get(
    "MIMIWATCH_MODEL_DIR",
    os.path.join(os.path.expanduser("~"), ".local", "share",
                 "mimiwatch", "models"))
WHISPER = os.path.join(MODEL_DIR, "whisper-large-v3-turbo-Q8_0.gguf")


def resolve_asr(spec: dict | None, lang: str | None) -> dict:
    """설정 한 덩어리에서 실제로 쓸 모델·장치·스레드를 뽑아냅니다.

    새로 만들 때(`build_live_asr`)와 돌아가는 세션에서 갈아 끼울 때
    (`TranscribeCppASR.swap`)가 같은 규칙을 써야 하므로 떼어 두었습니다.
    """
    spec = spec or {}
    path = (spec.get("models") or {}).get(lang or "") or spec.get("model") or WHISPER
    # 설정에는 파일 이름만 적을 수 있게 합니다. 전체 경로를 적으라고 하면
    # 윈도우·맥의 모델 위치가 달라 예시를 그대로 쓸 수 없습니다.
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(stream.model_dir(), path)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"전사 모델이 없습니다: {path}\n"
            "./install.sh 를 실행하거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
    device = resolve_device(spec.get("device", "auto"))
    # 설정에 스레드 수가 적혀 있으면 그것이 우선입니다. 없으면 어디서
    # 도는지에 맞춰 정합니다.
    return {"path": path, "device": device,
            "threads": int(spec.get("threads") or stream.default_threads(device)),
            "label": os.path.basename(path).replace(".gguf", "")}


def build_live_asr(spec: dict | None, lang: str | None, threads: int = 4):
    """세션이 쓸 인식기를 만듭니다.

    lang이 비어 있으면 모델이 스스로 판별합니다. 다만 방송 언어를 알고
    있다면 지정하는 편이 낫습니다 -- 판별이 흔들리면 문장 하나가 통째로
    다른 언어로 나옵니다.
    """
    r = resolve_asr(spec, lang)
    return TranscribeCppASR(r["path"], lang, threads=r["threads"],
                            label=r["label"], device=r["device"])
