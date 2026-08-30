"""오디오를 발화 단위로 잘라 받아 적고, 끝난 무리를 다시 해독하는 루프.

구조는 hayamimi(MIT, oboroge0)의 `realtime_transcribe`에서 가져왔습니다.
다만 그쪽은 언어를 모른 채 들어오는 소리를 상대하므로 언어 판별, 언어별
모델 라우팅, 무리 안에서 언어가 바뀔 때의 분할, 재해독 뒤 언어 재판정까지
짊어지고 있습니다. 이 프로젝트는 세션마다 언어를 하나로 고정하고 다국어
모델 하나(whisper-large-v3-turbo)로 처리하므로 그 절반이 죽은 코드입니다.
그래서 옮겨 오면서 걷어냈습니다.

남긴 것은 세 가지입니다.

- **선행 오디오**: VAD가 잡아낸 발화 시작점보다 조금 앞에서부터 잘라
  넘깁니다. 말머리가 잘려 나가는 것을 막습니다.
- **2패스 정제**: 짧게 끊어 즉시 내보낸 확정본과 별개로, 발화 한 무리가
  끝나면 합쳐서 다시 해독합니다. 문맥이 길어져 결과가 좋아집니다.
- **순서 보장**: 정제는 한 개의 작업 스레드가 넣은 순서대로 처리합니다.
  호출마다 스레드를 띄우면 시작 순서와 실행 순서가 달라져 자막이 뒤섞입니다.
"""
from __future__ import annotations

import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
import sherpa_onnx

import paths

SAMPLE_RATE = 16000
WINDOW_SIZE = 512      # VAD가 한 번에 보는 표본 수 (16kHz에서 약 32ms)

# VAD가 잡은 발화 시작점보다 이만큼 앞에서부터 넘깁니다. 자음 하나가 잘리면
# 첫 단어가 통째로 달라지므로, 여유를 두는 편이 낫습니다.
PREROLL_S = 1.0

GROUP_GAP_S = 2.0      # 이만큼 조용하면 발화 한 무리가 끝난 것으로 봅니다
GROUP_MAX_S = 25.0     # 쉬지 않고 말해도 여기서 끊습니다 (오디오 보관 한도)

# 재해독이 확정본을 합친 것보다 이만큼 짧으면 믿지 않습니다. 재해독은 내용을
# 다듬는 것이지 잃는 것이 아니므로, 크게 줄었다면 무언가 잘못된 것입니다.
REFINE_MIN_KEEP = 0.7


def model_dir() -> str:
    """모델을 두는 곳. `MIMIWATCH_MODEL_DIR`로 바꿀 수 있습니다.

    규칙은 `paths.py`에 있습니다(윈도우는 `%LOCALAPPDATA%`, 그 밖은
    `~/.local/share`). 예전에는 여기서 직접 계산했는데, 설정과 저장소가
    묶음(PyInstaller)일 때 같은 뿌리로 나와야 해서 한 곳으로 모았습니다.
    이 이름은 부르는 쪽이 많아 그대로 둡니다.
    """
    return paths.model_dir()


class Cancelled(RuntimeError):
    """사용자가 작업을 멈췄습니다.

    전사는 한 시간짜리 영상에서 가장 긴 단계입니다. 예전에는 취소를
    단계 사이에서만 확인해서, 전사가 시작되면 끝날 때까지 멈출 수
    없었습니다 -- 낮은 사양에서 무거운 모델을 잘못 고르면 그저 기다리는
    수밖에 없었습니다. 이 예외로 해독 루프 안에서 빠져나옵니다.
    """


_YTDLP: list[str] | None = None
_FFMPEG: str | None = None

# yt-dlp 한 번 부르는 데 허용하는 시간(초). 유튜브 쪽이 멎으면 yt-dlp도 같이
# 멎는데, 상한이 없으면 그 요청 스레드(또는 세션)가 영영 기다립니다. 주소
# 해석은 보통 몇 초입니다.
YTDLP_TIMEOUT_S = 90.0


