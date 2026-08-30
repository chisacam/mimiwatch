"""Live sessions: a running broadcast into subtitles the browser can follow.

Built on what the 2026-08-27 measurements settled:

  - yt-dlp resolves a live audio-only HLS rendition, and ffmpeg decodes it to
    the 16kHz mono PCM hayamimi already accepts.
  - The manifest publishes EXT-X-PROGRAM-DATE-TIME and EXT-X-MEDIA-SEQUENCE,
    so each captured sample has a media position -- subtitles can be placed
    on the player's own clock instead of guessed at.
  - The refine pass waits for two seconds of silence, which a fast talker
    never gives: it lagged up to 20s, on 40-50% of lines. So a final is
    published the moment it exists and the refined version REPLACES it later,
    carrying the same id. Waiting for refine would leave the screen empty.
  - Subtitles leave over SSE. hayamimi's WebSocket mirror dropped events
    (92 delivered vs 15 over the same window), so it is not used here.
"""
from __future__ import annotations

import difflib
import json
import queue
import re
from collections import deque
import subprocess
# 이름을 따로 들여옵니다. 시험(bench/live_errors.py)이 `live.subprocess`를
# 가짜로 갈아 끼우는데, 그 가짜에는 예외 클래스가 없습니다.
from subprocess import TimeoutExpired
import sys
import threading
import traceback
import time
import urllib.request
import uuid

import numpy as np

import bus
import config
import store
import stream
import translate as mw_translate
from stream import (SAMPLE_RATE, AudioHistory, Refiner, build_vad,  # noqa: F401
                    run_stream)
from tcpp_asr import build_live_asr

CHUNK = 1600            # 0.1s per VAD feed

# 탭 오디오를 받을 때 큐에 쌓아 둘 최대 길이(초). 전사가 실시간을 못 따라가면
# 여기가 찹니다. 브라우저는 재생을 늦출 수 없으므로 -- 사용자가 실제로 듣고
# 있는 소리입니다 -- 넘치면 가장 오래된 것부터 버리고 몇 초를 버렸는지 적습니다.
# 조용히 밀리다 20분 뒤 자막이 나오는 것보다 낫습니다.
INGEST_MAX_S = 300.0

# hayamimi's 12s force-split suits a single speaker who eventually pauses.
# A multi-speaker broadcast never gives the VAD its 0.35s of silence: on a
# four-way Minecraft collab, 44% of segments ran to the 12s cap, averaging
# 10.1s, and a ten-second stretch of overlapping speech decodes to one short
# line -- which is exactly the "they are talking but no subtitle appears"
# complaint. Splitting sooner produces more, shorter, more accurate finals;
# the refine pass merges them back for the reading panel.
LIVE_MAX_SPEECH = 4.0
LIVE_MIN_SILENCE = 0.30

# How aggressively to force-split, by what the audio actually sounds like.
# Measured on 2026-08-27: a four-way collab at 12s produced 5.0 lines/min
# against the 8.2 its own recording managed, because 44% of segments ran to
# the cap; at 6s it recovered to 7.9. A lecture has real pauses and suffers
# from splitting a sentence in half, so it keeps the longer cap.
PROFILES = {
    "talk":      {"max_speech": 12.0, "min_silence": 0.35,
                  "label": "발표·강연 (한 사람이 문장 사이에 쉼)"},
    "interview": {"max_speech": 6.0,  "min_silence": 0.35,
                  "label": "대담·인터뷰 (번갈아 말하고 쉼이 있음)"},
    "broadcast": {"max_speech": 4.0,  "min_silence": 0.30,
                  "label": "일반 방송 (한두 사람, 쉼이 짧음)"},
    "collab":    {"max_speech": 3.0,  "min_silence": 0.25,
                  "label": "합방·다인 대화 (발화가 겹침)"},
}

# HLS 수신이 끊겼을 때 같은 세션 안에서 다시 붙어 보는 횟수. 사이의 기다림은
# 3초·6초·9초…로 늘어나 다 합쳐 1분 남짓입니다. 그 안에 돌아오지 않으면
# 포기하고 「이어받기」에 맡깁니다 -- 그쪽은 사용자가 시키는 일입니다.
HLS_RECONNECT_TRIES = 5

# 한 조각(CHUNK)의 길이. 링과 시계가 같은 단위를 씁니다.
FRAME_S = CHUNK / SAMPLE_RATE

# 초점이 없는 세션이 들고 있는 최근 소리(초). 멀티뷰에서 다른 방송을 보는 동안에도
# 소리는 계속 받아 두고, 초점이 돌아오면 여기부터 받아 적습니다 -- 전환 직전 몇 초가
# 자막에 남고 첫 줄이 곧 뜹니다. 이보다 오래된 소리는 버립니다.
RING_S = 30.0


class Ring:
    """읽기 스레드와 받아 적는 스레드 사이의 최근 소리.

    예전에는 ffmpeg 을 읽는 일과 VAD·해독이 **한 스레드의 한 for 루프**였습니다. 그러면
    받아 적기를 멈추는 순간 파이프를 아무도 읽지 않아 ffmpeg 이 서고, 멀티뷰처럼
    「소리는 계속 받되 지금은 받아 적지 않는」 세션을 둘 수 없었습니다. 읽기는 여기에
    넣기만 하고, 받아 적는 쪽은 여기서 꺼내기만 합니다.

    항목은 소리 `("audio", recv_s, ndarray)` 와 표식 `("flush",)` `("rebase", base, note)`
    `("end",)` 입니다. 소리는 조각마다 읽기 시계의 값(recv_s)을 물고 있어서, 링에서 한참
    기다렸다 꺼내져도 자막 시각이 그 조각의 진짜 시각이 됩니다. 가득 차면 **소리만**
    오래된 것부터 버리고 표식은 남깁니다 -- 재접속으로 시간 기준이 바뀐 사실까지 버리면
    그 뒤 조각의 시각이 전부 틀어집니다.
    """
    TIMEOUT = object()

    def __init__(self, max_s: float):
        self._d: deque = deque()
        self._cv = threading.Condition()
        self._audio = 0                  # 소리 항목 수
        self.dropped_s = 0.0             # 넘쳐서 버린 소리(초)
        self.max_frames = 1
        self.set_max(max_s)

    def set_max(self, max_s: float):
        with self._cv:
            self.max_frames = max(1, int(round(max_s / FRAME_S)))
            self._trim()
            self._cv.notify_all()

    def _trim(self):
        while self._audio > self.max_frames:
            for i, it in enumerate(self._d):
                if it[0] == "audio":
                    del self._d[i]
                    self._audio -= 1
                    self.dropped_s += FRAME_S
                    break
            else:
                break

    def push(self, item):
        with self._cv:
            self._d.append(item)
            if item[0] == "audio":
                self._audio += 1
                self._trim()
            self._cv.notify_all()

    def full(self) -> bool:
        with self._cv:
            return self._audio >= self.max_frames

    def wait_room(self, timeout: float) -> bool:
        """소리 자리가 나기를 기다립니다. 초점 세션의 읽기 스레드가 씁니다 -- 받아 적기가
        느리면 버리는 대신 여기서 서서, 예전에 ffmpeg 파이프가 차던 것과 같은 역압이 됩니다."""
        with self._cv:
            return self._cv.wait_for(lambda: self._audio < self.max_frames, timeout)

    def pop(self, timeout: float):
        with self._cv:
            if not self._d and not self._cv.wait_for(lambda: self._d, timeout):
                return Ring.TIMEOUT
            it = self._d.popleft()
            if it[0] == "audio":
                self._audio -= 1
            self._cv.notify_all()
            return it

    def seconds(self) -> float:
        with self._cv:
            return self._audio * FRAME_S

# 세션이 들고 있는 최근 이벤트 수(SSE 재접속용). LiveSession.emit 참조.
EVENT_LOG_MAX = 2000
# `_text_of`에서 "기록에 없음"을 "흡수됨(None)"과 구분하는 표식.
_UNKNOWN = object()

_sessions: dict[str, "LiveSession"] = {}
_lock = threading.Lock()
# 프로세스에 **한 세션만** 받아 적습니다. 전사 모델은 한 벌을 나눠 쓰는데(models.py) 같은
# 모델을 두 세션이 동시에 돌리는 것은 바인딩이 보장하지 않습니다(tcpp_asr.py). 멀티뷰의
# 초점이 옮겨 갈 때 옛 세션이 이 토큰을 놓은 뒤에야 새 세션이 잡습니다 -- 그 사이의 소리는
# 새 세션의 링이 들고 있으므로 잃는 것이 없습니다.
_transcriber = threading.Lock()

# 끝난 세션의 마지막 상태는 SQLite가 들고 있습니다. 예전에는 메모리 딕셔너리에
# 최근 20개만 남겨 두었는데, 재시작하면 그마저 사라지는 데다 상한을 넘긴 세션은
# 살아 있는 서버에서도 404가 되었습니다.


_TWITCH_LOGIN = re.compile(r"twitch\.tv/(?!videos/)([A-Za-z0-9_]+)", re.I)
_M3U8 = re.compile(r"\.m3u8(\?|$)", re.I)


def site_of(d: dict, url: str = "") -> dict:
    """yt-dlp `-j` 결과에서 화면이 임베드에 쓰는 것만 골라냅니다.

    화면은 예전에 `video_id` 가 있으면 유튜브 플레이어에 넣었습니다. 이제 트위치와 생
    m3u8 도 받으므로 어느 사이트인지를 서버가 말해 줘야 합니다 -- 트위치의 id 는 숫자
    스트림 번호라 유튜브 플레이어에 넣으면 아무것도 나오지 않습니다.

      site     "youtube" | "twitch" | "other"
      channel  트위치 로그인명(임베드가 이것으로 채널을 찾습니다). 다른 사이트는 빈 값
      video_id yt-dlp 의 id 그대로 (유튜브면 영상 id)
    """
    key = (d.get("extractor_key") or d.get("extractor") or "").lower()
    dom = (d.get("webpage_url_domain") or "").lower()
    vid = d.get("id") or ""
    if key.startswith("youtube") or "youtube" in dom or "youtu.be" in dom:
        return {"site": "youtube", "video_id": vid, "channel": d.get("channel_id") or ""}
    if key.startswith("twitch") or "twitch" in dom or "twitch.tv/" in (url or "").lower():
        login = d.get("uploader_id") or d.get("display_id") or ""
        if not login:
            m = _TWITCH_LOGIN.search(url or "")
            login = m.group(1) if m else ""
        return {"site": "twitch", "video_id": vid, "channel": login.lower()}
    return {"site": "other", "video_id": vid, "channel": ""}


def looks_like_m3u8(url: str) -> bool:
    return bool(_M3U8.search(url or ""))


