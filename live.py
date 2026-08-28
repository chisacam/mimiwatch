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
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import time
import urllib.request
import uuid

import numpy as np

import store
import stream
import translate as mw_translate
from stream import (SAMPLE_RATE, AudioHistory, Refiner, build_vad,  # noqa: F401
                    run_stream)
from tcpp_asr import build_live_asr

CHUNK = 1600            # 0.1s per VAD feed

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

_sessions: dict[str, "LiveSession"] = {}
_lock = threading.Lock()

# 끝난 세션의 마지막 상태는 SQLite가 들고 있습니다. 예전에는 메모리 딕셔너리에
# 최근 20개만 남겨 두었는데, 재시작하면 그마저 사라지는 데다 상한을 넘긴 세션은
# 살아 있는 서버에서도 404가 되었습니다.


def resolve_audio(url: str) -> tuple[str, dict]:
    """Audio-only rendition plus what the manifest says about media time."""
    why = []
    for fmt in ("234", "233", "bestaudio"):
        out = subprocess.run(stream.ytdlp_cmd() + ["--no-warnings", "-f", fmt, "-g", url],
                             capture_output=True, text=True)
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
                + (" (석 달 넘음)" if ytdlp_stale(ver) else "")
                + ". `yt-dlp -U` 로 올린 뒤 다시 해 보십시오.")
    raise RuntimeError("yt-dlp가 이 주소에서 오디오를 찾지 못했습니다."
                       + hint + " [" + " / ".join(why) + "]")


def ytdlp_version() -> str:
    """설치된 yt-dlp의 판. 못 물으면 빈 문자열."""
    try:
        out = subprocess.run(stream.ytdlp_cmd() + ["--version"], capture_output=True,
                             text=True, timeout=20)
        return (out.stdout or "").strip().splitlines()[0] if out.returncode == 0 else ""
    except Exception:
        return ""