def ytdlp_cmd() -> list[str]:
    """yt-dlp를 부르는 명령.

    **가상환경에 깔린 것을 먼저 씁니다.** yt-dlp는 파이썬 패키지이고,
    설치 스크립트가 여기에 최신으로 넣어 둡니다. 그러면 시스템에 낡은
    판이 있어도 가려집니다 -- 유튜브가 추출 경로를 자주 바꿔서, 몇 달
    지난 판은 포맷 목록을 통째로 받지 못합니다(이슈 #1).

    `python -m yt_dlp`로 부릅니다. 경로를 짐작하지 않아도 되고, 스크립트
    껍데기가 어디에 어떤 이름으로 놓였든 상관없습니다.

    없으면 PATH의 `yt-dlp`로 물러납니다. 예전 설치본이 그렇습니다.

    **도구 디렉터리에 받아 둔 독립 실행 파일이 있으면 그것이 먼저입니다.**
    묶음(PyInstaller)으로 배포하면 안에 든 yt-dlp는 판이 박혀 있어 몇 달
    뒤에는 낡습니다 -- 가상환경에 두어 해결했던 그 문제가 묶음에서 되돌아옵니다.
    yt-dlp가 배포하는 독립 실행 파일은 `-U`로 스스로 판올림하므로, 「엔진
    관리 › 모델·도구」에서 받아 두면 묶음을 다시 만들지 않아도 최신을 씁니다.

    묶음 안에서는 `python -m yt_dlp`를 부를 파이썬이 없습니다. 실행 파일
    자신을 `--ytdlp`로 다시 띄우면 `app.py`가 그 인자를 보고 yt_dlp의
    main으로 넘깁니다. 자식 프로세스로 두는 이유는 그대로입니다 -- 시간
    상한을 걸고 죽일 수 있어야 합니다(유튜브가 멎으면 같이 멎습니다).
    """
    global _YTDLP
    if _YTDLP is None:
        standalone = paths.tool("yt-dlp")
        if standalone:
            _YTDLP = [standalone]
        # find_spec은 모듈을 실행하지 않습니다. import 하면 1초 가까이
        # 걸리는데, 이 함수는 자주 불립니다.
        elif importlib.util.find_spec("yt_dlp") is not None:
            _YTDLP = ([sys.executable, "--ytdlp"] if paths.frozen()
                      else [sys.executable, "-m", "yt_dlp"])
        else:
            _YTDLP = ["yt-dlp"]
        _YTDLP += _js_runtime_args()
        _YTDLP += _cookie_args()
    return list(_YTDLP)


def deno_path() -> str | None:
    """유튜브 JS 챌런지를 풀 deno. 도구 디렉터리 → 흔한 자리 → PATH."""
    return paths.which("deno")


def _js_runtime_args() -> list[str]:
    """yt-dlp에 JS 런타임 자리를 알려 줍니다.

    2025.11부터 유튜브 추출은 외부 JS 런타임(deno)이 있어야 온전합니다. yt-dlp는 맥·
    리눅스에서 **PATH만** 뒤지므로, Finder에서 띄운 묶음(PATH가 짧음)은 홈브루 deno를
    못 찾고 「모델·도구」로 받은 것도 모릅니다 -- 그래서 경로를 직접 넘깁니다.
    `--remote-components ejs:github`은 풀이 스크립트(yt-dlp-ejs)가 없거나 판이 어긋날 때
    깃허브에서 받아 오게 하는 보험입니다. 기본은 꺼져 있어 조용히 포맷만 사라집니다.
    """
    deno = deno_path()
    if not deno:
        return []
    return ["--js-runtimes", f"deno:{deno}", "--remote-components", "ejs:github"]


