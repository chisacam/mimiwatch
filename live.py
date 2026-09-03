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
# The name is imported separately. The test (bench/live_errors.py) swaps
# `live.subprocess` for a fake, and that fake has no exception classes.
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

# The most audio (in seconds) to hold queued while receiving tab audio. This
# fills up when transcription cannot keep up with real time. The browser cannot
# slow playback down -- it is the sound the user is actually listening to -- so
# on overflow the oldest is dropped and how many seconds were dropped is written
# down. Better than silently falling behind and producing a subtitle 20 minutes
# later.
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
                  "label_en": "Talk · lecture (one person pausing between sentences)",
                  "label_ko": "발표·강연 (한 사람이 문장 사이에 쉼)"},
    "interview": {"max_speech": 6.0,  "min_silence": 0.35,
                  "label_en": "Panel · interview (taking turns, with pauses)",
                  "label_ko": "대담·인터뷰 (번갈아 말하고 쉼이 있음)"},
    "broadcast": {"max_speech": 4.0,  "min_silence": 0.30,
                  "label_en": "General stream (one or two people, short pauses)",
                  "label_ko": "일반 방송 (한두 사람, 쉼이 짧음)"},
    "collab":    {"max_speech": 3.0,  "min_silence": 0.25,
                  "label_en": "Collab · group conversation (speech overlaps)",
                  "label_ko": "합방·다인 대화 (발화가 겹침)"},
}

# How many times to reattach within the same session when HLS reception breaks.
# The waits in between grow to 3s, 6s, 9s… for a little over a minute in total.
# If it has not come back by then we give up and leave it to "Resume" -- that
# one is the user's call.
HLS_RECONNECT_TRIES = 5

# The length of one chunk (CHUNK). The ring and the clock use the same unit.
FRAME_S = CHUNK / SAMPLE_RATE

# How much recent audio (in seconds) an unfocused session holds. While another
# broadcast is being watched in multiview the sound keeps being received, and when
# focus returns transcription starts from here -- the few seconds before the switch
# stay in the subtitles and the first line appears right away. Audio older than
# this is dropped.
RING_S = 30.0


class Ring:
    """Recent audio between the reading thread and the transcribing thread.

    Reading ffmpeg and running the VAD and decoding used to be **one for loop in one
    thread**. Then the moment transcription stopped nobody was reading the pipe, so
    ffmpeg stalled, and a session that "keeps receiving sound but is not transcribing
    right now" -- which is what multiview needs -- was impossible. Reading only puts
    things in here, and the transcribing side only takes them out.

    An item is either audio `("audio", recv_s, ndarray)` or a marker `("flush",)`
    `("rebase", base, note)` `("end",)`. Every audio chunk carries the reading clock's
    value (recv_s) with it, so even when it is taken out after a long wait in the ring
    the subtitle's time is that chunk's real time. When full, **only audio** is
    dropped oldest-first and the markers are kept -- dropping the fact that a
    reconnect moved the time base would throw off the times of every chunk after it.
    """
    TIMEOUT = object()

    def __init__(self, max_s: float):
        self._d: deque = deque()
        self._cv = threading.Condition()
        self._audio = 0                  # how many audio items
        self.dropped_s = 0.0             # audio dropped on overflow (seconds)
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
        """Wait for room for audio. The focused session's reading thread uses this --
        when transcription is slow it stalls here instead of dropping, which is the same
        back pressure the ffmpeg pipe used to give."""
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

# How many recent events a session holds (for SSE reconnects). See LiveSession.emit.
EVENT_LOG_MAX = 2000
# The marker that tells "not in the record" from "absorbed (None)" in `_text_of`.
_UNKNOWN = object()

_sessions: dict[str, "LiveSession"] = {}
_lock = threading.Lock()
# **Only one session in the process** transcribes. The transcription model is one
# shared copy (models.py), and the bindings do not guarantee two sessions running the
# same model at once (tcpp_asr.py). When multiview focus moves, the new session takes
# this token only after the old one has let it go -- the sound in between is held by
# the new session's ring, so nothing is lost.
_transcriber = threading.Lock()

# SQLite holds a finished session's last status. Previously an in-memory dict kept
# only the most recent 20, which was lost on restart anyway, and a session past the
# cap became a 404 even on a running server.


_TWITCH_LOGIN = re.compile(r"twitch\.tv/(?!videos/)([A-Za-z0-9_]+)", re.I)
_M3U8 = re.compile(r"\.m3u8(\?|$)", re.I)


def site_of(d: dict, url: str = "") -> dict:
    """Pick out of a yt-dlp `-j` result only what the UI needs for embedding.

    The UI used to put anything with a `video_id` into the YouTube player. Now that
    Twitch and raw m3u8 are accepted too, the server has to say which site it is -- a
    Twitch id is a numeric stream number, and putting it in the YouTube player shows
    nothing.

      site     "youtube" | "twitch" | "other"
      channel  the Twitch login name (the embed finds the channel by it). Empty elsewhere
      video_id yt-dlp's id as it is (the video id on YouTube)
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
    # The `worst` at the end is the smallest HLS with video muxed in. In 2026-08 a
    # seven-week-old yt-dlp got no audio-only format at all and live died outright --
    # ffmpeg pulls just the sound out of a muxed stream too, so the pipe only gets a
    # little fatter and transcription still works. 234/233 are YouTube's audio-only
    # itags. On other sites they spend a few seconds asking twice for something that
    # is not there, so they are skipped.
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
    # Carry what yt-dlp said verbatim. "Could not resolve" on its own does not
    # tell you where to look -- whether an update is needed, whether a login is
    # needed, or whether it was never live in the first place is written in
    # that line.
    ver = ytdlp_version()
    hint = ""
    if all("format is not available" in w for w in why):
        # If all three are missing, it is not that one format is absent but
        # that the whole list did not come back. Almost always because yt-dlp
        # is old.
        hint = (f" — 포맷을 하나도 받지 못했습니다. yt-dlp({ver or '판 미상'})가 "
                f"낡았을 수 있습니다"
                + (" (45일 넘음)" if ytdlp_stale(ver) else "")
                + ". 「엔진 관리 › 모델·도구」에서 yt-dlp 독립 실행 파일을 (다시) 받거나 "
                  "`yt-dlp --update-to nightly` 로 올린 뒤 다시 해 보십시오.")
    raise RuntimeError("yt-dlp가 이 주소에서 오디오를 찾지 못했습니다."
                       + hint + " [" + " / ".join(why) + "]")


def ytdlp_version() -> str:
    """The installed yt-dlp's version. An empty string if it cannot be asked."""
    try:
        out = subprocess.run(stream.ytdlp_cmd() + ["--version"], capture_output=True,
                             text=True, timeout=20,
                             **stream.child_io(stderr=False))
        return (out.stdout or "").strip().splitlines()[0] if out.returncode == 0 else ""
    except Exception:
        return ""