def ytdlp_stale(version: str, days: int = 90) -> bool:
    """이 판이 낡았는가.

    yt-dlp의 판은 YYYY.MM.DD입니다. 유튜브가 추출 경로를 자주 바꾸고
    yt-dlp가 그때마다 따라가므로, 몇 달 지난 판은 포맷 목록을 통째로 받지
    못하는 일이 흔합니다. 그러면 234도 233도 bestaudio도 전부 "Requested
    format is not available"이 됩니다 -- 포맷이 없는 것이 아니라 아무것도
    못 읽은 것입니다.
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
            "segments": 0, "window_s": 0.0, "media_base": 0.0}
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
                 genre: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
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
        self.title = ""
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
        self._stop = threading.Event()
        self._ff: subprocess.Popen | None = None
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
            for q in self._subs:
                q.put(data)

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error,
                "title": self.title, "url": self.url, "video_id": self.video_id,
                "source_lang": self.lang, "viewer_lang": self.viewer_lang,
                "backend": self.backend_id,
                "asr_backend": self.asr_backend_id,
                "refine": self.refine,
                "asr": self.asr_label,
                "media_base": round(self.media_base, 2),
                "profile": self.profile, "max_speech": self.max_speech,
                "genre": self.genre,
                "window_s": round(self.window_s, 1),
                "audio_s": round(self.audio_s, 1),
                "elapsed": round(time.time() - self.started, 1),
                "lines": self.lines, "translated": self.translated}

    def _persist(self):
        store.save_session(self.status(), self.video_id)

    # ---- publishing -------------------------------------------------------
    def publish_line(self, kind: str, text: str, lang: str, speaker: str):
        text = (text or "").strip()
        if not text:
            return
        media_t = self.media_base + self.audio_s
        if kind == "final":
            self._seq += 1
            self.lines += 1
            cue = {"type": "cue", "id": self._seq, "kind": "final",
                   "t": round(media_t, 2), "text": text,
                   "lang": lang, "speaker": speaker}
            self._recent.append(cue)
            del self._recent[:-40]
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
            cue = {"type": "cue", "id": target["id"] if target else self._seq,
                   "kind": "refine", "t": round(target["t"] if target else media_t, 2),
                   "text": text, "lang": lang, "speaker": speaker,
                   "replaces": [c["id"] for c in covered]}
            for c in covered:
                self._recent.remove(c)
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
        older = [c["text"] for c in self._recent if c["t"] < cue["t"]]
        return older[-mw_translate.CONTEXT_LINES:]

    def _translate_async(self, cue: dict):
        if self.lang and self.lang == self.viewer_lang:
            return
        # 문맥은 여기서 붙잡습니다. 번역 스레드가 도는 사이에도 자막은 계속
        # 들어오므로, 스레드 안에서 읽으면 그때의 `_recent`는 이 줄의 앞이
        # 아닙니다.
        ctx = self._context_for(cue)
        threading.Thread(target=self._translate, args=(cue, ctx),
                         daemon=True).start()

    def _translate(self, cue: dict, context: list[str] | None = None):
        src = cue.get("lang") or self.lang or ""
        if not src or src == self.viewer_lang:
            return
        if not self._tr.should_translate(cue["text"], src, self.viewer_lang):
            return
        try:
            out = self._tr.translate(cue["text"], src, self.viewer_lang, context)
        except Exception as exc:
            # 실패했다고 줄을 버리지 않습니다. 그러면 시청자에게는 그 발화가
            # 아예 없었던 것처럼 보입니다. 번역할 수 없었다는 사실이 남도록
            # 원문을 그 자리에 넣고, 왜 실패했는지는 로그에 적습니다.
            print(f"[live] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
            out = cue["text"]
        if not (out or "").strip():
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

    def stop(self):
        self._stop.set()
        if self._ff:
            self._ff.terminate()

    def _release(self):
        """Drop the models this session loaded.

        Each session builds its own RoutedASR and translator -- roughly 3GB
        resident once the Japanese recogniser and M2M-100 are in. Holding a
        finished session in the registry kept all of that alive: seven
        sessions in one afternoon reached 23GB.
        """
        self._asr = None
        self._tr = None
        self._recent.clear()
        if self._ff:
            try:
                self._ff.kill()
            except Exception:
                pass
            self._ff = None

    def _chunks(self):
        assert self._ff and self._ff.stdout
        need = CHUNK * 2
        while not self._stop.is_set():
            raw = self._ff.stdout.read(need)
            if not raw or len(raw) < need:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            self.audio_s += len(samples) / SAMPLE_RATE
            yield samples
        self.state = "stopped"

    def _run(self):
        # 이 넷을 미리 비워 둡니다. 아래 finally의 `del`이 이름을 지우는데,
        # try가 그 이름들이 만들어지기 전에 실패하면 `del`이 UnboundLocalError를
        # 냅니다. 그러면 **진짜 예외가 그 오류로 덮이고**, 더 나쁘게는 뒤따르는
        # _release()와 _retire()가 실행되지 않아 모델이 얹힌 채 남습니다
        # (세션 하나가 3GB입니다). 이슈 #1에서 실제로 그렇게 원인이 가려졌습니다.
        asr = vad = history = refiner = None
        try:
            meta = subprocess.run(stream.ytdlp_cmd() + ["--no-warnings", "-j", self.url],
                                  capture_output=True, text=True)
            if meta.returncode == 0:
                d = json.loads(meta.stdout)
                self.title = d.get("title", "")
                self.video_id = d.get("id", "") or ""
                if not d.get("is_live"):
                    self.state = "error"
                    # 방금 끝난 방송도 여기로 옵니다. /api/probe가 볼 때는
                    # 라이브였는데 그 사이 끝난 경우입니다.
                    self.error = ("라이브가 아닙니다. 방송이 방금 끝났거나 "
                                  "녹화본 주소일 수 있습니다. 녹화본은 "
                                  "「＋ 영상 추가」로 처리하십시오.")
                    self._persist()
                    self.emit({"type": "status", **self.status()})
                    return

            src, info = resolve_audio(self.url)
            release_ts = None
            if meta.returncode == 0:
                release_ts = d.get("release_timestamp") or d.get("timestamp")
            # Skipping the DVR window means the audio starts at the live edge;
            # media_base has to account for everything we deliberately passed.
            self.window_s = info.get("window_s") or 0.0
            self.media_base = media_base_from(info.get("pdt"), release_ts,
                                              self.window_s)
            print(f"[live] playlist: {info.get('segments')} segments / "
                  f"{self.window_s:.0f}s window, media_base={self.media_base:.0f}s",
                  flush=True)
            self.state = "loading"
            self._persist()
            self.emit({"type": "status", **self.status()})

            spec = asr_spec = None
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "backends.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            for b in cfg["backends"]:
                if b["id"] == self.backend_id:
                    spec = b
            for b in cfg.get("asr_backends", []):
                if b["id"] == self.asr_backend_id:
                    asr_spec = b
            self._tr = mw_translate.build(spec, self.genre)

            # Speaker tags are a recorded-video feature. CAM++ needs enough
            # voice in one segment to place it, and live splits at 3-4s to
            # keep up with a talker who rarely finishes a long sentence: in
            # 70 seconds that produced six speaker ids on a stream that did
            # not have six people talking. A label that invents speakers is
            # worse than no label.
            asr = self._asr = build_live_asr(asr_spec, self.lang, threads=4)
            self.asr_label = asr.label
            vad = build_vad(min_silence=self.min_silence,
                            max_speech=self.max_speech)
            sink = Sink(self)
            history = AudioHistory(SAMPLE_RATE)
            refiner = Refiner(asr, history, sink) if self.refine else None

            # -live_start_index -2 starts two segments from the end of the
            # playlist. Without it ffmpeg reads a full-DVR playlist from the
            # top and transcribes the broadcast's opening greetings while the
            # viewer watches its live edge.
            self._ff = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error",
                 "-live_start_index", "-2", "-i", src,
                 "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                stdout=subprocess.PIPE)

            self.state = "running"
            self._persist()
            self.emit({"type": "status", **self.status()})
            run_stream(self._chunks(), vad, asr, sink, history, refiner)
            self.state = "stopped"
            self._persist()
            self.emit({"type": "status", **self.status()})
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
            del asr, vad, history, refiner
            self._release()
            _retire(self.id)


def start(url: str, lang: str | None, viewer_lang: str, backend_id: str,
          profile: str = "broadcast", asr_backend_id: str = "",
          refine: bool = True, genre: str | None = None) -> dict:
    # One viewer watches one broadcast. Leaving the previous session running
    # would keep a second copy of every model resident for nothing.
    for old_id in list(_sessions):
        old = get(old_id)
        if old is not None:
            old.stop()

    s = LiveSession(url, lang, viewer_lang, backend_id, profile=profile,
                    asr_backend_id=asr_backend_id, refine=refine, genre=genre)
    with _lock:
        _sessions[s.id] = s
    # 첫 자막이 나오기 전에 서버가 죽어도 세션이 있었다는 사실은 남습니다.
    s._persist()
    s.start()
    return {"id": s.id}


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


def _retire(session_id: str):
    """Move a finished session out of the live registry. Its final status and
    its subtitles stay in SQLite, so a late poll -- or a poll after the next
    restart -- still gets an answer instead of a 404."""
    with _lock:
        s = _sessions.pop(session_id, None)
    if s is not None:
        s._persist()


def get(session_id: str) -> LiveSession | None:
    with _lock:
        return _sessions.get(session_id)


def status_of(session_id: str) -> dict | None:
    s = get(session_id)
    if s is not None:
        return s.status()
    return store.session(session_id)


def recent(limit: int = 20) -> list[dict]:
    """Sessions the viewer can go back to, newest first.

    A live session leaves no cue file, so before this it existed only for as
    long as the tab stayed open. The picker needs a list to offer.
    """
    live_now = {sid: s.status() for sid, s in _sessions.items()}
    out = []
    for row in store.sessions(limit):
        out.append({**row, **live_now.get(row["id"], {})})
    return out


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
    spec = None
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "backends.json"), encoding="utf-8") as f:
        for b in json.load(f)["backends"]:
            if b["id"] == backend_id:
                spec = b
    if spec is None:
        return {"error": f"'{backend_id}' 백엔드가 없습니다"}
    # 장르는 보고 있는 영상의 성질이므로 백엔드를 바꿔도 그대로입니다.
    s._tr = mw_translate.build(spec, s.genre)
    s.backend_id = backend_id
    return {"backend": backend_id}


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
    spec = None
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "backends.json"), encoding="utf-8") as f:
        for b in json.load(f).get("asr_backends", []):
            if b["id"] == asr_backend_id:
                spec = b
    if spec is None:
        return {"error": f"'{asr_backend_id}' 전사 엔진이 없습니다"}
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