def ytdlp_args(*opts: str, url: str) -> list[str]:
    """yt-dlp 호출 한 줄. 주소는 언제나 `--` 뒤에 둡니다.

    주소 칸에 `-`로 시작하는 것을 붙여 넣으면 옵션으로 읽힙니다 -- `--version`이면
    판 번호가 나오고 끝이지만 `--exec`면 명령이 실행됩니다. 붙여 넣는 값은 사용자가
    통제하지 않는 곳(채팅, 게시글)에서 오기도 하므로 다섯 호출 자리가 전부 여기를
    지나게 합니다. `--no-playlist`도 여기서 붙입니다: 재생목록이 딸린 주소에서 `-j`가
    여러 줄을 내면 부르는 쪽의 JSON 해석이 통째로 넘어졌습니다.
    """
    return ytdlp_cmd() + ["--no-warnings", "--no-playlist", *opts, "--", url]


def _usable_stderr():
    """자식에게 물려줄 수 있는 표준 오류. 성치 않으면 `DEVNULL`.

    `fileno()`가 번호를 답해도 그 뒤의 핸들이 닫혀 있을 수 있어 `fstat`으로
    한 번 두드려 봅니다. 창 없이 뜬 파이썬은 `sys.stderr`가 아예 None입니다.
    """
    try:
        fd = sys.stderr.fileno()
        os.fstat(fd)
        return fd
    except Exception:
        return subprocess.DEVNULL


def child_io(*, stderr: bool = True) -> dict:
    """자식 프로세스(ffmpeg·yt-dlp)에 넘길 표준 입출력.

    **물려받은 핸들에 기대지 않습니다.** `Popen`은 stdin/stderr를 따로 주지
    않으면 부모 것을 물려주려고 복제(DuplicateHandle)하는데, 부모의 그 핸들이
    유효하지 않으면 자식을 낳기도 전에

        OSError: [WinError 6] 핸들이 잘못되었습니다

    로 넘어집니다. 0.3.1의 윈도우 사용자가 라이브 세션의 `_spawn_ffmpeg`에서
    정확히 이렇게 죽었습니다 -- 표준 오류가 성치 않은 채로 뜬 프로세스(콘솔
    없이 띄운 묶음, 작업 스케줄러·서비스로 띄운 것, 출력을 이상하게 돌려놓고
    부른 셸)입니다. 앞선 yt-dlp 호출은 `capture_output=True`로 stdout·stderr를
    파이프로 잡고 있어 살아남았고, 파이프를 stdout에만 건 ffmpeg이 걸렸습니다.

    stdin은 언제나 NUL입니다. ffmpeg에도 yt-dlp에도 넣어 줄 것이 없고,
    터미널에서 돌 때 자식이 키 입력을 가져가는 일도 함께 막습니다.

    stderr는 성한 것이 있으면 그대로 물려줍니다 -- ffmpeg의 오류 한 줄이
    콘솔이나 mimiwatch.log에 남아야 다음 보고가 진단 가능해집니다. 성치
    않으면 버립니다. 부르는 쪽이 stderr를 직접 잡는 자리(`capture_output`,
    `stderr=PIPE`)에서는 `stderr=False`로 부릅니다 -- 두 번 줄 수 없습니다.
    """
    kw = {"stdin": subprocess.DEVNULL}
    if stderr:
        kw["stderr"] = _usable_stderr()
    return kw


def ffmpeg_cmd() -> str:
    """ffmpeg 실행 파일. PATH → 흔한 자리(홈브루 등) → 도구 디렉터리 순입니다.

    예전에는 `"ffmpeg"`라는 이름을 그대로 Popen에 넘겼습니다. 터미널에서는
    되지만 Finder에서 띄운 묶음은 PATH가 짧아 홈브루 것을 못 찾고, 설치
    스크립트 없이 묶음만 받은 사람에게는 ffmpeg 자체가 없습니다. 그 경우
    「엔진 관리 › 모델·도구」에서 받아 도구 디렉터리에 둡니다(modelhub.py).

    없으면 무엇을 하면 되는지를 담아 FileNotFoundError를 냅니다. 이 오류는
    세션과 작업의 오류 칸에 그대로 실립니다.
    """
    global _FFMPEG
    if _FFMPEG is None or not os.path.exists(_FFMPEG):
        found = paths.which("ffmpeg")
        if not found:
            raise FileNotFoundError(
                "ffmpeg가 없습니다. 「엔진 관리 › 모델·도구」에서 받거나 "
                "(macOS) brew install ffmpeg / (윈도우) winget install Gyan.FFmpeg 로 "
                "설치하십시오.")
        _FFMPEG = found
    return _FFMPEG