def resolve_audio(url: str, youtube: bool = True) -> tuple[str, dict]:
    """Audio-only rendition plus what the manifest says about media time."""
    why = []
    # 마지막의 `worst`는 영상이 섞인(muxed) 가장 작은 HLS 입니다. 2026-08에 7주 묵은
    # yt-dlp 가 오디오 전용 포맷을 하나도 못 받아 라이브가 통째로 죽었습니다 -- ffmpeg 은
    # 영상 섞인 스트림에서도 소리만 뽑으므로, 파이프가 조금 굵어질 뿐 받아 적기는 됩니다.
    # 234/233 은 유튜브의 오디오 전용 itag 입니다. 다른 사이트에서는 없는 것을 두 번
    # 물어보느라 몇 초를 쓰므로 건너뜁니다.
    fmts = ("234", "233", "bestaudio", "worst") if youtube else ("bestaudio", "worst")
    for fmt in fmts:
        try:
            out = subprocess.run(stream.ytdlp_args("-f", fmt, "-g", url=url),
                                 capture_output=True, text=True,
                                 timeout=stream.YTDLP_TIMEOUT_S,
                                 **stream.child_io(stderr=False))
        except TimeoutExpired:
            why.append(f"{fmt}: {stream.YTDLP_TIMEOUT_S:.0f}초 안에 답하지 않음")
            continue
        lines = out.stdout.strip().splitlines()
        if out.returncode == 0 and lines:
            return lines[0], manifest_info(lines[0])
        why.append(f"{fmt}: {(out.stderr or '').strip().splitlines()[-1]}"
                   if (out.stderr or "").strip() else f"{fmt}: 빈 결과")
    # yt-dlp가 한 말을 그대로 실어 보냅니다. "해석할 수 없습니다"만으로는
    # 손댈 곳을 알 수 없습니다 -- 판올림이 필요한지, 로그인이 필요한지,
    # 애초에 라이브가 아닌지가 저 줄에 적혀 있습니다.
    ver = ytdlp_version()
    hint = ""
    if all("format is not available" in w for w in why):
        # 셋 다 없다면 특정 포맷이 빠진 것이 아니라 목록을 통째로 못 받은
        # 것입니다. 거의 언제나 yt-dlp가 낡아서입니다.
        hint = (f" — 포맷을 하나도 받지 못했습니다. yt-dlp({ver or '판 미상'})가 "
                f"낡았을 수 있습니다"
                + (" (45일 넘음)" if ytdlp_stale(ver) else "")
                + ". 「엔진 관리 › 모델·도구」에서 yt-dlp 독립 실행 파일을 (다시) 받거나 "
                  "`yt-dlp --update-to nightly` 로 올린 뒤 다시 해 보십시오.")
    raise RuntimeError("yt-dlp가 이 주소에서 오디오를 찾지 못했습니다."
                       + hint + " [" + " / ".join(why) + "]")


def ytdlp_version() -> str:
    """설치된 yt-dlp의 판. 못 물으면 빈 문자열."""
    try:
        out = subprocess.run(stream.ytdlp_cmd() + ["--version"], capture_output=True,
                             text=True, timeout=20,
                             **stream.child_io(stderr=False))
        return (out.stdout or "").strip().splitlines()[0] if out.returncode == 0 else ""
    except Exception:
        return ""


def ytdlp_stale(version: str, days: int = 45) -> bool:
    """이 판이 낡았는가.

    yt-dlp의 판은 YYYY.MM.DD입니다. 유튜브가 추출 경로를 자주 바꾸고
    yt-dlp가 그때마다 따라가므로, 몇 달 지난 판은 포맷 목록을 통째로 받지
    못하는 일이 흔합니다. 기준을 90일에서 45일로 낮췼습니다 -- 2026-08 실측에서
    7주 전 판이 이미 라이브 오디오 포맷을 하나도 받지 못했습니다. 그러면 234도
    233도 bestaudio도 전부 "Requested format is not available"이 됩니다 -- 포맷이
    없는 것이 아니라 아무것도 못 읽은 것입니다.
    """
    try:
        y, m, d = (int(x) for x in version.split(".")[:3])
        from datetime import date
        return (date.today() - date(y, m, d)).days > days
    except Exception:
        return False