def ytdlp_stale(version: str, days: int = 45) -> bool:
    """Is this version old.

    A yt-dlp version is YYYY.MM.DD. YouTube changes its extraction path often
    and yt-dlp follows every time, so a version a few months old commonly
    fails to get the format list at all. The bar was lowered from 90 days to
    45 -- in a 2026-08 measurement a version seven weeks old already got no
    live audio format whatsoever. Then 234, 233 and bestaudio all become
    "Requested format is not available" -- not that the format is missing, but
    that nothing could be read.
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
            # #EXT-X-ENDLIST means a finished playlist (a recording). If an address
            # we do not know to be live (other) carries this, there is nothing to
            # reattach to once ffmpeg ends.
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


# Which final lines a refined line absorbed must not be decided by whether the
# earlier characters are contained verbatim. Refinement re-decodes the joined
# audio, so the same speech comes out slightly different (measured: `무기도
# 풀제열이야?` became `무기도 풀제일이야?`). Then the absorption test misses and
# the rough final is left standing next to the refined line. It is less visible on
# screen, but both pile up in storage, so a reload shows the same utterance twice.
# So we look at how much overlaps, not at an exact text match.
COVER_RATIO = 0.6
# A refined line is one utterance group joined and re-decoded, so it never reaches
# back to absorb lines much older than that group.
COVER_WINDOW_S = 30.0
# Short lines must not be judged by similarity. The two characters `はい` are in
# every refined line, so a loose test swallows unrelated ones too.
COVER_EXACT_BELOW = 4
# A run of unrecognised lines this long in the middle of a group still counts as
# the same group.
COVER_GAP = 2


def _covers(final_text: str, refined: str) -> bool:
    """Does this refined line contain this final line."""
    a = final_text.strip()
    if not a:
        return False
    if len(a) < COVER_EXACT_BELOW:
        return a in refined
    matched = sum(b.size for b in
                  difflib.SequenceMatcher(None, a, refined).get_matching_blocks())
    return matched / len(a) >= COVER_RATIO


class Sink:
    """Hand the lines the transcription loop produces to the session's publishing path."""

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
        # Where the sound comes from. "hls" means the server resolves the address
        # with yt-dlp and pulls it directly with ffmpeg; "tab" means the browser
        # uploads what it hears in its own tab. It is the route for what the server
        # cannot receive, such as a members-only broadcast -- no cookies needed
        # either, and the tab is one the user picked in the share dialog themselves.
        self.source = "tab" if source == "tab" else "hls"
        self.lang = lang
        self.viewer_lang = viewer_lang
        self.backend_id = backend_id
        self.asr_backend_id = asr_backend_id
        # The profile (content type) decides how many seconds an utterance is cut
        # at; the genre decides what vocabulary that utterance is translated into.
        # They look like they overlap but they are different axes -- a gaming stream
        # and a chatting stream split at the same interval but use different words,
        # and a technical talk exists as a recording too.
        self.genre = genre if genre in mw_translate.GENRE_PROMPTS \
            else mw_translate.DEFAULT_GENRE
        # Refinement waits 2 seconds for one utterance group to end, then joins it
        # and decodes again. The longer context makes the result better, but the
        # subtitle settles that much later and a line already on screen changes
        # wholesale. On a broadcast where speech goes back and forth quickly,
        # cutting short and sending out at once can be easier to follow, so it is
        # left switchable.
        self.refine = refine
        prof = PROFILES.get(profile, PROFILES["broadcast"])
        self.profile = profile if profile in PROFILES else "broadcast"
        self.max_speech = prof["max_speech"]
        self.min_silence = prof["min_silence"]
        self.state = "starting"
        self.error: str | None = None
        # Why it stopped. "user" is pressing "Stop" or shutting the server down,
        # "ended" is the broadcast having finished, "stream" is reception breaking
        # with no reattach. If all three looked like `stopped`, the UI could not
        # decide whether to offer a resume.
        self.stopped_by = ""
        # On resume, the media position received up to just before the break.
        # 0 means a session starting fresh, with nothing to rewind to.
        self.resume_from = 0.0
        # Whether to rewind to resume_from and fill the hole. Only the automatic
        # reconnect inside a session does that -- it is a few seconds since the break,
        # so it picks straight up inside the DVR window. The user's "Resume" does not
        # rewind: resuming after an hour's stop meant transcribing that hour first, so
        # the subtitle for the place being watched came out much later. It starts at
        # the live edge now and writes the gap down as one line.
        self._rewind = False
        # The stretch that could not be filled (seconds). Above 0, it is written
        # into the subtitles.
        self.gap_s = 0.0
        # yt-dlp fills this in for hls; the browser supplies it for tab.
        self.title = title
        # Did a person choose this name? yt-dlp's title is a guess we can improve
        # on, never a correction, so once someone renames a session nothing may
        # write over it -- see set_title and _resolve_hls.
        self.title_by_user = False
        # Reopening this session after a restart needs a video id to embed.
        # The session id is one we made, so it cannot go into the player.
        self.video_id = ""
        self.media_base = 0.0        # media seconds at the first sample we get
        self.window_s = 0.0          # DVR window we skipped to reach live
        self.audio_s = 0.0           # seconds fed so far
        self.started = time.time()
        self.lines = 0
        self.translated = 0
        self._seq = 0
        self._subs: list[queue.Queue] = []
        # A recent record of the events that went out. When a subscriber that broke
        # off reattaches carrying `Last-Event-ID`, only what it missed is resent from
        # here -- not the whole backlog. 2000 is comfortably a little over an hour of
        # a few hundred subtitle lines plus their translations and statuses.
        self._eseq = 0
        self._elog: deque[tuple[int, str]] = deque(maxlen=EVENT_LOG_MAX)
        self._stop = threading.Event()
        # The publishing path's lock. Final lines go in from the receiving thread
        # and refined lines from the refinement thread -- if the two touch `_recent`
        # at once, one's `remove` tangles with the other's `del [:-40]` and either
        # raises ValueError or absorbs the wrong line.
        self._pub_lock = threading.RLock()
        self._ff: subprocess.Popen | None = None
        # Between the reading side (the ffmpeg thread, or feed() for a tab) and the
        # transcribing thread. See Ring. There are two clocks -- `_recv_*` is where
        # the reading side has **received** to, and `media_base`/`audio_s` is where
        # **the chunk being decoded right now** sits. On a session receiving alone the
        # two move together, but when sound pools in the ring the latter runs behind.
        #
        # The browser cannot slow tab audio down (it is the sound the user is
        # listening to), so when transcription falls behind the ring is made long
        # (INGEST_MAX_S), and on overflow the oldest is dropped and how many seconds
        # were dropped is written down. Better than silently falling behind and
        # producing a subtitle 20 minutes later. An address session's pipe gives back
        # pressure, so its ring never overflows.
        self._ring = Ring(INGEST_MAX_S if self.source == "tab" else RING_S)
        self._recv_base = 0.0
        self._recv_s = 0.0
        self.dropped_s = 0.0        # sound dropped on ring overflow (s). feed() returns it
        self._ended = False         # reading ended (broadcast over, or given up); with ("end",)
        self._rx: threading.Thread | None = None
        # Focus: is this session's sound being transcribed right now. A session
        # receiving alone is focused from birth and is only turned off in multiview
        # (set_focus). The ring keeps filling while there is no focus too.
        self._focus = threading.Event()
        self._focus.set()
        self.group = ""             # multiview bundle id. Empty means a session receiving alone
        self.site = ""              # "youtube" | "twitch" | "other" (site_of). Picks the embed
        self.channel = ""           # the Twitch login name
        self._warm_persisted_s = 0.0   # the _recv_s at the last status write while on standby
        self._asr = None            # released on stop; see _release()
        # The recogniser object is let go when the session ends, but which engine
        # it was has to stay. Reading it off the object each time meant the last
        # status write, which happens after it is let go, overwrote it with the
        # default and the record lied.
        self.asr_label = ""
        self._tr = None
        # Refine replaces the final that covered the same speech. Matching on
        # the text hayamimi already emitted is enough here because a refined
        # group repeats its members' words.
        self._recent: list[dict] = []
        # Translation happens in the order things were put in, by **one worker
        # thread**.
        #
        # Previously a new thread was started for every subtitle line. There was one
        # model lock anyway so they could not run side by side, a two-hour broadcast
        # created thousands of threads, and above all **the order they finished in
        # was not fixed.** A refined line inherits the id of the first final line it
        # absorbed, so if that final line's translation thread finished later than
        # the refined line's, the old partial translation was published and stored
        # under the same id and the wrong translation was left under the refined
        # line. With one consumer draining one queue the order is the publishing
        # order, and `_text_of` below skips translating a line already replaced --
        # which saves that much Gemma time too.
        self._tr_q: queue.Queue = queue.Queue()
        self._tr_thread: threading.Thread | None = None
        # The source text currently on screen, per id. Used to check, when a
        # translation finishes, whether that line is still this text. A line a
        # refined line absorbed drops out of here, and the inherited id changes to
        # the refined line's text.
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
        """The events that went out after `last_id`. None if it is outside the record
        -- then the whole backlog."""
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
                "title": self.title, "title_by_user": self.title_by_user,
                "url": self.url, "video_id": self.video_id,
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
                # Separate from where transcription reached (media_base+audio_s):
                # where the reading side received to. On a session with no focus the
                # former stands still while the latter keeps going. Resume uses the latter.
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
        # Tell every screen looking at the list -- a new session, the line count, a
        # state change. It arrives on every subtitle line, but the screen only fixes
        # that one line in place, so it is light.
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
            # The line count is part of the status, so the status is written along
            # with every subtitle line. It is once every few seconds so it costs
            # nothing, and how far transcription got is accurate after a restart.
            self._persist()
            self._translate_async(cue)
        else:
            # A refined group supersedes the finals whose words it contains.
            # A refined line is one utterance group joined, so what it absorbs has
            # to be contiguous like that group. Picking from anywhere means a short
            # interjection like `はい` matches something from long ago, and the
            # refined line inherits that old line's time and id and wedges itself
            # into a past subtitle slot.
            #
            # But it must not be scanned from the tail. Refinement arrives after
            # waiting 2 seconds of silence, so the next utterance's final has come
            # in by then, and stopping there absorbs nothing and leaves the same
            # speech as two lines. So the longest contiguous run is found regardless
            # of where it is.
            hits = [i for i, c in enumerate(self._recent)
                    if c["kind"] == "final"
                    and media_t - c["t"] <= COVER_WINDOW_S
                    and _covers(c["text"], text)]
            # Bundle the matched lines into runs. One or two in the middle of a
            # group commonly fail the test -- when the refinement re-decode diverges
            # badly (`いや空込みだ` becoming `川上だ`, say) only that line goes
            # unrecognised. Splitting the group there absorbs one side and leaves
            # the rest as duplicates, so a gap that small is stepped over.
            groups: list[list[int]] = []
            for i in hits:
                if groups and i - groups[-1][-1] <= COVER_GAP + 1:
                    groups[-1].append(i)
                else:
                    groups.append([i])
            best = max(groups, key=len) if groups else []
            # The biggest run replaces everything inside it, the missed lines
            # included. A refined line is one whole group transcribed again.
            covered = ([self._recent[i] for i in range(best[0], best[-1] + 1)]
                       if best else [])
            target = covered[0] if covered else None
            if target is None:
                # Not one final line to absorb was found (the re-decode diverged
                # badly, or those lines have already been pushed out of `_recent`).
                # Previously `self._seq` -- that is, **the most recent line's
                # number** -- was used as it was, but that line can be the next
                # utterance, unrelated to this refined line. Then that utterance's
                # source text was overwritten and its translation emptied too. A
                # refined line that found no match goes in as a new line.
                self._seq += 1
                self.lines += 1
            cue = {"type": "cue", "id": target["id"] if target else self._seq,
                   "kind": "refine", "t": round(target["t"] if target else media_t, 2),
                   "text": text, "lang": lang, "speaker": speaker,
                   "replaces": [c["id"] for c in covered]}
            for c in covered:
                self._recent.remove(c)
                self._text_of[c["id"]] = None      # absorbed. Dropped from the translation queue
            self._text_of[cue["id"]] = text
            # A line a refined line absorbed disappears from the screen, so it is
            # deleted from storage too. Only the one line whose id was inherited is
            # kept, and that slot is overwritten with the refined line.
            store.drop_cues(self.id, [c["id"] for c in covered
                                      if c["id"] != cue["id"]])
            store.save_cue(self.id, cue)
            self.emit(cue)
            self._translate_async(cue)

    def _context_for(self, cue: dict) -> list[str]:
        """The few subtitle lines just before this one. Passed to the translator as context.

        Only **what is earlier than this line** is taken from `self._recent`. For
        a final line, it has just appended itself at the end; for a refined line,
        the lines it absorbed are already gone but the later lines that came in
        during the wait remain. Filtering by time handles both cases with one rule.
        """
        # A notice we wrote ourselves (kind=note) is not speech. Putting it in the
        # context makes the translator read "서버가 멈춘 사이 …" as the preceding
        # sentence and translate it.
        older = [c["text"] for c in self._recent
                 if c["t"] < cue["t"] and c.get("kind") != "note"]
        return older[-mw_translate.CONTEXT_LINES:]

    def _trim_text_of(self, keep: int = 500):
        """Keep `_text_of` from growing with the length of the broadcast. Translation
        queues up right after publishing, so there is never a reason to look at
        something several hundred lines back.

        For a line that was trimmed away `_superseded` answers **unknown**, and an
        unknown line gets translated. Previously "absent" was read as "absorbed", so
        on a slow machine where the translator fell more than 500 lines behind those
        lines were never translated. An absorbed line is left as None to keep the two
        apart.
        """
        if len(self._text_of) > keep * 2:
            for k in sorted(self._text_of)[:-keep]:
                del self._text_of[k]

    def _translate_async(self, cue: dict):
        # A notice we wrote ourselves. Not something to hand the translator.
        if cue.get("kind") == "note":
            return
        if self.lang and self.lang == self.viewer_lang:
            return
        # The context is captured here. Subtitles keep coming in while a
        # translation waits its turn, so reading it inside the worker thread would
        # not give what came before this line.
        ctx = self._context_for(cue)
        self._tr_q.put((dict(cue), ctx))
        if self._tr_thread is None or not self._tr_thread.is_alive():
            self._tr_thread = threading.Thread(target=self._translate_loop,
                                               daemon=True, name=f"tr-{self.id}")
            self._tr_thread.start()

    def _translate_loop(self):
        while True:
            item = self._tr_q.get()
            if item is None:                      # the end marker _release() sent
                return
            cue, ctx = item
            try:
                self._translate(cue, ctx)
            except Exception as exc:              # one line's failure must not block the rest
                print(f"[live] 번역 루프 오류: {exc}", file=sys.stderr, flush=True)

    def _superseded(self, cue: dict) -> bool:
        """Has this line been absorbed by a refined line, or its text changed, in the
        meantime. A line trimmed out of the record, and so unknown, counts as still
        alive."""
        cur = self._text_of.get(cue["id"], _UNKNOWN)
        return cur is not _UNKNOWN and cur != cue["text"]

    def _translate(self, cue: dict, context: list[str] | None = None):
        src = cue.get("lang") or self.lang or ""
        if not src or src == self.viewer_lang:
            return
        # If a refined line absorbed this line while it waited its turn there is
        # nothing to translate. The refined line's own translation is queued behind.
        if self._superseded(cue):
            return
        # Held once. When the session ends `_release()` sets `_tr` to None, but the
        # lines left in the queue are drained before that (`_close_translator`).
        tr = self._tr
        if tr is None:
            return
        if not tr.should_translate(cue["text"], src, self.viewer_lang):
            return
        try:
            out = tr.translate(cue["text"], src, self.viewer_lang, context)
        except Exception as exc:
            # A line is not thrown away because it failed. That would make the
            # utterance look to the viewer as if it never happened. The source text
            # goes in that slot so the fact that it could not be translated remains,
            # and why it failed goes into the log.
            print(f"[live] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
            out = cue["text"]
        if not (out or "").strip():
            return
        # A refined line may have arrived during the few hundred milliseconds of
        # translating. Then this result is the translation of text that is no longer
        # on screen, and it would overwrite the translation of the refined line that
        # inherited the same id -- so it is discarded.
        if self._superseded(cue):
            return
        # The translation is stored even when it equals the source text. Leaving a
        # proper noun or a short interjection as it is is the correct translation,
        # and previously this case was read as a failure and the line vanished.
        self.translated += 1
        store.save_translation(self.id, cue["id"], self.backend_id, out)
        self.emit({"type": "translation", "id": cue["id"],
                   "kind": cue["kind"], "text": out})

    # ---- pipeline ---------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def set_focus(self, on: bool):
        """Whether to transcribe this session's sound. Turned off, the session stays
        alive and keeps receiving sound (the ring).

        Turned on, the transcribing thread takes the token (_transcriber) and decodes
        starting from what pooled in the ring. Turned off, the run_stream now running
        finalises the utterance it is holding and withdraws -- the token is released
        after that.
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
            # The browser cannot slow tab audio down, so while focused a long
            # stretch is held. Without focus there is no reason to hold that much --
            # 30 seconds is enough for when it comes back.
            self._ring.set_max(INGEST_MAX_S if on else RING_S)
        self._persist()
        self.emit({"type": "status", **self.status()})

    def stop(self):
        self.stopped_by = self.stopped_by or "user"
        self._stop.set()
        # Read once. The reading thread's `_reap_ff` can set `_ff` to None in the
        # meantime, so reading it twice raised AttributeError here.
        ff = self._ff
        if ff:
            ff.terminate()
        # A tab session has no reading thread, so nobody puts ("end",) in. The
        # transcribing side is waiting on the ring, so it is woken here directly.
        if self.source == "tab":
            self._ended = True
            self._ring.push(("end",))

    # ---- tab audio intake -------------------------------------------------
    def feed(self, raw: bytes) -> dict:
        """One block of 16kHz mono int16 PCM the browser uploaded. This is a tab
        session's "reading".

        It is cut into the 0.1s chunks the VAD takes and put into the ring -- the same
        size as the ffmpeg path. run_stream feeds the VAD one chunk at a time and
        measures the refinement point off it, so handing over a whole 2 seconds makes
        both of those coarse together. On ring overflow the oldest is dropped (without
        waiting -- the browser is waiting on this request) and how many seconds were
        dropped is returned.
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
        """Where in the playlist to start reading to receive again from the break.

        YouTube serves even a broadcast in progress with some rewind available
        (the DVR window). If the time the server was down falls inside that
        window, it can be joined back up **without losing a single chunk**. If
        it was down longer than the window, what could not be filled is left in
        `gap_s` and written on screen as such.

        Returns (ffmpeg's -live_start_index, seconds skipped).
        """
        first = media_base_from(info.get("pdt"), release_ts, 0.0)
        segs = int(info.get("segments") or 0)
        seg_dur = (self.window_s / segs) if segs else float(info.get("target") or 2.0)
        want = self.resume_from - first        # how many seconds to skip from the playlist's front

        if seg_dur <= 0 or self.window_s <= 0:
            # The window could not be read. Receive from the live edge without
            # attempting a rewind, and since how much was lost is unknown, do
            # not write it down.
            return -2, self.window_s
        if want <= 0:
            # The point where we stopped has already been pushed out of the
            # window. Receive from the oldest thing left, and write the space
            # in between down as lost.
            self.gap_s = max(0.0, -want)
            return 0, 0.0
        if want >= self.window_s:
            # There is nothing inside the window left unfilled -- the point
            # where we stopped is still past the live edge, so just resume
            # from the edge.
            self.gap_s = 0.0
            return -2, self.window_s
        idx = max(0, int(want / seg_dur))
        self.gap_s = 0.0
        return idx, idx * seg_dur

    def _release(self):
        """Let go of what this session holds.

        `models.py` now keeps one copy of the model weights per process, so
        what is let go here is this session's decode session, the translator
        shell and the recent lines. Previously every session loaded the model
        anew and a finished session stayed in the registry holding on to it --
        one afternoon reached 23GB with seven sessions. Sharing the model
        removed that problem at the root, and this stays as the place where
        the references are cut.
        """
        # The reading thread may be waiting for room in the ring. The transcribing
        # side is gone, so that wait has to end too -- this flag is that loop's
        # exit condition.
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
            self._reap_ff()

    def _reap_ff(self, timeout: float = 2.0):
        """Reap the ffmpeg that ended (or was just killed).

        Previously only terminate/kill was called and never wait, so a finished
        ffmpeg stayed `<defunct>` until the next Popen came up. One piled up per
        reconnect and they looked like orphans in ps. A zombie eats no memory,
        but it is grounds for a user to say "processes are being left behind".
        If it does not end within 2 seconds (having missed the pipe closing) it
        is left alone -- it gets reaped anyway when the parent ends.
        """
        ff, self._ff = self._ff, None
        if ff is None:
            return
        try:
            ff.wait(timeout=timeout)
        except Exception:
            pass

    def _close_translator(self, timeout: float = 10.0):
        """End the translation worker thread. It translates what is queued before
        leaving.

        If the last few lines' translations disappeared because the session ended,
        the goodbye at the end of a broadcast would be left as source text only.
        It does not wait forever, though -- if the translator is stuck it is let
        go after 10 seconds.
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
        # `-nostdin`: we take the sound over the stdout pipe. ffmpeg has no
        # reason to look at standard input, and this also stops it taking key
        # presses when run in a terminal. For standard I/O see stream.child_io
        # -- that is where inheriting the parent's handles used to fall over on
        # Windows.
        self._ff = subprocess.Popen(
            [stream.ffmpeg_cmd(), "-loglevel", "error", "-nostdin",
             "-live_start_index", str(start_index), "-i", src,
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, **stream.child_io())

    def _start_reader(self, src, start_index):
        """Stand ffmpeg up and start the thread that reads it. A tab session has
        nothing to do here because `feed()` plays the reading role."""
        if self.source == "tab":
            return
        self._spawn_ffmpeg(src, start_index)
        self._rx = threading.Thread(target=self._read_loop, name=f"rx-{self.id}",
                                    daemon=True)
        self._rx.start()

    def _push_audio(self, samples):
        """Put a chunk that was read into the ring. On a focused session it waits
        until there is room -- not dropping even when transcription is slow was the
        old pipe's behaviour. Without focus, the ring drops the oldest."""
        item = ("audio", self._recv_s, samples)
        while (self._ring.full() and self._focus.is_set()
               and not self._stop.is_set()):
            self._ring.wait_room(0.2)
        self._ring.push(item)
        # A standby session has no subtitles, so nothing triggers a status write.
        # It is written now and then so that "how far it received" (recv_t) is not
        # left hours behind after a restart.
        if (not self._focus.is_set()
                and self._recv_s - self._warm_persisted_s >= RING_S):
            self._warm_persisted_s = self._recv_s
            self._persist()

    def _read_loop(self):
        """Put the sound ffmpeg produces into the ring as 0.1s chunks. **On a break
        it reattaches within the same session.** Runs on the reading thread.

        Previously the session was "ended" as soon as ffmpeg finished. The broadcast
        being over and the playlist briefly not arriving had the same outcome, and a
        two-hour broadcast that broke once every 30 minutes split its subtitles into
        new sessions. Now, when ffmpeg ends by itself, whether the broadcast is still
        in progress is asked again, and if it is, reception resumes at the break
        (without losing a single chunk if it is inside the DVR window). The stretch
        that could not be filled is written into the subtitles -- the same rule as
        resume.

        A `("flush",)` marker is put in once between chunks to finalise the utterance
        being held. At the end `("end",)` goes in -- the transcribing side sees that
        and withdraws.
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
                attempt = 0                  # when sound arrives the retry count starts over
                self._push_audio(samples)
            if self._stop.is_set():
                break
            # ffmpeg ended by itself. Either the broadcast is over, or the
            # playlist briefly did not arrive. Reap it first (so it is not left
            # a zombie), then finalise the utterance being held.
            self._reap_ff()
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
                # Not attached yet. Wait a moment, then on to the next attempt.
                # If stop() comes the wait ends right away.
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
        """Take chunks out of the ring and hand them to `run_stream`. Runs on the
        transcribing thread.

        An audio chunk arrives carrying the reading clock's value, so `audio_s` is
        **set to that value** (not added to). Even when 30 seconds that pooled in the
        ring are decoded in one go, each line's time is where that chunk actually sat
        in the broadcast. The markers are handled in order -- flush finalises the
        utterance being held (None), rebase is the new time base after a reconnect
        plus the notice about the missing stretch, and end means reading has finished.
        On losing focus it finalises only the utterance being held and withdraws; the
        session stays alive and keeps receiving sound.
        """
        idle_flush = self.source == "tab"   # only a tab flushes on 2s of silence -- as before
        idle = False
        waited = 0.0
        while not self._stop.is_set() and self._focus.is_set():
            # Wait briefly. When focus moves, noticing that much sooner is what lets
            # the next session get the token. The silence test (2 seconds) is made by
            # adding up the time waited.
            item = self._ring.pop(timeout=0.5)
            if item is Ring.TIMEOUT:
                waited += 0.5
                # No sound is arriving -- on a tab, the video was paused or the
                # share was cut. Instead of waiting for silence that will not come,
                # finalise the utterance being held.
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
        # Only focus was lost. Finalise the utterance being held and withdraw quietly.
        yield None

    def _reconnect(self, attempt: int) -> str:
        """Stand ffmpeg back up at the break.

        Returns "ok" (attached) / "ended" (the broadcast is over) / "retry"
        (could not attach right now). If it attached, `self._ff` is the new
        process.
        """
        # Where reception got to so far. The new playlist's time base
        # (_recv_base) is recomputed here, so _recv_s counts from 0 again --
        # media_t = base + s.
        self.resume_from = self._recv_base + self._recv_s
        self._rewind = True                  # the break just now is inside the DVR window. Fill it
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
        # The new time base and the notice about the missing stretch go **through**
        # the ring. Publishing them directly here would make them arrive ahead of the
        # old chunks still in the ring, and those chunks would be stamped with the
        # new base.
        note = (f"⋯ 수신이 끊겨 약 {int(self.gap_s)}초를 받지 못했습니다 ⋯"
                if self.gap_s >= 1.0 else "")
        self._ring.push(("rebase", self._recv_base, note))
        self._spawn_ffmpeg(src, start_index)
        self.gap_s = 0.0
        return "ok"

    def _run(self):
        # This only decides where the sound comes from; _transcribe does the
        # transcribing. The finally that lets the model go is over there -- this
        # function's finally only takes the session out of the registry, so it
        # runs no matter what fails. (Previously it `del`d the model names here,
        # and when the try failed before it had made those names,
        # UnboundLocalError covered the real exception and skipped _release()
        # too, leaving one session sitting at 3GB. Issue #1.)
        try:
            src = start_index = None
            if self.source == "hls":
                src, start_index = self._resolve_hls()
                if src is None:
                    return      # _resolve_hls has already reported the error
                self.media_base = self._recv_base   # no thread yet, so just copy it
            else:
                # Tab audio has neither a full playlist nor a broadcast time to
                # line up with. The moment the user is listening to is 0 seconds
                # -- which is in fact more accurate for aligning subtitles over
                # the player, because the user's playback position is the
                # reference.
                #
                # On a resume the time axis continues from where it stopped.
                # Going back to 0 would wedge new subtitles in among the old ones
                # and tangle the transcript's order. On a new session resume_from
                # is 0.
                self.media_base = self._recv_base = self.resume_from
            self.state = "loading"
            self._persist()
            self.emit({"type": "status", **self.status()})
            self._transcribe(src, start_index)
        except Exception as exc:
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            # Only one line goes to the screen. Where it came from has to stay
            # in the log for the next report to be diagnosable.
            print(f"[live] 세션 {self.id} 실패:", file=sys.stderr)
            traceback.print_exc()
            self._persist()
            self.emit({"type": "status", **self.status()})
        finally:
            self._release()
            _retire(self)

    def _resolve_hls(self, reconnect: bool = False):
        """Resolve the broadcast address into something ffmpeg can read.

        Returns (playlist address, -live_start_index); a first value of None
        means there is no going further -- the status and the error have already
        been reported here. On `reconnect`, "not live" is not an error but the
        broadcast being over, so it returns None without touching the status.
        """
        d = {}
        try:
            meta = subprocess.run(stream.ytdlp_args("-j", url=self.url),
                                  capture_output=True, text=True,
                                  timeout=stream.YTDLP_TIMEOUT_S,
                                  **stream.child_io(stderr=False))
        except TimeoutExpired:
            # Even if the metadata does not come back, resolve_audio below tries
            # once more. If that fails too it raises with the reason attached.
            meta = subprocess.CompletedProcess(args=[], returncode=-1,
                                               stdout="", stderr="시간 초과")
        if meta.returncode == 0:
            try:
                d = json.loads(meta.stdout)
            except json.JSONDecodeError:
                d = {}                    # a playlist address. resolve_audio below decides
            # Two ways this line used to destroy a good name. A playlist address
            # parses to {} above, so the title became "" -- the session lost the
            # name it was listed under. And a resume re-resolves the URL, which put
            # the fetched title back over one the user had typed; set_title exists
            # because tab audio has no title to fetch, and a rename that survives
            # only until the next reconnect is not a rename.
            if d.get("title") and not self.title_by_user:
                self.title = d["title"]
            self.video_id = d.get("id", "") or ""
            info = site_of(d, self.url)
            self.site, self.channel = info["site"], info["channel"]
            # yt-dlp's generic extractor does not know whether a raw m3u8 is live
            # (is_live is None). The user entered it as live, so unknown counts as
            # live. Only an explicit no (False) blocks it.
            not_live = (d.get("is_live") is False if self.site == "other"
                        else not d.get("is_live"))
            if not_live:
                if reconnect:
                    return None, None
                self.state = "error"
                # A broadcast that has just ended comes here too -- it was live
                # when /api/probe looked and ended in the meantime.
                self.error = ("라이브가 아닙니다. 방송이 방금 끝났거나 "
                              "녹화본 주소일 수 있습니다. 녹화본은 "
                              "「＋ 영상 추가」로 처리하십시오.")
                self._persist()
                self.emit({"type": "status", **self.status()})
                return None, None

        src, info = resolve_audio(self.url, youtube=self.site != "other" and self.site != "twitch")
        if reconnect and info.get("ended"):
            # The playlist says itself that it is finished (a recording's m3u8).
            # yt-dlp does not know whether it is live so it never says "ended", and
            # without recognising it here ffmpeg reattaches to the same tail every
            # time it finishes and transcribes the same speech over again -- which
            # it actually did dozens of times.
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
            # Resume: start at the live edge. The distance from where it stopped
            # is the missing stretch.
            self.gap_s = max(0.0, self._recv_base - self.resume_from) if self._recv_base else 0.0
        print(f"[live] playlist: {info.get('segments')} segments / "
              f"{self.window_s:.0f}s window, media_base={self._recv_base:.0f}s"
              + (f", 이어받기 index={start_index} 빠진 구간={self.gap_s:.0f}s"
                 if self.resume_from else ""),
              flush=True)
        return src, start_index

    def _transcribe(self, src, start_index):
        """Take sound in and send subtitles out. It does not know where the sound
        comes from.

        Whether it is an ffmpeg pipe or chunks the browser uploaded, the path is the
        same from here -- all `run_stream` takes is a generator producing float32
        chunks.

        Transcription happens only **while focus is held** (_episode). A session
        receiving alone is focused from the start so it has just one episode; in
        multiview there is one for every time focus comes and goes. In between, this
        thread waits for focus and the reading thread fills the ring.
        """
        self._start_reader(src, start_index)   # nothing to do for a tab -- feed() puts it in
        self.state = "running"
        self._persist()
        self.emit({"type": "status", **self.status()})
        if self.gap_s >= 1.0:
            # No silent hole is left behind. If there is a stretch a rewind
            # could not fill, the transcript says so -- there being no subtitle
            # and not having been able to transcribe are two different stories.
            self.publish_line(
                "note",
                f"⋯ 서버가 멈춘 사이 약 {int(self.gap_s)}초를 받지 "
                f"못했습니다 ⋯", self.lang or "", "")
        if self.source == "tab" and self.resume_from:
            # Tab audio cannot be rewound. The sound from while the share was
            # cut is nowhere, so not even how many seconds it was is known.
            self.publish_line(
                "note", "⋯ 여기서부터 탭 소리를 다시 받습니다. 공유가 "
                "끊긴 사이는 받지 못했습니다 ⋯", self.lang or "", "")
        while not self._stop.is_set() and not self._ended:
            if not self._focus.wait(0.5):
                continue                 # a standby session: only the ring is filling
            self._episode()
        if self.state != "error":        # if the receive loop gave up, leave its word standing
            self.state = "stopped"
        self._persist()
        self.emit({"type": "status", **self.status()})

    def _ensure_engines(self):
        """Build the transcriber and the translator once, on first getting focus. The
        process shares the weights (models.py) so what a session holds is only the
        decode session and a shell, and it is not let go on losing focus -- the next
        focus uses it right away. An engine changed under "Manage" while on standby is
        written in asr_backend_id/backend_id and applied here."""
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
            # The engine name has to ride in the status for the UI to write
            # "transcription <engine>".
            self._persist()
            self.emit({"type": "status", **self.status()})

    def _episode(self):
        """One run_stream for as long as focus is held. Runs inside the process's
        transcription token.

        The VAD, the audio history and the refiner are built anew every time -- all
        three go by a sample position that puts run_stream's start at 0, so carrying
        them across the gap where there was no focus makes the lead-in and the
        refinement source point at the wrong sound. The refiner's close() has to
        finish inside the token too, so that this side's last decode is done by the
        time another session starts running the same model.
        """
        while not _transcriber.acquire(timeout=0.5):
            if not self._focus.is_set() or self._stop.is_set():
                return               # focus went elsewhere while we waited
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
                # Wait for the last group's refinement to finish. A refined line
                # must not arrive after focus has moved, or after the status has
                # been written as "ended".
                refiner.close()
        finally:
            # The refinement thread must be ended. Left alive it holds the
            # transcription model and the del below achieves nothing. And the token
            # is released only **after it has really finished** -- close() waits
            # just 10 seconds, and if a refinement decode is still running after
            # that the next session would run the same model at the same time.
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
    """Stop the other sessions. All of them if `keep_group` is empty -- the old rule
    that "one person watches one broadcast", as it was. Multiview keeps its own bundle
    and stops the rest. Left running, an ffmpeg and a ring would turn for a broadcast
    nobody is watching."""
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
        # It is born a standby session. set_focus() sends the status out and it is
        # not even registered yet, so only the flag is lowered.
        s._focus.clear()
        if s.source == "tab":
            s._ring.set_max(RING_S)
    with _lock:
        _sessions[s.id] = s
    # Even if the server dies before the first subtitle, the fact that the session
    # existed remains.
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


# ---- multiview ------------------------------------------------------------------
#
# Several broadcasts sit on one screen, but the sound and the subtitles belong to the
# **focused** one only. On the server a bundle (Group) is thin -- the member session
# ids and which one has focus. A member without focus only receives sound (the ring)
# and does not transcribe, and when focus moves the old one withdraws before the new
# one picks up from what pooled in its ring. A bundle lives only in memory. On
# restart the member sessions are left "interrupted" and the bundle is gone -- putting
# them back together is the user's call.

MULTIVIEW_MAX = 4          # the screen splits into four at most. That many ffmpegs come up too


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
    """Move the bundle's focus to `sid`. **The old one is turned off first** -- that
    way the old session's run_stream finishes refinement and lets the transcription
    token go before the new session takes it (two sessions never run the same model at
    once). The new session's sound is in its ring in the meantime, so nothing is
    lost."""
    g.focus = sid
    for m in g.members:
        s = get(m)
        if s is not None and m != sid:
            s.set_focus(False)
    s = get(sid)
    if s is not None:
        s.set_focus(True)


def _leave_group(s: LiveSession):
    """Take a finished session out of its bundle. If it had focus, move focus to the
    first member left; if none are left, delete the bundle."""
    g = _groups.get(s.group) if s.group else None
    s.group = ""                     # a finished session is not the bundle's (no ⊞ in the list)
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
    """One source as a member of the bundle. A session already receiving (`session`)
    is folded in; otherwise a new standby session is made."""
    sid = src.get("session")
    if sid:
        s = get(sid)
        if s is not None and s.state in RUNNING_STATES:
            s.group = gid
            return s
        # A stopped broadcast. It is resumed as the same session and put in as a
        # standby member -- a past broadcast dragged out of the list comes here.
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
    """Make a bundle. Each item of `sources` is either `{"session": id}` (fold in what
    is being watched now) or `{"url": ...}` / `{"source": "tab", "title": ...}` (a new
    standby session). Focus goes to the session `focus` points at, or to the first
    member if there is none. Every session outside the bundle is stopped."""
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
        _groups[g.id] = g                # it has to be findable when a member comes in by resume
    members: list[LiveSession] = []
    for src in sources:
        m = _member_from(src, g.id, lang, viewer_lang, backend_id, profile,
                         asr_backend_id, refine, genre)
        if isinstance(m, dict):
            # There is no session to fold in. The standby sessions just made are
            # rolled back and the bundle deleted too.
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
        m.set_focus(False)           # the folded-in session may have been holding focus alone
    _evict_outside(g.id)
    _publish_group(g)
    return m.status()


def multiview_remove(gid: str, sid: str) -> dict:
    """Close a tile = stop that session and take it out of the bundle."""
    g = _groups.get(gid)
    if g is None:
        return {"error": "no such group"}
    if sid not in g.members:
        return {"error": "그 세션은 이 묶음에 없습니다"}
    s = get(sid)
    if s is not None:
        s.stop()                     # once it ends _retire → _leave_group tidies up, but
    g.members.remove(sid)            # the UI needs an answer now, so it is removed here first
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
    """Fix a session's name.

    Tab audio has no title to fetch. Chrome puts an opaque identifier in the
    capture track's label rather than the tab title -- the measured value is
    `web-contents-media-stream://8D6F…`. If the name could not be written at
    the start, or was written wrong, fixing it later is the only way.

    A finished session has to be fixable too. What it was you listened to
    usually becomes a question when you look at the list afterwards.
    """
    title = (title or "").strip()[:200]
    if not title:
        return {"error": "이름을 입력해 주세요"}
    s = get(session_id)
    if s is not None:
        s.title = title
        s.title_by_user = True
        s._persist()
        s.emit({"type": "status", **s.status()})
        return {"ok": True, "title": title}
    st = store.session(session_id)
    if not st:
        return {"error": "no such session"}
    # store.session returns the doc with video_id laid on top. Putting it back as
    # it is would put that column inside the doc once more, so it is taken off
    # before saving.
    video_id = st.pop("video_id", "") or ""
    st["title"] = title
    st["title_by_user"] = True
    store.save_session(st, video_id)
    bus.publish({"type": "session", **st, "video_id": video_id})
    return {"ok": True, "title": title}


def notify_edit(owner: str, cue: dict, backend: str = ""):
    """Tell the windows that are watching about an edited line.

    An edit in the main window has to show up in the Subtitle log window right
    away. Only a session that is receiving has subscribers (a finished session's
    SSE sends the whole backlog and closes), so if there is nothing to do here
    it passes quietly.

    No new event kind is invented; it is sent in the same shape as an arriving
    subtitle. The browser already finds the line by id and swaps it in place.
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
    """Push a re-translated line out to the windows that are watching. If the
    session is not receiving there are no subscribers, so it passes quietly."""
    s = get(owner)
    if s is not None:
        s.emit({"type": "translation", "id": int(cue_id),
                "kind": kind or "final", "text": text})


def notify_drop(owner: str, cue_id: int):
    s = get(owner)
    if s is not None:
        s.emit({"type": "drop", "id": int(cue_id)})


def feed(session_id: str, raw: bytes) -> dict:
    """Put one block of tab audio the browser uploaded into the session."""
    s = get(session_id)
    if not s:
        # The server restarted, or the session ended. The browser sees this
        # answer and cuts the share itself -- better than going on uploading
        # sound nobody is listening to.
        return {"error": "no such session"}
    return s.feed(raw)


def shutdown(timeout: float = 8.0) -> int:
    """Close the sessions properly before shutting the server down.

    Just killing the process leaves them `running` in the DB, and the next
    start-up's recovery sweep marks them **interrupted**. That is, the record
    does not tell the user shutting down themselves apart from the server
    dying.

    `stop()` only sends a signal, so we wait here -- until the receive loop
    notices ffmpeg's broken pipe and writes the state as `stopped`. Without
    waiting, having bothered to call it counts for nothing.
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

    It is removed **only when it is itself**. When a session ends in error the UI
    shows "Resume" soon after, but the old session's `_run` stays alive up to 20
    seconds longer while the refiner and the translation worker close. If a new
    session resumed under the same id entered the registry in that window, the old
    one's finally took that new session out and overwrote the status with its own
    (error) -- the new session became an orphan that did not even hear "Stop".
    """
    with _lock:
        if _sessions.get(session.id) is session:
            _sessions.pop(session.id, None)
        else:
            return                          # another (resumed) session is already in that slot
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

    When the limit was 20 the table held 42 -- half of them were invisible and
    there was no way to delete them either. Now that they can be deleted
    (`delete`) the limit is left generous, and the UI can ask for more with
    `?limit=`.
    """
    with _lock:
        live_now = {sid: s.status() for sid, s in _sessions.items()}
    out = []
    for row in store.sessions(limit):
        out.append({**row, **live_now.get(row["id"], {})})
    return out


def delete(session_id: str) -> dict:
    """Delete a session and its subtitles. It has to be stopped first if it is
    receiving -- deleting it from the table mid-reception makes the next line
    create it again right away, leaving a ghost session."""
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
    # The genre is a property of the video being watched, so it stays across a
    # backend change.
    s._tr = mw_translate.build(spec, s.genre)
    s.backend_id = backend_id
    return {"backend": backend_id}


def resume(session_id: str, asr_backend_id: str = "", backend_id: str = "",
           source: str = "", url: str = "", group: str = "") -> dict:
    """Join a broken session's reception back up **as the same session**.

    Until now, when the server died that session ended there. The reason was
    that pretending to have resumed leaves a silent hole -- and making the hole
    not silent removes that reason: the missing seconds are written as one
    subtitle line.

    **It does not rewind.** Previously, when it was inside the DVR window, it
    rewound to where it stopped and filled the hole, which meant the later the
    resume the more had to be transcribed first, so the subtitle for the place
    being watched came out much later (more so with a slow engine). What the
    user is watching is now, so it starts at the live edge now. Only the
    automatic reconnect inside a session (a break of a few seconds) rewinds.

    The session id is reused, so the subtitles continue. It is not done
    automatically -- starting to receive a broadcast again because the server
    was switched on is not something the user asked for.

    Given a `group`, it comes back as a member of that multiview bundle (a
    standby session -- it only gets focus when the bundle has none). This is the
    route for dragging a stopped broadcast from the list onto the screen.
    """
    st = store.session(session_id)
    if not st:
        return {"error": "no such session"}
    if st.get("state") in ("starting", "loading", "running"):
        return {"error": "이미 받는 중입니다"}
    if get(session_id) is not None:
        # The stored status says finished, but the old session's threads are still
        # tidying up (wrapping up refinement and translation, up to 20 seconds).
        # Laying a new session on top of that lets the old finally push the new one
        # out.
        return {"error": "앞선 수신을 정리하는 중입니다. 몇 초 뒤 다시 누르십시오."}
    # The audio source can be changed on resume. A broadcast received by address
    # that turns members-only partway through goes to tab audio; one received as tab
    # audio that you want to carry on after closing the browser goes to the address.
    # The subtitles continue by session id, so it is one thread even across a source
    # change. The media time continues from `resume_from` for both sources.
    tab = (source or st.get("source")) == "tab"
    live_url = (url or "").strip() or (st.get("url") or "")
    if not tab and not live_url:
        return {"error": "주소가 남아 있지 않아 이어받을 수 없습니다. 탭 소리로 이어받으십시오."}

    # Only one broadcast is received at a time. The same rule as start() -- there
    # is no reason to keep two copies of the model loaded. If it is going into a
    # bundle, only that bundle is kept.
    g = _groups.get(group) if group else None
    if group and g is None:
        return {"error": "no such group"}
    _evict_outside(group)

    # What the caller passed wins for the engines (changing them under "Manage",
    # then resuming). An id that is not in the config falls back to the stored one --
    # a resume must not fail over one engine name.
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
    s.title_by_user = bool(st.get("title_by_user"))
    s.video_id = st.get("video_id") or ""
    s.site = st.get("site") or ""
    s.channel = st.get("channel") or ""
    s.error = None                       # why it had stopped is water under the bridge now
    # Joining up means the numbers have to continue. A new line reusing an old
    # line's id would overwrite that slot on screen.
    prior = store.cues(session_id)
    s._seq = max((int(c["id"]) for c in prior), default=0)
    s.lines = len(prior)
    # It continues from "where it received to" (recv_t). A multiview standby
    # session's transcribed position (media_base+audio_s) can be stuck hours back,
    # and rewinding from there would transcribe an already-watched stretch again even
    # inside the DVR window. An old record has no recv_t, so in that case the old way.
    s.resume_from = float(st.get("recv_t")
                          or (float(st.get("media_base") or 0.0) + float(st.get("audio_s") or 0.0)))
    if g is not None:
        s.group = g.id
        if g.focus in (None, s.id):
            g.focus = s.id               # in a bundle with no focus, this is the focus
        else:
            s._focus.clear()             # if there is one, it comes back as a standby session
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
    # The browser looks at source to decide whether to ask for the tab share
    # again. The server cannot rewind a tab session, so unless the sound is played
    # to it again it stays "receiving" without a single line being added.
    return {"id": s.id, "resumed": True, "source": s.source}


def set_asr(session_id: str, asr_backend_id: str) -> dict:
    """Swap the transcription engine on a running session.

    Previously the session had to be restarted. That changed the session id, and
    subtitles are stored by session id, so **the transcript up to then vanished
    from the screen.** The translator was already being swapped inside the
    session (set_backend), so there is no reason only the transcriber should not
    be. Within one video the subtitles have to continue.

    A line already sent stays the work of the engine that transcribed it, and
    only what follows is the new engine's -- the same rule as on the translator
    side.
    """
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    spec = config.find_asr(asr_backend_id)
    if spec is None:
        return {"error": f"'{asr_backend_id}' 전사 엔진이 없습니다"}
    if s._asr is None:
        # It has never had focus, so no engine was built (a multiview standby
        # session). It is built with this id at the first focus.
        s.asr_backend_id = asr_backend_id
        s._persist()
        return {"asr": asr_backend_id, "label": "", "device": "", "threads": 0}
    if not hasattr(s._asr, "swap"):
        return {"error": "이 세션의 전사기는 갈아 끼울 수 없습니다"}
    try:
        info = s._asr.swap(spec)
    except Exception as exc:
        # On failure what was in use stays. You do not lose the broadcast trying
        # to change it.
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