def reset_tool_cache():
    """도구를 새로 받았을 때 부릅니다. 위 둘은 한 번 찾은 값을 기억합니다."""
    global _YTDLP, _FFMPEG
    _YTDLP = None
    _FFMPEG = None


def _cookie_args() -> list[str]:
    """멤버십 전용 방송을 받으려면 로그인한 쿠키가 있어야 합니다.

    유튜브는 아이디·비밀번호 로그인을 받지 않고 OAuth 도 더는 통하지
    않습니다. 쿠키뿐입니다.

      MIMIWATCH_YTDLP_COOKIES            쿠키 파일 (Netscape 형식)
      MIMIWATCH_YTDLP_COOKIES_BROWSER    브라우저에서 바로 (chrome, firefox …)

    **파일 쪽을 권합니다.** 유튜브는 열려 있는 탭의 계정 쿠키를 계속
    갈아 치우므로, 평소 쓰는 브라우저 프로필에서 바로 읽으면 얼마 못 가
    무효가 됩니다. 시크릿 창에서 로그인해 내보낸 뒤 그 창을 닫으면 그
    쿠키는 회전되지 않습니다(README 참조).

    둘 다 있으면 파일이 이깁니다. 함께 주면 yt-dlp 가 시크릿 세션이 아닌
    평소 쿠키를 덮어써 버립니다.
    """
    path = (os.environ.get("MIMIWATCH_YTDLP_COOKIES") or "").strip()
    if path:
        return ["--cookies", path]
    # 확장이 넘겨 준 쿠키(POST /api/cookies/youtube). 환경변수가 있으면 그쪽이 우선입니다.
    # 파일을 지우면(화면의 「지우기」) 다음 호출부터 빠집니다 -- reset_tool_cache 가 같이 불립니다.
    pushed = paths.cookies_path()
    if os.path.isfile(pushed):
        return ["--cookies", pushed]
    browser = (os.environ.get("MIMIWATCH_YTDLP_COOKIES_BROWSER") or "").strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def default_threads(device: str) -> int:
    """CPU로 돌릴 때는 스레드를 더 씁니다.

    GPU 경로에서 4는 넉넉합니다 -- 무거운 일은 GPU가 하고 CPU는 앞뒤만
    맡습니다. CPU로 돌리면 그 4가 전부이므로 코어 수에 맞춰 올립니다.
    논리 코어를 다 쓰면 오히려 나빠지는 일이 잦아 절반에서 멈춥니다.

    전사와 번역이 같은 규칙을 쓰도록 여기에 둡니다. 둘 다 stream을 이미
    가져오므로, 한쪽이 다른 쪽의 런타임에 묶이지 않습니다.
    """
    if (device or "").strip().lower() == "cpu":
        return max(4, min(8, (os.cpu_count() or 8) // 2))
    return 4


# 어느 Silero 파일을 쓸지. 기본은 k2 재수출(v4 계열, 643KB). `silero_vad_v5.onnx`(2.3MB)도
# 같은 자리에서 받을 수 있고 bench/vad_ab.py 가 둘을 맞대어 잽니다.
VAD_FILE = os.environ.get("MIMIWATCH_VAD_MODEL") or "silero_vad.onnx"
# 말이라고 볼 확률의 문턱. Silero 기본은 0.5인데 **0.3으로 낮춥니다.** 정답 자막이 있는
# 노래 표본 넷에서 0.5 → 0.3이 전체 오류율 64.4% → 59.0%로 유일하게 오차를 넘는 이득이었고
# (반주 위의 노랫소리를 0.5는 무음으로 봄: 277초 곡에서 말 57초 → 177초), 대화 표본
# 셋에서는 말 판정 초·빈 구간·환각 차단이 그대로였습니다(RESULTS.md 44절). 0.2는 다시
# 나빠졌습니다(61.2%). `MIMIWATCH_VAD_THRESHOLD`로 되돌릴 수 있습니다.
VAD_THRESHOLD = float(os.environ.get("MIMIWATCH_VAD_THRESHOLD") or 0.3)


def build_vad(min_silence: float = 0.35,
              max_speech: float = 12.0,
              model_file: str | None = None,
              threshold: float | None = None) -> sherpa_onnx.VoiceActivityDetector:
    """발화를 잘라 주는 Silero VAD.

    min_silence는 얼마나 조용해야 발화가 끝났다고 볼지, max_speech는 쉬지
    않고 말할 때 강제로 끊는 길이입니다. 짧게 끊을수록 자막이 빨리 나오지만
    문맥이 짧아지므로, 그 손해는 정제 단계가 되돌립니다.
    """
    vad_model = os.path.join(model_dir(), model_file or VAD_FILE)
    if not os.path.exists(vad_model):
        raise FileNotFoundError(
            f"{os.path.basename(vad_model)}가 없습니다: {vad_model}\n"
            "「엔진 관리 › 모델·도구」에서 받거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=vad_model,
            threshold=VAD_THRESHOLD if threshold is None else threshold,
            min_silence_duration=min_silence,
            min_speech_duration=0.25,
            window_size=WINDOW_SIZE,
            max_speech_duration=max_speech,
        ),
        sample_rate=SAMPLE_RATE,
        num_threads=1,
    )
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)