def manifest_info(m3u8: str) -> dict:
    """Where in the broadcast the playlist starts, and how long it is.

    MEDIA-SEQUENCE is NOT a media position. YouTube serves some live streams
    with a full-DVR playlist: 2496 one-second segments covering the whole
    41 minutes so far, numbered from 0. Multiplying sequence by segment
    length gave 0 there and looked like a parse failure, when the real
    problem was that the playlist -- and therefore ffmpeg -- starts at the
    beginning of the broadcast rather than at its live edge.

    So the window length is what matters: it says how far behind live the
    first segment sits, which is what `-live_start_index` has to skip.
    """
    info = {"seq": None, "target": None, "pdt": None,
            "segments": 0, "window_s": 0.0, "media_base": 0.0,
            # #EXT-X-ENDLIST 가 있으면 끝난 재생목록(녹화본)입니다. 라이브인지 모르는(other)
            # 주소가 이것을 달고 있으면 ffmpeg 이 끝난 뒤 다시 붙을 것이 없습니다.
            "ended": False}
    try:
        with urllib.request.urlopen(m3u8, timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return info
    durations = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            info["seq"] = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            info["target"] = float(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:") and info["pdt"] is None:
            info["pdt"] = line.split(":", 1)[1].strip()
        elif line.startswith("#EXTINF:"):
            try:
                durations.append(float(line.split(":", 1)[1].rstrip(",").split(",")[0]))
            except ValueError:
                pass
        elif line.startswith("#EXT-X-ENDLIST"):
            info["ended"] = True
    info["segments"] = len(durations)
    info["window_s"] = sum(durations)
    return info


def media_base_from(pdt: str | None, release_ts: float | None,
                    window_s: float) -> float:
    """Media position of the audio we are about to receive.

    PDT is the wall clock of the playlist's first segment and
    release_timestamp is when the broadcast began, so their difference is
    the media offset. We skip to the live edge, so add the window we chose
    not to read.
    """
    if not pdt or not release_ts:
        return 0.0
    try:
        stamp = pdt.replace("Z", "+00:00")
        first = __import__("datetime").datetime.fromisoformat(stamp).timestamp()
    except Exception:
        return 0.0
    return max(0.0, (first - release_ts) + window_s)


# 정제본이 어떤 확정 줄을 흡수했는지 가릴 때 앞 글자가 그대로 들어 있는지로
# 보면 안 됩니다. 정제는 합친 오디오를 다시 해독하므로 같은 말이라도 글자가
# 조금 달라집니다(실측: `무기도 풀제열이야?`가 `무기도 풀제일이야?`가 됨).
# 그러면 흡수 판정이 빗나가 거친 확정본과 정제본이 나란히 남습니다.
# 화면에서는 눈에 덜 띄지만 저장분에 둘 다 쌓여, 새로고침하면 같은 발화가
# 두 번 나옵니다. 그래서 글자 일치가 아니라 겹치는 정도로 봅니다.
COVER_RATIO = 0.6
# 정제는 한 무리의 발화를 합쳐 다시 해독한 것이므로, 그 무리보다 훨씬 오래된
# 줄까지 거슬러 올라가 흡수할 일은 없습니다.
COVER_WINDOW_S = 30.0
# 짧은 줄은 유사도로 보면 안 됩니다. `はい` 두 글자는 어떤 정제본에나 들어
# 있어서, 느슨하게 보면 관계없는 것까지 삼킵니다.
COVER_EXACT_BELOW = 4
# 무리 한가운데에서 못 알아본 줄이 이만큼까지 이어져도 같은 무리로 봅니다.
COVER_GAP = 2


def _covers(final_text: str, refined: str) -> bool:
    """정제본이 이 확정 줄을 담고 있는가."""
    a = final_text.strip()
    if not a:
        return False
    if len(a) < COVER_EXACT_BELOW:
        return a in refined
    matched = sum(b.size for b in
                  difflib.SequenceMatcher(None, a, refined).get_matching_blocks())
    return matched / len(a) >= COVER_RATIO


class Sink:
    """전사 루프가 내놓는 줄을 세션의 발행 경로로 넘깁니다."""

    def __init__(self, session: "LiveSession"):
        self.s = session

    def final(self, text: str, lang: str = "", speaker: str = ""):
        self.s.publish_line("final", text, lang, speaker)

    def refine(self, text: str, lang: str = "", speaker: str = ""):
        self.s.publish_line("refine", text, lang, speaker)


class LiveSession:
    def __init__(self, url: str, lang: str | None, viewer_lang: str,
                 backend_id: str, profile: str = "broadcast",
                 asr_backend_id: str = "", refine: bool = True,
                 genre: str | None = None, source: str = "hls",
                 title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        # 소리를 어디서 받는가. "hls"는 서버가 yt-dlp로 주소를 풀어 ffmpeg으로
        # 직접 당기고, "tab"은 브라우저가 자기 탭에서 들리는 소리를 올려 줍니다.
        # 멤버십 전용 방송처럼 서버가 받을 수 없는 것을 위한 길입니다 -- 쿠키도
        # 필요 없고, 사용자가 공유 대화상자에서 직접 고른 탭입니다.
        self.source = "tab" if source == "tab" else "hls"
        self.lang = lang
        self.viewer_lang = viewer_lang
        self.backend_id = backend_id
        self.asr_backend_id = asr_backend_id
        # 프로필(콘텐츠 유형)은 발화를 몇 초에 끊을지를 정하고, 장르는 그
        # 발화를 어떤 어휘로 옮길지를 정합니다. 겹쳐 보이지만 다른 축입니다 --
        # 게임 방송과 잡담 방송은 끊는 간격이 같아도 쓰는 말이 다르고,
        # 기술 발표는 녹화본에도 있습니다.
        self.genre = genre if genre in mw_translate.GENRE_PROMPTS \
            else mw_translate.DEFAULT_GENRE
        # 정제는 발화 한 무리가 끝나기를 2초 기다렸다 합쳐서 다시 해독합니다.
        # 문맥이 길어져 결과가 좋아지지만, 그만큼 자막이 늦게 자리를 잡고
        # 이미 뜬 줄이 통째로 바뀝니다. 말이 빠르게 오가는 방송에서는 짧게
        # 끊어 바로 내보내는 편이 따라가기 쉬울 수 있어 끌 수 있게 둡니다.
        self.refine = refine
        prof = PROFILES.get(profile, PROFILES["broadcast"])
        self.profile = profile if profile in PROFILES else "broadcast"
        self.max_speech = prof["max_speech"]
        self.min_silence = prof["min_silence"]
        self.state = "starting"
        self.error: str | None = None
        # 왜 멈췄는가. "user"는 「중단」을 눌렀거나 서버를 끈 것, "ended"는
        # 방송이 끝난 것, "stream"은 수신이 끊겼는데 다시 붙지 못한 것입니다.
        # 셋이 다 `stopped`로 보이면 화면은 이어받기를 권할지 정할 수 없습니다.
        self.stopped_by = ""
        # 이어받을 때, 끊기기 직전까지 받아 둔 미디어 위치입니다. 0이면
        # 새로 시작하는 세션이라 되감을 것이 없습니다.
        self.resume_from = 0.0
        # resume_from 까지 되감아 메울지. 세션 안의 자동 재접속만 그렇게 합니다 -- 끊긴 지
        # 몇 초라 DVR 창 안에서 바로 이어집니다. 사용자의 「이어받기」는 되감지 않습니다:
        # 한 시간 멈춘 뒤 이어받으면 한 시간치를 먼저 받아 적느라 지금 보는 자리의 자막이
        # 한참 뒤에나 나왔습니다. 지금 라이브 끝에서 시작하고 빠진 구간은 한 줄로 적습니다.
        self._rewind = False
        # 메우지 못한 구간(초). 0보다 크면 자막에 그렇게 적습니다.
        self.gap_s = 0.0
        # hls는 yt-dlp가 채우고, tab은 브라우저가 넣어 줍니다.
        self.title = title
        # 재시작 뒤 이 세션을 다시 열려면 임베드할 영상 id가 필요합니다.
        # 세션 id는 우리가 만든 것이라 플레이어에 넣을 수 없습니다.
        self.video_id = ""
        self.media_base = 0.0        # media seconds at the first sample we get
        self.window_s = 0.0          # DVR window we skipped to reach live
        self.audio_s = 0.0           # seconds fed so far
        self.started = time.time()
        self.lines = 0
        self.translated = 0
        self._seq = 0
        self._subs: list[queue.Queue] = []
        # 나간 이벤트의 최근 기록. 끊겼다 다시 붙는 구독자가 `Last-Event-ID`를
        # 들고 오면 여기서 빠진 것만 다시 보냅니다 -- 백로그 전부가 아니라.
        # 2000개면 자막 몇 백 줄에 번역·상태까지 넉넉히 한 시간 남짓입니다.
        self._eseq = 0
        self._elog: deque[tuple[int, str]] = deque(maxlen=EVENT_LOG_MAX)
        self._stop = threading.Event()
        # 발행 경로의 자물쇠. 확정 줄은 수신 스레드가, 정제본은 정제 스레드가
        # 넣습니다 -- 둘이 동시에 `_recent`를 고치면 한쪽의 `remove`가 다른
        # 쪽의 `del [:-40]`과 엉켜 ValueError가 나거나 엉뚱한 줄을 흡수합니다.
        self._pub_lock = threading.RLock()
        self._ff: subprocess.Popen | None = None
        # 읽는 쪽(ffmpeg 스레드, 탭이면 feed())과 받아 적는 스레드 사이. Ring 참조.
        # 시계는 둘입니다 -- `_recv_*` 는 읽는 쪽이 **받은** 자리, `media_base`/`audio_s`
        # 는 받아 적는 쪽이 **지금 해독하는 조각**의 자리입니다. 혼자 받는 세션에서는
        # 둘이 같이 가지만, 링에 소리가 고여 있으면 뒤쪽이 앞쪽보다 늦습니다.
        #
        # 탭 소리는 브라우저가 재생을 늦출 수 없으므로(사용자가 듣고 있는 소리입니다)
        # 받아 적기가 밀리면 링을 길게(INGEST_MAX_S) 잡고, 넘치면 가장 오래된 것부터
        # 버리며 몇 초를 버렸는지 적습니다. 조용히 밀리다 20분 뒤 자막이 나오는
        # 것보다 낫습니다. 주소 세션은 파이프가 역압을 주므로 링이 넘칠 일이 없습니다.
        self._ring = Ring(INGEST_MAX_S if self.source == "tab" else RING_S)
        self._recv_base = 0.0
        self._recv_s = 0.0
        self.dropped_s = 0.0        # 링이 넘쳐 버린 소리(초). 탭 세션이 feed() 로 되돌려 줍니다
        self._ended = False         # 읽기가 끝났다(방송 종료·포기). 링의 ("end",) 와 함께
        self._rx: threading.Thread | None = None
        # 초점: 이 세션의 소리를 지금 받아 적는가. 혼자 받는 세션은 태어날 때부터 초점이고
        # 멀티뷰에서만 꺼집니다(set_focus). 초점이 없는 동안에도 링은 채워집니다.
        self._focus = threading.Event()
        self._focus.set()
        self.group = ""             # 멀티뷰 묶음 id. 비면 혼자 받는 세션
        self.site = ""              # "youtube" | "twitch" | "other" (site_of). 화면이 임베드를 고름
        self.channel = ""           # 트위치 로그인명
        self._warm_persisted_s = 0.0   # 대기 중 마지막으로 상태를 적었을 때의 _recv_s
        self._asr = None            # released on stop; see _release()
        # 인식기 객체는 세션이 끝나면 놓아주지만 어떤 엔진이었는지는
        # 남아야 합니다. 객체에서 그때그때 읽으면, 놓아준 뒤에 쓰이는
        # 마지막 상태 저장이 기본값으로 덮어써서 기록이 거짓말을 합니다.
        self.asr_label = ""
        self._tr = None
        # Refine replaces the final that covered the same speech. Matching on
        # the text hayamimi already emitted is enough here because a refined
        # group repeats its members' words.
        self._recent: list[dict] = []
        # 번역은 **작업 스레드 하나**가 넣은 순서대로 합니다.
        #
        # 예전에는 자막 한 줄마다 스레드를 새로 띄웠습니다. 모델 자물쇠가
        # 어차피 하나라 나란히 돌 수도 없었고, 두 시간 방송이면 스레드가
        # 수천 번 만들어졌으며, 무엇보다 **끝나는 순서가 정해지지 않았습니다.**
        # 정제본은 흡수한 첫 확정 줄의 id를 물려받는데, 그 확정 줄의 번역
        # 스레드가 정제본의 번역보다 늦게 끝나면 같은 id로 옛 부분 번역이
        # 발행·저장되어 정제본 아래에 엉뚱한 번역이 남았습니다. 큐 하나를
        # 한 소비자가 비우면 순서가 곧 발행 순서이고, 아래 `_text_of`로
        # 이미 덮인 줄의 번역은 건너뜁니다 -- Gemma 시간도 그만큼 아낍니다.
        self._tr_q: queue.Queue = queue.Queue()
        self._tr_thread: threading.Thread | None = None
        # id별 지금 화면에 있는 원문. 번역이 끝났을 때 그 줄이 아직 이 글자인지
        # 확인하는 데 씁니다. 정제본이 흡수한 줄은 여기서 빠지고, 물려받은
        # id는 정제본의 글자로 바뀝니다.
        self._text_of: dict[int, str] = {}

    # ---- fan-out ----------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with _lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with _lock:
            if q in self._subs:
                self._subs.remove(q)

    def emit(self, event: dict):
        data = json.dumps(event, ensure_ascii=False)
        with _lock:
            self._eseq += 1
            self._elog.append((self._eseq, data))
            for q in self._subs:
                q.put((self._eseq, data))

    def replay_since(self, last_id) -> list[tuple[int, str]] | None:
        """`last_id` 뒤에 나간 이벤트. 기록 밖이면 None -- 그때는 백로그 전부."""
        try:
            last = int(last_id)
        except (TypeError, ValueError):
            return None
        with _lock:
            if not self._elog or last < self._elog[0][0] - 1 or last > self._eseq:
                return None
            return [(seq, data) for seq, data in self._elog if seq > last]

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error,
                "title": self.title, "url": self.url, "video_id": self.video_id,
                "source": self.source,
                "source_lang": self.lang, "viewer_lang": self.viewer_lang,
                "backend": self.backend_id,
                "asr_backend": self.asr_backend_id,
                "refine": self.refine,
                "asr": self.asr_label,
                "stopped_by": self.stopped_by,
                "media_base": round(self.media_base, 2),
                "profile": self.profile, "max_speech": self.max_speech,
                "genre": self.genre,
                "window_s": round(self.window_s, 1),
                "audio_s": round(self.audio_s, 1),
                # 받아 적은 자리(media_base+audio_s)와 따로, 읽는 쪽이 받은 자리. 초점이
                # 없는 세션은 앞쪽이 멈춰 있어도 뒤쪽은 계속 나아갑니다. 이어받기는 뒤쪽에서.
                "recv_s": round(self._recv_s, 1),
                "recv_t": round(self._recv_base + self._recv_s, 2),
                "ring_s": round(self._ring.seconds(), 1),
                "focused": self._focus.is_set(), "group": self.group,
                "site": self.site, "channel": self.channel,
                "elapsed": round(time.time() - self.started, 1),
                "lines": self.lines, "translated": self.translated}

    def _persist(self):
        st = self.status()
        store.save_session(st, self.video_id)
        # 목록을 보고 있는 모든 화면에 알립니다 -- 새 세션, 줄 수, 상태 변화.
        # 자막 한 줄마다 오지만 화면은 그 줄만 제자리에서 고치므로 가볍습니다.
        bus.publish({"type": "session", **st})

    # ---- publishing -------------------------------------------------------
    def publish_line(self, kind: str, text: str, lang: str, speaker: str):
        text = (text or "").strip()
        if not text:
            return
        with self._pub_lock:
            self._publish_locked(kind, text, lang, speaker)

    def _publish_locked(self, kind: str, text: str, lang: str, speaker: str):
        media_t = self.media_base + self.audio_s
        if kind in ("final", "note"):
            self._seq += 1
            self.lines += 1
            cue = {"type": "cue", "id": self._seq, "kind": kind,
                   "t": round(media_t, 2), "text": text,
                   "lang": lang, "speaker": speaker}
            self._recent.append(cue)
            del self._recent[:-40]
            self._text_of[cue["id"]] = text
            self._trim_text_of()
            store.save_cue(self.id, cue)
            self.emit(cue)
            # 줄 수는 상태에 들어 있으므로 자막 한 줄마다 상태도 같이 적습니다.
            # 몇 초에 한 번이라 비용이 없고, 어디까지 받아 적었는지가 재시작
            # 뒤에 정확해집니다.
            self._persist()
            self._translate_async(cue)
        else:
            # A refined group supersedes the finals whose words it contains.
            # 정제는 한 무리의 발화를 합친 것이므로, 흡수 대상도 그 무리처럼
            # 이어져 있어야 합니다. 아무 데서나 고르면 `はい` 같은 짧은
            # 맞장구가 한참 전 것까지 걸려, 정제본이 그 옛 줄의 시각과 id를
            # 물려받아 과거 자막 자리에 끼어듭니다.
            #
            # 다만 꼬리에서부터 훑으면 안 됩니다. 정제는 무음 2초를 기다렸다
            # 오므로 그 사이에 다음 발화의 확정본이 먼저 들어와 있고, 거기서
            # 멈춰 버리면 아무것도 흡수하지 못해 같은 말이 두 줄로 남습니다.
            # 그래서 위치에 관계없이 가장 긴 연속 구간을 찾습니다.
            hits = [i for i, c in enumerate(self._recent)
                    if c["kind"] == "final"
                    and media_t - c["t"] <= COVER_WINDOW_S
                    and _covers(c["text"], text)]
            # 걸린 줄들을 덩어리로 묶습니다. 무리 한가운데 한둘이 판정에서
            # 빠지는 일은 흔합니다 -- 정제 재해독에서 글자가 크게 갈리면
            # (`いや空込みだ`가 `川上だ`가 되는 식) 그 줄만 못 알아봅니다.
            # 거기서 무리를 쪼개면 한쪽만 흡수되고 나머지가 중복으로 남으므로,
            # 그 정도 틈은 건너뜁니다.
            groups: list[list[int]] = []
            for i in hits:
                if groups and i - groups[-1][-1] <= COVER_GAP + 1:
                    groups[-1].append(i)
                else:
                    groups.append([i])
            best = max(groups, key=len) if groups else []
            # 가장 큰 덩어리는 그 안의 빠진 줄까지 통째로 대체합니다. 정제본은
            # 무리 하나를 통째로 다시 받아 적은 것이니까요.
            covered = ([self._recent[i] for i in range(best[0], best[-1] + 1)]
                       if best else [])
            target = covered[0] if covered else None
            if target is None:
                # 흡수할 확정 줄을 하나도 못 찾았습니다(재해독에서 글자가 크게
                # 갈렸거나, 그 줄들이 이미 `_recent` 밖으로 밀려났거나). 이때
                # 예전에는 `self._seq` -- 곧 **가장 최근 줄의 번호** -- 를 그대로
                # 썼는데, 그 줄은 이 정제본과 무관한 다음 발화일 수 있습니다.
                # 그러면 그 발화의 원문이 덮이고 번역까지 비워졌습니다. 짝을
                # 못 찾은 정제본은 새 줄로 넣습니다.
                self._seq += 1
                self.lines += 1
            cue = {"type": "cue", "id": target["id"] if target else self._seq,
                   "kind": "refine", "t": round(target["t"] if target else media_t, 2),
                   "text": text, "lang": lang, "speaker": speaker,
                   "replaces": [c["id"] for c in covered]}
            for c in covered:
                self._recent.remove(c)
                self._text_of[c["id"]] = None      # 흡수됨. 번역 대기열의 그 줄은 버립니다
            self._text_of[cue["id"]] = text
            # 정제본이 흡수한 줄은 화면에서 사라지므로 저장분에서도 지웁니다.
            # 자기 id를 물려받은 한 줄만 남기고 그 자리를 정제본으로 덮습니다.
            store.drop_cues(self.id, [c["id"] for c in covered
                                      if c["id"] != cue["id"]])
            store.save_cue(self.id, cue)
            self.emit(cue)
            self._translate_async(cue)

    def _context_for(self, cue: dict) -> list[str]:
        """이 줄 직전의 자막 몇 줄. 번역기에 참고로 넘깁니다.

        `self._recent`에서 **이 줄보다 이른 것만** 고릅니다. 확정 줄이면
        방금 자기 자신이 맨 뒤에 붙어 있고, 정제본이면 흡수한 줄들이 이미
        빠진 대신 기다리는 동안 들어온 뒤 줄이 남아 있습니다. 시각으로
        거르면 두 경우가 한 규칙으로 처리됩니다.
        """
        # 우리가 적은 안내(kind=note)는 발화가 아닙니다. 문맥에 넣으면
        # 번역기가 「서버가 멈춘 사이 …」를 앞 문장으로 알고 옮깁니다.
        older = [c["text"] for c in self._recent
                 if c["t"] < cue["t"] and c.get("kind") != "note"]
        return older[-mw_translate.CONTEXT_LINES:]

    def _trim_text_of(self, keep: int = 500):
        """`_text_of`가 방송 길이만큼 자라지 않게 합니다. 번역은 발행 직후
        줄을 서므로 몇백 줄 뒤의 것을 다시 볼 일은 없습니다.

        잘려 나간 줄은 `_superseded`가 **모른다**고 답하고, 모르는 줄은 번역합니다.
        예전에는 "없음"을 "흡수됨"으로 읽어, 번역기가 500줄 넘게 밀린 느린 기계에서
        그 줄들이 영영 번역되지 않았습니다. 흡수된 줄은 None 으로 남겨 구분합니다.
        """
        if len(self._text_of) > keep * 2:
            for k in sorted(self._text_of)[:-keep]:
                del self._text_of[k]

    def _translate_async(self, cue: dict):
        # 우리가 적은 안내입니다. 번역기에 넘길 것이 아닙니다.
        if cue.get("kind") == "note":
            return
        if self.lang and self.lang == self.viewer_lang:
            return
        # 문맥은 여기서 붙잡습니다. 번역이 차례를 기다리는 사이에도 자막은
        # 계속 들어오므로, 작업 스레드 안에서 읽으면 그때의 `_recent`는 이
        # 줄의 앞이 아닙니다.
        ctx = self._context_for(cue)
        self._tr_q.put((dict(cue), ctx))
        if self._tr_thread is None or not self._tr_thread.is_alive():
            self._tr_thread = threading.Thread(target=self._translate_loop,
                                               daemon=True, name=f"tr-{self.id}")
            self._tr_thread.start()

    def _translate_loop(self):
        while True:
            item = self._tr_q.get()
            if item is None:                      # _release()가 보낸 끝 표시
                return
            cue, ctx = item
            try:
                self._translate(cue, ctx)
            except Exception as exc:              # 한 줄의 실패가 뒤 줄을 막으면 안 됩니다
                print(f"[live] 번역 루프 오류: {exc}", file=sys.stderr, flush=True)

    def _superseded(self, cue: dict) -> bool:
        """이 줄이 그 사이 정제본에 흡수되었거나 글자가 바뀌었는가. 기록에서 잘려 나가
        모르는 줄은 아직 살아 있는 것으로 봅니다."""
        cur = self._text_of.get(cue["id"], _UNKNOWN)
        return cur is not _UNKNOWN and cur != cue["text"]

    def _translate(self, cue: dict, context: list[str] | None = None):
        src = cue.get("lang") or self.lang or ""
        if not src or src == self.viewer_lang:
            return
        # 차례를 기다리는 동안 정제본이 이 줄을 흡수했으면 번역할 것이 없습니다.
        # 정제본 자신의 번역이 뒤에 줄 서 있습니다.
        if self._superseded(cue):
            return
        # 한 번 붙잡아 둡니다. 세션이 끝나면 `_release()`가 `_tr`를 None으로
        # 놓는데, 큐에 남은 줄은 그 전에 비웁니다(`_close_translator`).
        tr = self._tr
        if tr is None:
            return
        if not tr.should_translate(cue["text"], src, self.viewer_lang):
            return
        try:
            out = tr.translate(cue["text"], src, self.viewer_lang, context)
        except Exception as exc:
            # 실패했다고 줄을 버리지 않습니다. 그러면 시청자에게는 그 발화가
            # 아예 없었던 것처럼 보입니다. 번역할 수 없었다는 사실이 남도록
            # 원문을 그 자리에 넣고, 왜 실패했는지는 로그에 적습니다.
            print(f"[live] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
            out = cue["text"]
        if not (out or "").strip():
            return
        # 번역하는 몇백 밀리초 사이에 정제본이 들어왔을 수 있습니다. 그러면
        # 이 결과는 이미 화면에 없는 글자의 번역이고, 같은 id를 물려받은
        # 정제본의 번역을 덮어쓰게 됩니다 -- 버립니다.
        if self._superseded(cue):
            return
        # 번역이 원문과 같아도 저장합니다. 고유명사나 짧은 감탄사는 그대로
        # 두는 것이 옳은 번역이고, 예전에는 이 경우를 실패로 보아 줄이
        # 사라졌습니다.
        self.translated += 1
        store.save_translation(self.id, cue["id"], self.backend_id, out)
        self.emit({"type": "translation", "id": cue["id"],
                   "kind": cue["kind"], "text": out})

    # ---- pipeline ---------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def set_focus(self, on: bool):
        """이 세션의 소리를 받아 적을지. 꺼도 세션은 살아서 소리를 계속 받습니다(링).

        켜면 받아 적는 스레드가 토큰(_transcriber)을 잡고 링에 고인 것부터 해독합니다.
        끄면 지금 도는 run_stream 이 걸린 발화를 확정하고 물러납니다 -- 그 뒤 토큰이 풀립니다.
        """
        if on == self._focus.is_set():
            return
        if on:
            self._focus.set()
        else:
            self._focus.clear()
        print(f"[live] 세션 {self.id} 초점 {'켬' if on else '끔'} "
              f"(링 {self._ring.seconds():.1f}초, 받은 {self._recv_s:.0f}초, "
              f"받아 적은 {self.audio_s:.0f}초)",
              flush=True)
        if self.source == "tab":
            # 탭 소리는 브라우저가 늦출 수 없어 초점일 때는 길게 받아 둡니다. 초점이 아니면
            # 그만큼 들고 있을 이유가 없습니다 -- 돌아왔을 때 30초면 충분합니다.
            self._ring.set_max(INGEST_MAX_S if on else RING_S)
        self._persist()
        self.emit({"type": "status", **self.status()})

    def stop(self):
        self.stopped_by = self.stopped_by or "user"
        self._stop.set()
        if self._ff:
            self._ff.terminate()
        # 탭 세션에는 읽기 스레드가 없어 아무도 ("end",) 를 넣어 주지 않습니다. 받아
        # 적는 쪽이 링에서 기다리고 있으므로 여기서 직접 깨웁니다.
        if self.source == "tab":
            self._ended = True
            self._ring.push(("end",))

    # ---- 탭 오디오 수신 ---------------------------------------------------
    def feed(self, raw: bytes) -> dict:
        """브라우저가 올린 16kHz 모노 int16 PCM 한 덩어리. 탭 세션의 「읽기」는 이것입니다.

        VAD가 받는 0.1초 조각으로 잘라 링에 넣습니다 -- ffmpeg 경로와 같은 크기입니다.
        run_stream은 조각 하나마다 VAD를 먹이고 정제 시점을 재므로, 2초를 통째로
        넘기면 그 두 가지가 같이 거칠어집니다. 링이 넘치면 오래된 것부터 버리고
        (기다리지 않습니다 -- 이 요청은 브라우저가 기다리고 있습니다) 몇 초를 버렸는지
        되돌려 줍니다.
        """
        if self.source != "tab":
            return {"error": "이 세션은 탭 오디오를 받지 않습니다"}
        if self._stop.is_set() or self.state in ("stopped", "error"):
            return {"error": "세션이 끝났습니다", "state": self.state}
        need = CHUNK * 2
        for off in range(0, len(raw) - need + 1, need):
            block = np.frombuffer(raw, dtype=np.int16, count=CHUNK,
                                  offset=off).astype(np.float32) / 32768.0
            self._recv_s += FRAME_S
            self._ring.push(("audio", self._recv_s, block))
        self.dropped_s = self._ring.dropped_s
        return {"ok": True, "state": self.state,
                "queued_s": round(self._ring.seconds(), 1),
                "dropped_s": round(self.dropped_s, 1)}

    def _resume_point(self, info: dict, release_ts: float | None):
        """끊긴 자리에서 다시 받으려면 재생목록의 어디부터 읽어야 하는가.

        유튜브는 지금 진행 중인 방송도 얼마간 되감을 수 있게 내어 줍니다
        (DVR 창). 서버가 멈춘 사이가 그 창 안이면 **한 조각도 잃지 않고**
        이어 붙일 수 있습니다. 창보다 오래 멈춰 있었으면 메우지 못한 만큼을
        `gap_s`에 남겨, 화면에 그렇게 적습니다.

        돌려주는 것은 (ffmpeg의 -live_start_index, 건너뛴 초)입니다.
        """
        first = media_base_from(info.get("pdt"), release_ts, 0.0)
        segs = int(info.get("segments") or 0)
        seg_dur = (self.window_s / segs) if segs else float(info.get("target") or 2.0)
        want = self.resume_from - first        # 재생목록 앞에서 몇 초를 건너뛸까

        if seg_dur <= 0 or self.window_s <= 0:
            # 창을 읽지 못했습니다. 되감기를 시도하지 않고 라이브 끝에서
            # 받되, 얼마를 잃었는지는 알 수 없으므로 적지 않습니다.
            return -2, self.window_s
        if want <= 0:
            # 우리가 멈춘 지점이 이미 창 밖으로 밀려났습니다. 남아 있는
            # 가장 오래된 것부터 받고, 그 사이는 잃은 것으로 적습니다.
            self.gap_s = max(0.0, -want)
            return 0, 0.0
        if want >= self.window_s:
            # 창 안에서 못 메울 것이 없습니다 -- 우리가 멈춘 지점이 아직
            # 라이브 끝보다 뒤이므로 그냥 끝에서 이어 받습니다.
            self.gap_s = 0.0
            return -2, self.window_s
        idx = max(0, int(want / seg_dur))
        self.gap_s = 0.0
        return idx, idx * seg_dur

    def _release(self):
        """이 세션이 쥔 것을 놓습니다.

        모델 가중치는 이제 `models.py`가 프로세스에 한 벌만 들고 있으므로
        여기서 놓는 것은 이 세션의 해독 세션·번역기 껍데기·최근 줄입니다.
        예전에는 세션마다 모델을 새로 올렸고 끝난 세션이 등록부에 남아 그것을
        붙잡았습니다 -- 한 오후에 일곱 세션으로 23GB까지 갔습니다. 그 문제는
        모델을 공유하는 것으로 뿌리에서 없어졌고, 여기는 참조를 끊는 자리로
        남습니다.
        """
        # 읽기 스레드가 링에 자리가 나기를 기다리고 있을 수 있습니다. 받아 적는 쪽이
        # 없어졌으니 그 기다림도 끝내야 합니다 -- 이 깃발이 그 루프의 탈출 조건입니다.
        self._stop.set()
        self._asr = None
        self._close_translator()
        self._tr = None
        self._recent.clear()
        if self._ff:
            try:
                self._ff.kill()
            except Exception:
                pass
            self._ff = None

    def _close_translator(self, timeout: float = 10.0):
        """번역 작업 스레드를 끝냅니다. 줄 서 있는 것은 마저 번역하고 나옵니다.

        마지막 몇 줄의 번역이 세션이 끝났다는 이유로 사라지면, 방송 끝의
        인사가 원문으로만 남습니다. 대신 한없이 기다리지는 않습니다 -- 번역기가
        멎어 있으면 10초 뒤 그냥 놓습니다.
        """
        t = self._tr_thread
        if t is None or not t.is_alive():
            return
        self._tr_q.put(None)
        t.join(timeout)

    def _spawn_ffmpeg(self, src: str, start_index):
        # -live_start_index -2 starts two segments from the end of the
        # playlist. Without it ffmpeg reads a full-DVR playlist from the top
        # and transcribes the broadcast's opening greetings while the viewer
        # watches its live edge.
        # `-nostdin`: 소리는 우리가 stdout 파이프로 받아 갑니다. ffmpeg 이
        # 표준 입력을 들여다볼 일이 없고, 터미널에서 돌 때 키 입력을 가져가는
        # 것도 막습니다. 표준 입출력은 stream.child_io 를 보십시오 -- 부모의
        # 핸들을 물려주다 윈도우에서 넘어지던 자리입니다.
        self._ff = subprocess.Popen(
            [stream.ffmpeg_cmd(), "-loglevel", "error", "-nostdin",
             "-live_start_index", str(start_index), "-i", src,
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, **stream.child_io())

    def _start_reader(self, src, start_index):
        """ffmpeg 을 세우고 그것을 읽는 스레드를 띄웁니다. 탭 세션은 `feed()` 가
        읽기 역할이라 여기서 할 일이 없습니다."""
        if self.source == "tab":
            return
        self._spawn_ffmpeg(src, start_index)
        self._rx = threading.Thread(target=self._read_loop, name=f"rx-{self.id}",
                                    daemon=True)
        self._rx.start()

    def _push_audio(self, samples):
        """읽은 조각을 링에 넣습니다. 초점 세션이면 자리가 날 때까지 기다립니다 --
        받아 적기가 느려도 버리지 않는 것이 예전 파이프의 동작이었습니다. 초점이
        없으면 링이 오래된 것부터 버립니다."""
        item = ("audio", self._recv_s, samples)
        while (self._ring.full() and self._focus.is_set()
               and not self._stop.is_set()):
            self._ring.wait_room(0.2)
        self._ring.push(item)
        # 대기 세션은 자막이 없어 상태가 저장될 계기가 없습니다. 재시작 뒤 「어디까지
        # 받았는가」(recv_t)가 몇 시간 전으로 남지 않게 이따금 적어 둡니다.
        if (not self._focus.is_set()
                and self._recv_s - self._warm_persisted_s >= RING_S):
            self._warm_persisted_s = self._recv_s
            self._persist()

    def _read_loop(self):
        """ffmpeg이 내놓는 소리를 0.1초 조각으로 링에 넣습니다. **끊기면 같은 세션
        안에서 다시 붙습니다.** 읽기 스레드에서 돕니다.

        예전에는 ffmpeg이 끝나면 곧 세션이 「종료됨」이었습니다. 방송이 끝난
        것과 재생목록을 잠깐 못 받은 것이 같은 결말이었고, 두 시간 방송이
        30분에 한 번 끊기면 자막이 새 세션으로 갈라졌습니다. 이제 ffmpeg이
        스스로 끝나면 방송이 아직 진행 중인지 다시 물어보고, 진행 중이면 끊긴
        자리(DVR 창 안이면 한 조각도 잃지 않고)에서 이어 받습니다. 못 메운
        구간은 자막에 적습니다 -- 이어받기와 같은 규칙입니다.

        조각 사이에 `("flush",)` 표식을 한 번 넣어 걸려 있던 발화를 확정시킵니다.
        끝나면 `("end",)` 를 넣습니다 -- 받아 적는 쪽은 그것을 보고 물러납니다.
        """
        need = CHUNK * 2
        attempt = 0
        try:
            self._read_until_end(need, attempt)
        finally:
            self._ended = True
            self._ring.push(("end",))

    def _read_until_end(self, need: int, attempt: int):
        while not self._stop.is_set():
            assert self._ff and self._ff.stdout
            while not self._stop.is_set():
                raw = self._ff.stdout.read(need)
                if not raw or len(raw) < need:
                    break
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                self._recv_s += len(samples) / SAMPLE_RATE
                attempt = 0                  # 소리가 오면 재시도 횟수는 처음부터
                self._push_audio(samples)
            if self._stop.is_set():
                break
            # ffmpeg이 스스로 끝났습니다. 방송이 끝났거나, 재생목록을 잠깐
            # 못 받은 것입니다. 걸려 있는 발화를 먼저 확정합니다.
            self._ring.push(("flush",))
            reattached = False
            while not self._stop.is_set() and attempt < HLS_RECONNECT_TRIES:
                attempt += 1
                outcome = self._reconnect(attempt)
                if outcome == "ok":
                    reattached = True
                    break
                if outcome == "ended":
                    self.stopped_by = "ended"
                    break
                # 아직 붙지 못했습니다. 잠깐 기다렸다 다음 시도로. stop()이
                # 오면 기다림이 곧 끝납니다.
                if self._stop.wait(min(3.0 * attempt, 15.0)):
                    break
            if reattached:
                continue
            if not self._stop.is_set() and self.stopped_by != "ended":
                self.stopped_by = "stream"
                self.state = "error"
                self.error = (f"수신이 끊겼고 {HLS_RECONNECT_TRIES}번 다시 붙어 보았지만 "
                              "되지 않았습니다. 「이어받기」로 다시 시도할 수 있습니다.")
            break

    def _consume(self):
        """링에서 조각을 꺼내 `run_stream` 에 넘깁니다. 받아 적는 스레드에서 돕니다.

        소리 조각은 읽기 시계의 값을 물고 오므로 `audio_s` 를 **그 값으로 맞춥니다**
        (더하지 않고). 링에 고여 있던 30초를 몰아서 해독해도 각 줄의 시각은 그 조각이
        방송에서 실제로 있던 자리입니다. 표식은 순서대로 처리합니다 -- flush 는 걸린
        발화를 확정하고(None), rebase 는 재접속 뒤의 새 시간 기준과 빠진 구간 안내이고,
        end 는 읽기가 끝났다는 뜻입니다. 초점을 잃으면 걸린 발화만 확정하고 물러납니다;
        세션은 그대로 살아 소리를 계속 받습니다.
        """
        idle_flush = self.source == "tab"   # 탭만 무음 2초에 비웁니다 -- 예전 규칙 그대로
        idle = False
        waited = 0.0
        while not self._stop.is_set() and self._focus.is_set():
            # 짧게 기다립니다. 초점이 옮겨 가면 그만큼 빨리 알아채야 다음 세션이 토큰을
            # 받습니다. 무음 판정(2초)은 기다린 시간을 더해서 냅니다.
            item = self._ring.pop(timeout=0.5)
            if item is Ring.TIMEOUT:
                waited += 0.5
                # 소리가 오지 않습니다 -- 탭이라면 영상을 멈췄거나 공유가 끊긴 것.
                # 오지 않을 무음을 기다리는 대신 걸려 있는 발화를 확정합니다.
                if idle_flush and not idle and waited >= 2.0:
                    idle = True
                    yield None
                continue
            waited = 0.0
            kind = item[0]
            if kind == "audio":
                self.audio_s = item[1]
                idle = False
                yield item[2]
            elif kind == "flush":
                yield None
            elif kind == "rebase":
                self.media_base = item[1]
                self.audio_s = 0.0
                if item[2]:
                    self.publish_line("note", item[2], self.lang or "", "")
            elif kind == "end":
                break
        if self._stop.is_set() or self._ended:
            if self.state != "error":
                self.state = "stopped"
            return
        # 초점만 잃었습니다. 걸린 발화를 확정하고 조용히 물러납니다.
        yield None

    def _reconnect(self, attempt: int) -> str:
        """끊긴 자리에서 ffmpeg을 다시 세웁니다.

        돌려주는 것은 "ok"(붙었음) / "ended"(방송이 끝났음) / "retry"(지금은
        못 붙었음)입니다. 붙었으면 `self._ff`가 새 프로세스입니다.
        """
        # 지금까지 받은 자리. 새 재생목록의 시각 기준(_recv_base)이 여기서
        # 다시 계산되므로 _recv_s는 0부터 다시 셉니다 -- media_t = base + s.
        self.resume_from = self._recv_base + self._recv_s
        self._rewind = True                  # 방금 끊긴 자리는 DVR 창 안입니다. 메웁니다
        try:
            src, start_index = self._resolve_hls(reconnect=True)
        except Exception as exc:
            print(f"[live] 세션 {self.id} 다시 붙기 {attempt}회 실패: {exc}",
                  file=sys.stderr, flush=True)
            return "retry"
        if src is None:
            return "ended"
        self._recv_s = 0.0
        print(f"[live] 세션 {self.id} 다시 붙음 ({attempt}회, 빠진 구간 {self.gap_s:.0f}초)",
              flush=True)
        # 새 시간 기준과 빠진 구간 안내는 링을 **거쳐서** 갑니다. 여기서 바로 발행하면
        # 링에 남아 있는 옛 조각들보다 앞서 도착해, 그 조각들의 시각이 새 기준으로 찍힙니다.
        note = (f"⋯ 수신이 끊겨 약 {int(self.gap_s)}초를 받지 못했습니다 ⋯"
                if self.gap_s >= 1.0 else "")
        self._ring.push(("rebase", self._recv_base, note))
        self._spawn_ffmpeg(src, start_index)
        self.gap_s = 0.0
        return "ok"

    def _run(self):
        # 여기는 소리를 어디서 받을지만 정하고, 받아 적는 일은 _transcribe가
        # 합니다. 모델을 놓는 finally는 그쪽에 있습니다 -- 이 함수의 finally는
        # 세션을 등록부에서 빼는 일만 하므로, 무엇이 실패해도 실행됩니다.
        # (예전에 여기서 모델 이름을 `del` 하다가 try가 그 이름을 만들기 전에
        # 실패하면 UnboundLocalError가 진짜 예외를 덮고 _release()까지
        # 건너뛰어, 세션 하나가 3GB인 채로 남았습니다. 이슈 #1.)
        try:
            src = start_index = None
            if self.source == "hls":
                src, start_index = self._resolve_hls()
                if src is None:
                    return      # 오류는 _resolve_hls가 이미 알렸습니다
                self.media_base = self._recv_base   # 아직 스레드가 없어 그냥 복사합니다
            else:
                # 탭 오디오에는 풀 재생목록도, 맞출 방송 시각도 없습니다.
                # 사용자가 듣고 있는 그 순간이 0초입니다 -- 오히려 화면 위
                # 자막 정렬에는 이쪽이 정확합니다. 사용자의 재생 위치가
                # 곧 기준이기 때문입니다.
                #
                # 이어받기면 멈춘 자리에서 시간축을 이어 갑니다. 0으로
                # 되돌리면 새 자막이 옛 자막 사이에 끼어 들어가 스크립트
                # 순서가 뒤엉킵니다. 새 세션에서는 resume_from이 0입니다.
                self.media_base = self._recv_base = self.resume_from
            self.state = "loading"
            self._persist()
            self.emit({"type": "status", **self.status()})
            self._transcribe(src, start_index)
        except Exception as exc:
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            # 화면에는 한 줄만 갑니다. 어디서 났는지는 로그에 남겨야
            # 다음 보고가 진단 가능해집니다.
            print(f"[live] 세션 {self.id} 실패:", file=sys.stderr)
            traceback.print_exc()
            self._persist()
            self.emit({"type": "status", **self.status()})
        finally:
            self._release()
            _retire(self)

    def _resolve_hls(self, reconnect: bool = False):
        """방송 주소를 ffmpeg이 읽을 수 있는 것으로 풀어냅니다.

        돌려주는 것은 (재생목록 주소, -live_start_index)이고, 첫 값이 None이면
        더 갈 수 없다는 뜻입니다 -- 상태와 오류는 여기서 이미 알렸습니다.
        `reconnect`면 「라이브가 아님」은 오류가 아니라 방송이 끝난 것이므로
        상태를 건드리지 않고 None만 돌려줍니다.
        """
        d = {}
        try:
            meta = subprocess.run(stream.ytdlp_args("-j", url=self.url),
                                  capture_output=True, text=True,
                                  timeout=stream.YTDLP_TIMEOUT_S,
                                  **stream.child_io(stderr=False))
        except TimeoutExpired:
            # 정보를 못 받아도 아래 resolve_audio가 한 번 더 시도합니다.
            # 거기서도 안 되면 그쪽이 이유를 실어 예외를 냅니다.
            meta = subprocess.CompletedProcess(args=[], returncode=-1,
                                               stdout="", stderr="시간 초과")
        if meta.returncode == 0:
            try:
                d = json.loads(meta.stdout)
            except json.JSONDecodeError:
                d = {}                    # 재생목록 주소. 아래 resolve_audio가 판단합니다
            self.title = d.get("title", "")
            self.video_id = d.get("id", "") or ""
            info = site_of(d, self.url)
            self.site, self.channel = info["site"], info["channel"]
            # 생 m3u8 은 yt-dlp 의 범용 추출기가 라이브인지 모릅니다(is_live 가 None).
            # 사용자가 라이브로 넣은 것이니 모르면 라이브로 봅니다. 아니라고 하면(False)
            # 그때만 막습니다.
            not_live = (d.get("is_live") is False if self.site == "other"
                        else not d.get("is_live"))
            if not_live:
                if reconnect:
                    return None, None
                self.state = "error"
                # 방금 끝난 방송도 여기로 옵니다. /api/probe가 볼 때는
                # 라이브였는데 그 사이 끝난 경우입니다.
                self.error = ("라이브가 아닙니다. 방송이 방금 끝났거나 "
                              "녹화본 주소일 수 있습니다. 녹화본은 "
                              "「＋ 영상 추가」로 처리하십시오.")
                self._persist()
                self.emit({"type": "status", **self.status()})
                return None, None

        src, info = resolve_audio(self.url, youtube=self.site != "other" and self.site != "twitch")
        if reconnect and info.get("ended"):
            # 재생목록이 끝났다고 스스로 말합니다(녹화본 m3u8). yt-dlp 는 라이브인지 몰라
            # 「끝남」이라 하지 않으므로, 여기서 알아보지 않으면 ffmpeg 이 끝날 때마다 같은
            # 꼬리에 다시 붙어 같은 말을 되받아 적습니다 -- 실제로 수십 번 그랬습니다.
            return None, None
        release_ts = None
        if meta.returncode == 0:
            release_ts = d.get("release_timestamp") or d.get("timestamp")
        # Skipping the DVR window means the audio starts at the live edge;
        # media_base has to account for everything we deliberately passed.
        self.window_s = info.get("window_s") or 0.0
        start_index, skipped = -2, self.window_s
        if self.resume_from and self._rewind:
            start_index, skipped = self._resume_point(info, release_ts)
        self._recv_base = media_base_from(info.get("pdt"), release_ts, skipped)
        if self.resume_from and not self._rewind:
            # 이어받기: 라이브 끝에서 시작합니다. 멈춘 자리와의 거리가 빠진 구간입니다.
            self.gap_s = max(0.0, self._recv_base - self.resume_from) if self._recv_base else 0.0
        print(f"[live] playlist: {info.get('segments')} segments / "
              f"{self.window_s:.0f}s window, media_base={self._recv_base:.0f}s"
              + (f", 이어받기 index={start_index} 빠진 구간={self.gap_s:.0f}s"
                 if self.resume_from else ""),
              flush=True)
        return src, start_index

    def _transcribe(self, src, start_index):
        """소리를 받아 자막으로 내보냅니다. 소리가 어디서 오는지는 모릅니다.

        ffmpeg 파이프든 브라우저가 올린 조각이든 여기부터는 같은 길입니다 --
        `run_stream`이 받는 것은 float32 조각을 내놓는 제너레이터뿐입니다.

        받아 적기는 **초점을 쥔 동안**만 합니다(_episode). 혼자 받는 세션은 처음부터
        초점이라 에피소드가 하나뿐이고, 멀티뷰에서는 초점이 오갈 때마다 하나씩입니다.
        그 사이 이 스레드는 초점을 기다리고, 읽기 스레드는 링을 채웁니다.
        """
        self._start_reader(src, start_index)   # 탭이면 할 일 없음 -- feed() 가 넣습니다
        self.state = "running"
        self._persist()
        self.emit({"type": "status", **self.status()})
        if self.gap_s >= 1.0:
            # 조용한 구멍을 남기지 않습니다. 되감아도 메우지 못한
            # 구간이 있으면 스크립트에 그렇게 적습니다 -- 자막이
            # 없는 것과 받아 적지 못한 것은 다른 이야기입니다.
            self.publish_line(
                "note",
                f"⋯ 서버가 멈춘 사이 약 {int(self.gap_s)}초를 받지 "
                f"못했습니다 ⋯", self.lang or "", "")
        if self.source == "tab" and self.resume_from:
            # 탭 오디오는 되감을 수 없습니다. 공유가 끊긴 동안의 소리는
            # 아무 데도 남아 있지 않으므로 몇 초인지도 알 수 없습니다.
            self.publish_line(
                "note", "⋯ 여기서부터 탭 소리를 다시 받습니다. 공유가 "
                "끊긴 사이는 받지 못했습니다 ⋯", self.lang or "", "")
        while not self._stop.is_set() and not self._ended:
            if not self._focus.wait(0.5):
                continue                 # 대기 세션: 링만 채워지고 있습니다
            self._episode()
        if self.state != "error":        # 수신 루프가 포기했으면 그 말을 남깁니다
            self.state = "stopped"
        self._persist()
        self.emit({"type": "status", **self.status()})

    def _ensure_engines(self):
        """전사기·번역기를 처음 초점을 받을 때 한 번 만듭니다. 가중치는 프로세스가 나눠
        쓰므로(models.py) 세션이 드는 것은 해독 세션과 껍데기뿐이고, 초점을 잃어도 놓지
        않습니다 -- 다음 초점에서 바로 씁니다. 대기 중 「관리」에서 엔진을 바꾼 것은
        asr_backend_id/backend_id 에 적혀 있다가 여기서 반영됩니다."""
        fresh = self._asr is None
        cfg = config.load()
        if self._tr is None:
            spec = config.find("tr", self.backend_id, cfg)
            self._tr = mw_translate.build(spec, self.genre)
        if self._asr is None:
            asr_spec = config.find("asr", self.asr_backend_id, cfg)
            # Speaker tags are a recorded-video feature. CAM++ needs enough
            # voice in one segment to place it, and live splits at 3-4s to
            # keep up with a talker who rarely finishes a long sentence: in
            # 70 seconds that produced six speaker ids on a stream that did
            # not have six people talking. A label that invents speakers is
            # worse than no label.
            self._asr = build_live_asr(asr_spec, self.lang, threads=4)
            self.asr_label = self._asr.label
        if fresh:
            # 엔진 이름이 상태에 실려야 화면이 「전사 <엔진>」을 적습니다.
            self._persist()
            self.emit({"type": "status", **self.status()})

    def _episode(self):
        """초점을 쥔 동안의 run_stream 한 번. 프로세스의 전사 토큰 안에서 돕니다.

        VAD·오디오 이력·정제기는 매번 새로 만듭니다 -- 셋 다 run_stream 시작을 0으로
        놓은 표본 위치를 기준으로 하므로, 초점이 없던 공백을 넘겨 이어 쓰면 선행 구간과
        정제 원본이 엉뚱한 소리를 가리킵니다. 정제기의 close() 까지 토큰 안에서 끝내야
        다른 세션이 같은 모델을 돌리기 시작할 때 이쪽의 마지막 해독이 끝나 있습니다.
        """
        while not _transcriber.acquire(timeout=0.5):
            if not self._focus.is_set() or self._stop.is_set():
                return               # 기다리는 사이 초점이 다른 데로 갔습니다
        vad = history = refiner = None
        try:
            if not self._focus.is_set() or self._stop.is_set() or self._ended:
                return
            self._ensure_engines()
            asr = self._asr
            vad = build_vad(min_silence=self.min_silence,
                            max_speech=self.max_speech)
            sink = Sink(self)
            history = AudioHistory(SAMPLE_RATE)
            refiner = Refiner(asr, history, sink) if self.refine else None
            print(f"[live] 세션 {self.id} 받아 적기 시작 (링 {self._ring.seconds():.1f}초)",
                  flush=True)
            run_stream(self._consume(), vad, asr, sink, history, refiner)
            if refiner is not None:
                # 마지막 무리의 정제가 끝나기를 기다립니다. 초점이 옮겨 간 뒤나 상태를
                # 「종료됨」으로 적은 뒤에 정제본이 도착하면 안 됩니다.
                refiner.close()
        finally:
            # 정제 스레드를 꼭 끝냅니다. 살려 두면 그 스레드가 전사 모델을
            # 쥐고 있어 아래 del 이 소용없습니다. 그리고 **정말 끝난 뒤에** 토큰을
            # 놓습니다 -- close() 는 10초만 기다리는데, 그 뒤에도 정제 해독이 돌고
            # 있으면 다음 세션이 같은 모델을 동시에 돌리게 됩니다.
            if refiner is not None:
                refiner.close()
                th = getattr(refiner, "_thread", None)
                waited = 0.0
                while (th is not None and th.is_alive() and waited < 120.0
                       and not self._stop.is_set()):
                    th.join(1.0)
                    waited += 1.0
                if th is not None and th.is_alive():
                    print(f"[live] 세션 {self.id} 정제 스레드가 {waited:.0f}초 뒤에도 살아 있음",
                          file=sys.stderr, flush=True)
            del vad, history, refiner
            focus = "있음" if self._focus.is_set() else "없음"
            print(f"[live] 세션 {self.id} 받아 적기 끝 (초점 {focus}, "
                  f"멈춤 {self._stop.is_set()}, 읽기 끝 {self._ended})", flush=True)
            _transcriber.release()

RUNNING_STATES = ("starting", "loading", "running")


def _evict_outside(keep_group: str):
    """다른 세션을 멈춥니다. `keep_group` 이 비면 전부 -- 「한 사람이 한 방송을 본다」는
    예전 규칙 그대로입니다. 멀티뷰는 제 묶음만 남기고 나머지를 멈춥니다. 남겨 두면
    ffmpeg 과 링이 아무도 보지 않는 방송을 위해 돌아갑니다."""
    for sid in list(_sessions):
        s = get(sid)
        if s is not None and (not keep_group or s.group != keep_group):
            s.stop()


def _new_session(url: str, lang: str | None, viewer_lang: str, backend_id: str,
                 profile: str, asr_backend_id: str, refine: bool, genre: str | None,
                 source: str, title: str, group: str = "",
                 focused: bool = True) -> LiveSession:
    s = LiveSession(url, lang, viewer_lang, backend_id, profile=profile,
                    asr_backend_id=asr_backend_id, refine=refine, genre=genre,
                    source=source, title=title)
    s.group = group
    if not focused:
        # 대기 세션으로 태어납니다. set_focus() 는 상태를 내보내는데 아직 등록도
        # 되지 않았으니 깃발만 내립니다.
        s._focus.clear()
        if s.source == "tab":
            s._ring.set_max(RING_S)
    with _lock:
        _sessions[s.id] = s
    # 첫 자막이 나오기 전에 서버가 죽어도 세션이 있었다는 사실은 남습니다.
    s._persist()
    s.start()
    return s


def start(url: str, lang: str | None, viewer_lang: str, backend_id: str,
          profile: str = "broadcast", asr_backend_id: str = "",
          refine: bool = True, genre: str | None = None,
          source: str = "hls", title: str = "") -> dict:
    # One viewer watches one broadcast. Leaving the previous session running
    # would keep a second copy of every model resident for nothing.
    _evict_outside("")
    s = _new_session(url, lang, viewer_lang, backend_id, profile, asr_backend_id,
                     refine, genre, source, title)
    return {"id": s.id, "source": s.source}


# ---- 멀티뷰 ---------------------------------------------------------------------
#
# 여러 방송을 한 화면에 두고 보되 소리와 자막은 **초점** 하나만. 서버에서 묶음(Group)은
# 얇습니다 -- 멤버 세션 id 와 초점이 어느 것인지. 초점이 아닌 멤버는 소리만 받고(링)
# 받아 적지 않으며, 초점이 옮겨 가면 옛 것이 물러난 뒤 새 것이 링에 고인 것부터 잇습니다.
# 묶음은 메모리에만 있습니다. 재시작하면 멤버 세션은 「중단됨」으로 남고 묶음은 없어집니다
# -- 다시 묶는 것은 사용자가 시킬 일입니다.

MULTIVIEW_MAX = 4          # 화면이 4분할까지입니다. ffmpeg 도 그만큼 뜹니다


class Group:
    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.members: list[str] = []
        self.focus: str | None = None

    def status(self) -> dict:
        return {"id": self.id, "focus": self.focus,
                "members": [status_of(m) or {"id": m} for m in self.members]}


_groups: dict[str, Group] = {}


def _publish_group(g: Group, deleted: bool = False):
    ev = {"type": "multiview", "id": g.id, "focus": g.focus, "members": list(g.members)}
    if deleted:
        ev["deleted"] = True
    bus.publish(ev)


def _apply_focus(g: Group, sid: str):
    """묶음의 초점을 `sid` 로. **옛 것을 먼저 끕니다** -- 그래야 옛 세션의 run_stream 이
    정제까지 끝내고 전사 토큰을 놓은 뒤 새 세션이 잡습니다(같은 모델을 두 세션이 동시에
    돌리지 않습니다). 그 사이 새 세션의 소리는 링에 있으므로 잃지 않습니다."""
    g.focus = sid
    for m in g.members:
        s = get(m)
        if s is not None and m != sid:
            s.set_focus(False)
    s = get(sid)
    if s is not None:
        s.set_focus(True)


def _leave_group(s: LiveSession):
    """끝난 세션을 묶음에서 뺍니다. 초점이었으면 남은 첫 멤버로 옮기고, 비면 묶음을 지웁니다."""
    g = _groups.get(s.group) if s.group else None
    s.group = ""                     # 끝난 세션은 묶음의 것이 아닙니다(목록의 ⊞ 표시가 남지 않게)
    if g is None or s.id not in g.members:
        return
    g.members.remove(s.id)
    if not g.members:
        with _lock:
            _groups.pop(g.id, None)
        _publish_group(g, deleted=True)
        return
    if g.focus == s.id:
        _apply_focus(g, g.members[0])
    _publish_group(g)


def _source_ok(src: dict) -> str:
    if src.get("session"):
        return ""
    if src.get("source") == "tab":
        return ""
    if not (src.get("url") or "").strip():
        return "주소가 없는 소스가 있습니다"
    return ""


def _member_from(src: dict, gid: str, lang, viewer_lang, backend_id, profile,
                 asr_backend_id, refine, genre) -> LiveSession | dict:
    """소스 하나를 묶음의 멤버로. 이미 받는 중인 세션(`session`)이면 편입하고, 아니면
    대기 세션으로 새로 만듭니다."""
    sid = src.get("session")
    if sid:
        s = get(sid)
        if s is not None and s.state in RUNNING_STATES:
            s.group = gid
            return s
        # 멈춘 방송입니다. 같은 세션으로 이어받아 대기 멤버로 넣습니다 -- 목록에서 끌어다
        # 놓은 지난 방송이 여기로 옵니다.
        got = resume(sid, asr_backend_id=asr_backend_id, backend_id=backend_id, group=gid)
        if "error" in got:
            return got
        return get(sid)
    tab = src.get("source") == "tab"
    return _new_session((src.get("url") or "").strip(), lang, viewer_lang, backend_id,
                        profile, asr_backend_id, refine, genre,
                        source="tab" if tab else "hls", title=src.get("title") or "",
                        group=gid, focused=False)


def multiview_start(sources: list[dict], lang: str | None, viewer_lang: str,
                    backend_id: str, profile: str = "broadcast",
                    asr_backend_id: str = "", refine: bool = True,
                    genre: str | None = None, focus: str | None = None) -> dict:
    """묶음을 만듭니다. `sources` 의 각 항목은 `{"session": id}`(지금 보는 것을 편입) 또는
    `{"url": ...}` / `{"source": "tab", "title": ...}`(새 대기 세션)입니다. 초점은 `focus`
    가 가리키는 세션, 없으면 첫 멤버입니다. 묶음 밖의 세션은 전부 멈춥니다."""
    sources = list(sources or [])
    if not sources:
        return {"error": "소스가 없습니다"}
    if len(sources) > MULTIVIEW_MAX:
        return {"error": f"멀티뷰는 최대 {MULTIVIEW_MAX}개까지입니다"}
    for src in sources:
        why = _source_ok(src)
        if why:
            return {"error": why}
    g = Group()
    with _lock:
        _groups[g.id] = g                # 멤버가 이어받기로 들어올 때 찾을 수 있어야 합니다
    members: list[LiveSession] = []
    for src in sources:
        m = _member_from(src, g.id, lang, viewer_lang, backend_id, profile,
                         asr_backend_id, refine, genre)
        if isinstance(m, dict):
            # 편입할 세션이 없습니다. 방금 만든 대기 세션들은 되돌리고 묶음도 지웁니다.
            for made in members:
                if not any(sr.get("session") == made.id for sr in sources):
                    made.stop()
            for made in members:
                made.group = ""
            with _lock:
                _groups.pop(g.id, None)
            return m
        members.append(m)
    g.members = [m.id for m in members]
    _evict_outside(g.id)
    _apply_focus(g, focus if focus in g.members else g.members[0])
    _publish_group(g)
    return g.status()


def multiview_status(gid: str) -> dict | None:
    g = _groups.get(gid)
    return g.status() if g else None


def multiview_focus(gid: str, sid: str) -> dict:
    g = _groups.get(gid)
    if g is None:
        return {"error": "no such group"}
    if sid not in g.members:
        return {"error": "그 세션은 이 묶음에 없습니다"}
    prev = g.focus
    if prev != sid:
        _apply_focus(g, sid)
        _publish_group(g)
    return {"group": g.id, "focus": g.focus, "previous": prev}


def multiview_add(gid: str, src: dict, lang: str | None, viewer_lang: str,
                  backend_id: str, profile: str = "broadcast",
                  asr_backend_id: str = "", refine: bool = True,
                  genre: str | None = None) -> dict:
    g = _groups.get(gid)
    if g is None:
        return {"error": "no such group"}
    if len(g.members) >= MULTIVIEW_MAX:
        return {"error": f"멀티뷰는 최대 {MULTIVIEW_MAX}개까지입니다"}
    why = _source_ok(src)
    if why:
        return {"error": why}
    m = _member_from(src, g.id, lang, viewer_lang, backend_id, profile,
                     asr_backend_id, refine, genre)
    if isinstance(m, dict):
        return m
    if m.id not in g.members:
        g.members.append(m.id)
    if m.id != g.focus:
        m.set_focus(False)           # 편입한 세션이 혼자 초점을 쥐고 있었을 수 있습니다
    _evict_outside(g.id)
    _publish_group(g)
    return m.status()


def multiview_remove(gid: str, sid: str) -> dict:
    """타일을 닫습니다 = 그 세션을 멈추고 묶음에서 뺍니다."""
    g = _groups.get(gid)
    if g is None:
        return {"error": "no such group"}
    if sid not in g.members:
        return {"error": "그 세션은 이 묶음에 없습니다"}
    s = get(sid)
    if s is not None:
        s.stop()                     # 끝나면 _retire → _leave_group 이 뒷정리를 하지만,
    g.members.remove(sid)            # 화면은 지금 답을 받아야 하므로 여기서 먼저 뺍니다
    if s is not None:
        s.group = ""
    if not g.members:
        with _lock:
            _groups.pop(g.id, None)
        _publish_group(g, deleted=True)
        return {"ok": True, "focus": None}
    if g.focus == sid:
        _apply_focus(g, g.members[0])
    _publish_group(g)
    return {"ok": True, "focus": g.focus}


def multiview_stop(gid: str) -> dict:
    g = _groups.get(gid)
    if g is None:
        return {"error": "no such group"}
    ids = list(g.members)
    for sid in ids:
        s = get(sid)
        if s is not None:
            s.group = ""
            s.stop()
    g.members = []
    with _lock:
        _groups.pop(g.id, None)
    _publish_group(g, deleted=True)
    return {"stopped": ids}


def set_title(session_id: str, title: str) -> dict:
    """세션 이름을 고칩니다.

    탭 소리에는 가져올 제목이 없습니다. 크롬은 캡처 트랙의 label 에 탭 제목이
    아니라 불투명한 식별자를 넣습니다 -- 실측한 값이
    `web-contents-media-stream://8D6F…` 입니다. 시작할 때 이름을 못 적었거나
    잘못 적었으면 나중에 고치는 수밖에 없습니다.

    끝난 세션도 고칠 수 있어야 합니다. 무엇을 들었는지는 대개 다 듣고 나서
    목록을 볼 때 문제가 되니까요.
    """
    title = (title or "").strip()[:200]
    if not title:
        return {"error": "이름을 입력해 주세요"}
    s = get(session_id)
    if s is not None:
        s.title = title
        s._persist()
        s.emit({"type": "status", **s.status()})
        return {"ok": True, "title": title}
    st = store.session(session_id)
    if not st:
        return {"error": "no such session"}
    # store.session 은 doc 에 video_id 를 얹어 돌려줍니다. 그대로 다시 넣으면
    # doc 안에 그 열이 한 번 더 들어가므로 떼어 내고 저장합니다.
    video_id = st.pop("video_id", "") or ""
    st["title"] = title
    store.save_session(st, video_id)
    bus.publish({"type": "session", **st, "video_id": video_id})
    return {"ok": True, "title": title}


def notify_edit(owner: str, cue: dict, backend: str = ""):
    """고친 줄을 보고 있는 창들에 알립니다.

    본 창에서 고치면 대본 창에도 바로 반영되어야 합니다. 받는 중인 세션만
    구독자가 있으므로(끝난 세션의 SSE 는 백로그를 다 보내고 닫습니다),
    여기서 할 일이 없으면 조용히 지나갑니다.

    새 이벤트 종류를 만들지 않고 자막이 도착할 때와 같은 모양으로 보냅니다.
    브라우저는 이미 id 로 줄을 찾아 제자리에서 갈아 끼웁니다.
    """
    s = get(owner)
    if s is None:
        return
    s.emit({"type": "cue", "id": cue["id"], "kind": cue["kind"],
            "t": cue["t"], "text": cue["text"], "lang": cue["lang"],
            "speaker": cue["speaker"], "edited": cue["edited"]})
    trs = cue.get("translations") or {}
    text = trs.get(backend) or next(iter(trs.values()), "")
    if text:
        s.emit({"type": "translation", "id": cue["id"],
                "kind": cue["kind"], "text": text})


def notify_translation(owner: str, cue_id: int, kind: str, text: str):
    """다시 번역한 줄을 보고 있는 창들에 흘려보냅니다. 받는 중인 세션이
    아니면 구독자가 없으므로 조용히 지나갑니다."""
    s = get(owner)
    if s is not None:
        s.emit({"type": "translation", "id": int(cue_id),
                "kind": kind or "final", "text": text})


def notify_drop(owner: str, cue_id: int):
    s = get(owner)
    if s is not None:
        s.emit({"type": "drop", "id": int(cue_id)})


def feed(session_id: str, raw: bytes) -> dict:
    """브라우저가 올린 탭 오디오 한 덩어리를 세션에 넣습니다."""
    s = get(session_id)
    if not s:
        # 서버가 재시작됐거나 세션이 끝났습니다. 브라우저는 이 답을 보고
        # 공유를 스스로 끊습니다 -- 아무도 듣지 않는 소리를 계속 올리는
        # 것보다 낫습니다.
        return {"error": "no such session"}
    return s.feed(raw)


def shutdown(timeout: float = 8.0) -> int:
    """서버를 끄기 전에 세션을 제대로 닫습니다.

    그냥 프로세스를 죽이면 DB에 `running`으로 남고, 다음 기동의 복구 스윕이
    그것을 **중단됨**으로 표시합니다. 사용자가 스스로 끈 것과 서버가 죽은
    것이 기록에서 구분되지 않는 셈입니다.

    `stop()`은 신호만 보내므로 여기서 기다립니다 -- 수신 루프가 ffmpeg의
    끊긴 파이프를 알아채고 상태를 `stopped`로 적을 때까지입니다. 기다리지
    않으면 애써 부른 보람이 없습니다.
    """
    with _lock:
        live_ids = list(_sessions)
    for sid in live_ids:
        s = get(sid)
        if s is not None:
            s.stop()
    if not live_ids:
        return 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not store.running_session_ids():
            break
        time.sleep(0.2)
    return len(live_ids)


def _retire(session: "LiveSession"):
    """Move a finished session out of the live registry. Its final status and
    its subtitles stay in SQLite, so a late poll -- or a poll after the next
    restart -- still gets an answer instead of a 404.

    **자기 자신일 때만** 뺍니다. 세션이 오류로 끝나면 화면은 곧 「이어받기」를 보이는데,
    옛 세션의 `_run`은 정제기·번역 워커가 닫히기까지 최대 20초 더 살아 있습니다. 그 사이
    같은 id 로 이어받은 새 세션이 등록부에 들어가면, 옛 것의 finally 가 그 새 세션을 빼고
    상태를 자기 것(error)으로 덮어썼습니다 -- 새 세션은 「중단」도 듣지 않는 고아가 됐습니다.
    """
    with _lock:
        if _sessions.get(session.id) is session:
            _sessions.pop(session.id, None)
        else:
            return                          # 이미 다른(이어받은) 세션이 그 자리에 있습니다
    _leave_group(session)
    session._persist()


def get(session_id: str) -> LiveSession | None:
    with _lock:
        return _sessions.get(session_id)


def status_of(session_id: str) -> dict | None:
    s = get(session_id)
    if s is not None:
        return s.status()
    return store.session(session_id)


def recent(limit: int = 50) -> list[dict]:
    """Sessions the viewer can go back to, newest first.

    A live session leaves no cue file, so before this it existed only for as
    long as the tab stayed open. The picker needs a list to offer.

    한도가 20이던 때 표에는 42개가 있었습니다 -- 절반이 보이지 않았고 지울
    길도 없었습니다. 이제 지울 수 있으니(`delete`) 한도는 넉넉히 두고,
    화면이 `?limit=`로 더 청할 수 있습니다.
    """
    with _lock:
        live_now = {sid: s.status() for sid, s in _sessions.items()}
    out = []
    for row in store.sessions(limit):
        out.append({**row, **live_now.get(row["id"], {})})
    return out


def delete(session_id: str) -> dict:
    """세션과 그 자막을 지웁니다. 받는 중이면 먼저 멈춰야 합니다 -- 받는
    도중에 표에서 지우면 다음 줄이 곧바로 다시 만들어 유령 세션이 됩니다."""
    if get(session_id) is not None:
        return {"error": "받는 중인 세션은 지울 수 없습니다. 먼저 「중단」하십시오."}
    if not store.delete_session(session_id):
        return {"error": "no such session"}
    bus.publish({"type": "session", "id": session_id, "deleted": True})
    return {"deleted": session_id}


def backlog(session_id: str) -> list[dict]:
    """Everything already published on this session, as the events the
    browser would have received. Replayed on SSE connect, which is what makes
    a reload -- or a restart -- keep the transcript so far.

    Translations ride behind their cue because the browser attaches them by
    id: a translation for a line it has not seen is dropped.
    """
    st = store.session(session_id) or {}
    backend = st.get("backend") or ""
    events = []
    for c in store.cues(session_id):
        tr = c.pop("translations", {})
        events.append({"type": "cue", **c})
        text = tr.get(backend) or next(iter(tr.values()), None)
        if text:
            events.append({"type": "translation", "id": c["id"],
                           "kind": c["kind"], "text": text})
    return events


def restore() -> int:
    """Sessions that were running when the server went down.

    Their ffmpeg child died with the process, so capture cannot continue and
    saying "running" would be a lie -- the UI would attach to a broadcast
    that is not being received. Mark them interrupted; the subtitles they
    already collected stay readable.
    """
    hit = 0
    for session_id in store.running_session_ids():
        st = store.session(session_id) or {}
        st["state"] = "interrupted"
        st["error"] = "서버가 재시작되어 수신이 끊겼습니다. 여기까지 받아 적은 자막입니다."
        store.save_session(st, st.get("video_id", ""))
        hit += 1
    return hit


def set_backend(session_id: str, backend_id: str) -> dict:
    """Swap the translator mid-session. Lines already published keep the text
    they were given; everything after this uses the new backend."""
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    spec = config.find_backend(backend_id)
    if spec is None:
        return {"error": f"'{backend_id}' 백엔드가 없습니다"}
    # 장르는 보고 있는 영상의 성질이므로 백엔드를 바꿔도 그대로입니다.
    s._tr = mw_translate.build(spec, s.genre)
    s.backend_id = backend_id
    return {"backend": backend_id}


def resume(session_id: str, asr_backend_id: str = "", backend_id: str = "",
           source: str = "", url: str = "", group: str = "") -> dict:
    """끊긴 세션의 수신을 **같은 세션으로** 이어 붙입니다.

    지금까지는 서버가 죽으면 그 세션은 거기서 끝이었습니다. 이어받은 척하면
    조용한 구멍이 생긴다는 이유였는데, 구멍을 조용하지 않게 만들면 그 이유가
    없어집니다 -- 빠진 초를 자막 한 줄로 적습니다.

    **되감지 않습니다.** 예전에는 DVR 창 안이면 멈춘 자리까지 되감아 메웠는데,
    그러면 이어받기가 늦을수록 그만큼을 먼저 받아 적느라 지금 보는 자리의 자막은
    한참 뒤에 나왔습니다(느린 엔진이면 더). 사용자가 보는 것은 지금이므로 지금
    라이브 끝에서 시작합니다. 세션 안의 자동 재접속(몇 초 끊김)만 되감습니다.

    세션 id를 그대로 쓰므로 자막은 이어집니다. 자동으로 하지 않습니다 --
    서버를 켰다고 방송을 다시 받기 시작하는 것은 사용자가 시킨 일이 아닙니다.

    `group` 을 주면 그 멀티뷰 묶음의 멤버로 살아납니다(대기 세션 -- 묶음에 초점이
    없을 때만 초점을 받습니다). 목록의 멈춘 방송을 화면에 끌어다 놓는 길입니다.
    """
    st = store.session(session_id)
    if not st:
        return {"error": "no such session"}
    if st.get("state") in ("starting", "loading", "running"):
        return {"error": "이미 받는 중입니다"}
    if get(session_id) is not None:
        # 저장된 상태는 끝났지만 옛 세션의 스레드가 아직 정리 중입니다(정제·번역 마무리,
        # 최대 20초). 그 위에 새 세션을 얹으면 옛 finally 가 새 것을 밀어냅니다.
        return {"error": "앞선 수신을 정리하는 중입니다. 몇 초 뒤 다시 누르십시오."}
    # 소리 출처는 바꿔 이어받을 수 있습니다. 주소로 받던 방송이 도중에 멤버십 전용으로 바뀌면
    # 탭 소리로, 탭 소리로 받던 것을 브라우저를 닫고 이어 가려면 주소로. 자막은 세션 id 로
    # 이어지므로 출처가 바뀌어도 한 줄기입니다. 미디어 시각은 두 출처 모두 `resume_from`
    # 에서 이어 갑니다.
    tab = (source or st.get("source")) == "tab"
    live_url = (url or "").strip() or (st.get("url") or "")
    if not tab and not live_url:
        return {"error": "주소가 남아 있지 않아 이어받을 수 없습니다. 탭 소리로 이어받으십시오."}

    # 한 번에 한 방송만 받습니다. start() 와 같은 규칙입니다 -- 모델을 두 벌
    # 올려 둘 이유가 없습니다. 묶음에 들어가는 것이면 그 묶음만 남깁니다.
    g = _groups.get(group) if group else None
    if group and g is None:
        return {"error": "no such group"}
    _evict_outside(group)

    # 엔진은 부르는 쪽이 준 것이 우선입니다(「관리」에서 바꾼 뒤 이어받기). 설정에 없는
    # id 면 저장된 것을 씁니다 -- 이어받기가 엔진 이름 하나 때문에 실패하면 안 됩니다.
    cfg = config.load()
    asr_id = (asr_backend_id if config.find("asr", asr_backend_id, cfg)
              else (st.get("asr_backend") or ""))
    tr_id = backend_id if config.find("tr", backend_id, cfg) else (st.get("backend") or "")
    s = LiveSession(live_url if not tab else (st.get("url") or live_url),
                    st.get("source_lang") or None,
                    st.get("viewer_lang") or "ko", tr_id,
                    profile=st.get("profile") or "broadcast",
                    asr_backend_id=asr_id,
                    refine=bool(st.get("refine")), genre=st.get("genre"),
                    source="tab" if tab else "hls")
    s.id = session_id
    s.title = st.get("title") or ""
    s.video_id = st.get("video_id") or ""
    s.site = st.get("site") or ""
    s.channel = st.get("channel") or ""
    s.error = None                       # 왜 멈췄었는지는 이제 지난 일입니다
    # 이어 붙이려면 번호가 이어져야 합니다. 새 줄이 옛 줄의 id를 다시 쓰면
    # 화면에서 그 자리를 덮어씁니다.
    prior = store.cues(session_id)
    s._seq = max((int(c["id"]) for c in prior), default=0)
    s.lines = len(prior)
    # 「받은 자리」(recv_t)에서 잇습니다. 멀티뷰의 대기 세션은 받아 적은 자리(media_base+
    # audio_s)가 몇 시간 전에 멈춰 있을 수 있는데, 거기서 되감으면 DVR 창 안이라도 이미
    # 본 구간을 다시 받아 적게 됩니다. 옛 기록에는 recv_t 가 없으므로 그때는 예전 식으로.
    s.resume_from = float(st.get("recv_t")
                          or (float(st.get("media_base") or 0.0) + float(st.get("audio_s") or 0.0)))
    if g is not None:
        s.group = g.id
        if g.focus in (None, s.id):
            g.focus = s.id               # 초점이 없는 묶음이면 이것이 초점
        else:
            s._focus.clear()             # 있으면 대기 세션으로 살아납니다
            if s.source == "tab":
                s._ring.set_max(RING_S)
        if s.id not in g.members:
            g.members.append(s.id)

    with _lock:
        _sessions[s.id] = s
    s._persist()
    s.start()
    if g is not None:
        _publish_group(g)
    # 브라우저는 source를 보고 탭 공유를 다시 물을지 정합니다. 탭 세션은
    # 서버가 되감을 수 없으므로 소리를 다시 들려주지 않으면 한 줄도 늘지
    # 않은 채 「받는 중」으로 남습니다.
    return {"id": s.id, "resumed": True, "source": s.source}


def set_asr(session_id: str, asr_backend_id: str) -> dict:
    """돌아가는 세션의 전사 엔진을 갈아 끼웁니다.

    예전에는 세션을 다시 시작해야 했습니다. 그러면 세션 id가 바뀌고 자막은
    세션 id로 저장되므로 **그때까지의 스크립트가 화면에서 사라졌습니다.**
    번역기는 이미 세션 안에서 갈아 끼우고 있었으니(set_backend) 전사기만
    그럴 이유가 없습니다. 한 영상 안에서 자막은 이어져야 합니다.

    이미 나간 줄은 그것을 받아 적은 엔진의 것으로 남고, 이후만 새 엔진이
    맡습니다 -- 번역기 쪽과 같은 규칙입니다.
    """
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    spec = config.find_asr(asr_backend_id)
    if spec is None:
        return {"error": f"'{asr_backend_id}' 전사 엔진이 없습니다"}
    if s._asr is None:
        # 아직 한 번도 초점을 받지 않아 엔진을 만들지 않았습니다(멀티뷰의 대기 세션).
        # 첫 초점에서 이 id 로 만듭니다.
        s.asr_backend_id = asr_backend_id
        s._persist()
        return {"asr": asr_backend_id, "label": "", "device": "", "threads": 0}
    if not hasattr(s._asr, "swap"):
        return {"error": "이 세션의 전사기는 갈아 끼울 수 없습니다"}
    try:
        info = s._asr.swap(spec)
    except Exception as exc:
        # 실패하면 쓰던 것이 그대로 남습니다. 바꾸려다 방송을 잃지 않습니다.
        return {"error": f"{exc}"}
    s.asr_backend_id = asr_backend_id
    s.asr_label = info["label"]
    s._persist()
    s.emit({"type": "status", **s.status()})
    return {"asr": asr_backend_id, **info}


def stop(session_id: str) -> dict:
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    s.stop()
    return {"state": "stopping"}