class AudioHistory:
    """최근 오디오를 들고 있다가 선행 구간과 정제용 원본을 떼어 줍니다."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, keep_s: float = 30.0):
        self.sr = sample_rate
        self.keep = int(keep_s * sample_rate)
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0          # buf[0]이 전체에서 몇 번째 표본인지
        self.last_seg_end = 0    # 선행 구간이 앞 발화를 침범하지 않도록

    def push(self, chunk: np.ndarray):
        self.buf = np.concatenate([self.buf, chunk])
        if len(self.buf) > self.keep:
            drop = len(self.buf) - self.keep
            self.buf = self.buf[drop:]
            self.offset += drop

    def with_preroll(self, seg_start: int, seg_samples: np.ndarray) -> np.ndarray:
        want = max(seg_start - int(PREROLL_S * self.sr),
                   self.last_seg_end, self.offset)
        pre = self.buf[want - self.offset:seg_start - self.offset]
        self.last_seg_end = seg_start + len(seg_samples)
        return seg_samples if len(pre) == 0 else np.concatenate([pre, seg_samples])

    def slice(self, start: int, end: int) -> np.ndarray:
        lo = max(start - int(PREROLL_S * self.sr), self.offset)
        return self.buf[lo - self.offset:end - self.offset].copy()


class Refiner:
    """끝난 발화 무리를 합쳐 다시 해독합니다.

    확정본은 2~4초짜리 조각을 따로따로 해독한 것이라 문맥이 없습니다. 한
    무리가 끝나면 그 구간의 원본 오디오를 통째로 다시 넘겨, 앞뒤를 아는
    상태로 받아 적게 합니다.
    """

    def __init__(self, asr, history: AudioHistory, sink,
                 sample_rate: int = SAMPLE_RATE):
        self.asr = asr
        self.history = history
        self.sink = sink
        self.sr = sample_rate
        self.spans: list[tuple[int, int, str, str]] = []   # start, end, text, speaker
        self._last_refined = ""          # refine_prompt 실험용: 직전 정제본
        # 작업 스레드는 하나입니다. 호출마다 스레드를 띄우면 start() 순서와
        # 실제 실행 순서가 달라져, 무음으로 닫힌 무리와 강제로 닫힌 무리가
        # 뒤바뀐 채 출력됩니다. 큐 하나를 한 소비자가 비우면 넣은 순서가
        # 곧 오디오의 시간 순서이므로 그럴 수 없습니다.
        self._tasks: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            task = self._tasks.get()
            if task is None:                              # close()가 보낸 끝 표시
                self._tasks.task_done()
                return
            try:
                task()
            except Exception as exc:                      # 한 무리의 실패가
                print(f"[refine] 실패: {exc}", flush=True)  # 세션을 죽이면 안 됩니다
            finally:
                self._tasks.task_done()

    def close(self, timeout: float = 10.0):
        """작업 스레드를 끝냅니다. 남은 무리는 마저 처리하고 나옵니다.

        이것이 없던 동안 `_loop`는 영영 `get()`에서 기다렸고, 그 스레드가
        `self`를 쥐고 있으니 `self.asr`(전사 모델 한 벌)·`history`·`sink`가
        세션이 끝난 뒤에도 회수되지 않았습니다. 세션마다 모델이 하나씩
        쌓이는 셈입니다. 세션이 끝나는 자리에서 꼭 부릅니다 -- 두 번 불러도
        됩니다.
        """
        if not self._thread.is_alive():
            return
        self._tasks.put(None)
        self._thread.join(timeout)

    def add_span(self, seg_start: int, seg_end: int, text: str, speaker: str):
        self.spans.append((seg_start, seg_end, text, speaker))

    def maybe_refine(self, now_sample: int, force: bool = False):
        if not self.spans:
            return
        first_start, last_end = self.spans[0][0], self.spans[-1][1]
        due = (force
               or now_sample - last_end >= int(GROUP_GAP_S * self.sr)
               or last_end - first_start >= int(GROUP_MAX_S * self.sr))
        if not due:
            return

        buf = self.history.slice(first_start, last_end)
        speakers = [sp for _, _, _, sp in self.spans if sp]
        speaker = max(set(speakers), key=speakers.count) if speakers else ""
        fast_joined = " ".join(t for _, _, t, _ in self.spans if t.strip())
        self.spans = []
        if len(buf) < self.sr // 2:
            return

        def work():
            # 25초짜리 무리의 재해독은 0.5~1초가 걸립니다. 수신 경로에서
            # 그대로 돌리면 다음 발화의 확정본이 그만큼 늦어지므로 여기서
            # 처리합니다.
            # 정제 패스에만 직전 정제본을 프롬프트로 넘길 수 있습니다(엔진 설정의
            # `refine_prompt`). 고유명사 일관성을 노리는 실험용이고 기본은 꺼져 있습니다.
            kw = {}
            if getattr(self.asr, "refine_prompt", False) and self._last_refined:
                kw["prompt"] = self._last_refined[-200:]
            got = self.asr.transcribe(buf, self.sr,
                                      speech_s=len(buf) / self.sr,
                                      live=False, **kw)
            text = got["text"].strip()
            if len(text) < REFINE_MIN_KEEP * len(fast_joined):
                text = fast_joined
            if not text.strip():
                return
            # forced_lang이 아니라 이번 해독이 알아낸 언어를 씁니다. 「자동
            # 판별」에서는 forced_lang이 빈 문자열이고, 그것을 자막에 실어
            # 보내면 번역기가 원본 언어를 몰라 그냥 돌아섭니다.
            lang = got.get("lang") or self.asr.forced_lang
            tag = f"{speaker}|{lang}" if speaker else lang
            print(f"[refine/{tag}] {text}", flush=True)
            self._last_refined = text
            self.sink.refine(text, lang, speaker)

        self._tasks.put(work)


def run_stream(chunks, vad, asr, sink, history: AudioHistory,
               refiner: Refiner | None = None, speaker_labeler=None,
               sample_rate: int = SAMPLE_RATE):
    """오디오 조각을 받아 VAD로 자르고, 잘린 발화를 받아 적습니다.

    chunks가 ndarray가 아니면 "지금 비우라"는 신호입니다. 방송이 끊겨
    영영 오지 않을 무음을 기다리는 대신 진행 중인 발화를 확정합니다.
    """
    audio_pos = 0.0
    # 해독 한 번이 실패했다고 방송 전체를 놓지 않습니다. 다만 장치가
    # 정말로 죽었으면 계속 시도해 봐야 소용이 없으므로, 연달아 실패하면
    # 그때는 포기합니다. translate.py의 차단기와 같은 생각입니다.
    fails = 0
    for chunk in chunks:
        if not isinstance(chunk, np.ndarray):
            vad.flush()
            fails = _drain(vad, asr, sink, history, refiner, speaker_labeler,
                           sample_rate, fails)
            if refiner is not None:
                refiner.maybe_refine(int(audio_pos * sample_rate), force=True)
            continue

        vad.accept_waveform(chunk)
        history.push(chunk)
        audio_pos += len(chunk) / sample_rate

        fails = _drain(vad, asr, sink, history, refiner, speaker_labeler,
                       sample_rate, fails)
        if refiner is not None and not vad.is_speech_detected():
            refiner.maybe_refine(int(audio_pos * sample_rate))

    # 소리가 끝났습니다(방송 종료, ffmpeg 종료). 걸려 있는 발화를 확정하고
    # 마지막 무리도 정제합니다. 이것이 없으면 마지막 몇 줄은 거친 확정본으로만
    # 남습니다 -- 위의 None 신호와 같은 일을 끝에서 한 번 더 합니다.
    vad.flush()
    _drain(vad, asr, sink, history, refiner, speaker_labeler, sample_rate, fails)
    if refiner is not None:
        refiner.maybe_refine(int(audio_pos * sample_rate), force=True)


# 해독이 연달아 이만큼 실패하면 장치가 죽은 것으로 보고 세션을 놓습니다.
# 한 번의 실패는 그 조각만 버리고 넘어갑니다 -- 라이브에서 한 줄을 잃는
# 것과 방송 전체를 잃는 것은 다른 이야기입니다.
DECODE_FAIL_LIMIT = 5


def _drain(vad, asr, sink, history, refiner, speaker_labeler, sample_rate,
           fails: int = 0) -> int:
    while not vad.empty():
        seg = vad.front
        t0 = time.perf_counter()
        samples = np.asarray(seg.samples, dtype=np.float32)
        seg_start, seg_end = seg.start, seg.start + len(samples)
        raw_speech_s = len(samples) / sample_rate
        samples = history.with_preroll(seg_start, samples)
        vad.pop()

        try:
            result = asr.transcribe(samples, sample_rate, speech_s=raw_speech_s)
        except Exception as exc:
            # GPU 드라이버가 조각 하나에서 넘어지는 일이 있습니다. 예전에는
            # 이 예외가 run_stream을 뚫고 나가 세션을 통째로 끝냈습니다.
            fails += 1
            print(f"[전사 실패 {fails}/{DECODE_FAIL_LIMIT}] "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if fails >= DECODE_FAIL_LIMIT:
                raise
            continue
        fails = 0
        latency_ms = (time.perf_counter() - t0) * 1000
        text = result["text"].strip()
        if not text:
            continue          # 효과음이나 잡음: 자막도 화자도 남기지 않습니다

        speaker = speaker_labeler.label(samples, sample_rate) if speaker_labeler else ""
        print(f"[{speaker + '|' if speaker else ''}{result['lang']}/"
              f"{result.get('tier', '?')}] {text}  "
              f"(seg={len(samples) / sample_rate:.1f}s, "
              f"decode={result['decode_ms']:.0f}ms, latency={latency_ms:.0f}ms)",
              flush=True)
        sink.final(text, result["lang"], speaker)
        if refiner is not None:
            refiner.add_span(seg_start, seg_end, text, speaker)
    return fails
