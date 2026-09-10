"""The pieces that turn one VOD into timestamped subtitles, and the shell that
runs them from the command line.

Nothing in the VOD flow has to match a clock. Everything is transcribed before
playback, and every subtitle carries a media-relative timestamp, so the browser
only has to look it up against the YouTube player's `getCurrentTime()`. The live
measurement of 2026-08-27 is why this path was built first -- refinement falls
as much as 20 seconds behind a fast speaker, and in a VOD that is played back
after it has all been made, that is not a problem.

    python transcribe_vod.py --url https://youtu.be/... --viewer-lang ko

**The command line uses the same path as the server.** There used to be one
more translate loop here, and it wrote the result in the old shape
(`data/<video id>.json`) -- the server only recognised that file once the next
startup had moved it into the table. Now `jobs.start_transcribe` is called
directly and only the progress is printed in the terminal. The result goes into
the same `data/mimiwatch.db` the server uses, and turning the server on shows it
in the list right away.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import wave

import numpy as np

import stream
from stream import build_vad
from tcpp_asr import build_live_asr

SAMPLE_RATE = 16000
CHUNK = 1600  # 0.1s per VAD feed


# Failures are raised as RuntimeError. They used to be SystemExit, but these
# functions are also called from the server's job thread
# (jobs._run_transcribe). SystemExit is not an Exception, so that side's
# `except Exception` does not catch it, and a thread's default excepthook
# quietly ignores SystemExit -- the job then stays `running` forever and does
# not listen to "stop" either. A network drop mid-download was exactly this
# path. The CLI side (main) turns it into an exit code.
class VodError(RuntimeError):
    """yt-dlp or ffmpeg failed."""


# ---- Local files -------------------------------------------------------------
#
# There is no reason for the transcription target to be a URL. An mp4 that was
# recorded, an mp3 that was extracted, go through the same pipeline -- one
# ffmpeg conversion step takes the place of the download. The server and the
# browser are on the same machine, so a path can be taken as it is, and what
# was uploaded through the file picker is put in data/uploads/ by the server,
# which hands that path over to here.

def is_local_source(url: str) -> bool:
    """Is what came in the URL field a file path on this machine?

    Only file://, absolute paths (/ on macOS and Linux, the drive-letter shape
    on Windows) and ~ count. Relative paths are not taken -- the server's
    working directory is not somewhere the user knows about, so even if one
    resolved, there would be no saying which file it is.
    """
    u = (url or "").strip()
    return bool(u) and (u.startswith("file://") or u.startswith("/")
                        or u.startswith("~") or bool(re.match(r"^[A-Za-z]:[\\/]", u)))


def local_path(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("file://"):
        from urllib.request import url2pathname
        from urllib.parse import urlparse, unquote
        u = url2pathname(unquote(urlparse(u).path))
    return os.path.abspath(os.path.expanduser(u))


def probe_local(url: str) -> dict:
    """Metadata for a local file. It does not go through yt-dlp.

    The id is a hash of the path. Putting the same file in again gives the same
    id, so the re-transcription rule that inherits translations made by another
    engine and hand edits holds exactly as it is. The duration is not measured
    here -- ffprobe is not a prerequisite (a single static ffmpeg file does not
    have it), and once the conversion is done the wav says it exactly anyway.
    """
    import hashlib
    path = local_path(url)
    if not os.path.isfile(path):
        raise VodError(f"No such file: {path}")
    if not os.access(path, os.R_OK):
        raise VodError(f"The file cannot be read: {path}")
    vid = "file-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    return {"id": vid, "title": os.path.splitext(os.path.basename(path))[0],
            "duration": None, "uploader": "", "is_live": False,
            "url": path, "source": "file", "media_path": path}


def convert_local(src: str, dest: str, should_stop=None) -> str:
    """Local media to 16kHz mono wav. It takes the place of the download stage.

    The cache rule differs from the URL one: if the source is newer than the
    wav, it is converted again. Different content landing at the same path is
    common with local files.
    """
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
        print(f"[vod] reusing cached audio {dest}", file=sys.stderr)
        return dest
    try:
        cmd = stream.ffmpeg_cmd()
    except FileNotFoundError as exc:
        raise VodError(str(exc)) from exc
    tmp = dest + ".part"
    proc = subprocess.Popen(
        [cmd, "-loglevel", "error", "-nostdin", "-y", "-i", src,
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", tmp],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        **stream.child_io(stderr=False))
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if should_stop and should_stop():
                proc.kill()
                proc.wait()
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise stream.Cancelled()
    if proc.returncode != 0:
        raise VodError("ffmpeg could not open this file: "
                       f"{(err or '').strip()[:300] or os.path.basename(src)}")
    os.replace(tmp, dest)
    return dest


def probe(url: str) -> dict:
    if is_local_source(url):
        return probe_local(url)
    try:
        out = subprocess.run(stream.ytdlp_args("-j", url=url),
                             capture_output=True, text=True,
                             timeout=stream.YTDLP_TIMEOUT_S,
                             **stream.child_io(stderr=False))
    except subprocess.TimeoutExpired:
        raise VodError(f"yt-dlp did not answer within {stream.YTDLP_TIMEOUT_S:.0f} seconds")
    if out.returncode != 0:
        raise VodError(f"yt-dlp failed: {out.stderr.strip()[:300]}")
    try:
        d = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise VodError("Give the URL of a single video (not a playlist or channel URL)")
    return {"id": d.get("id"), "title": d.get("title"),
            "duration": d.get("duration"), "uploader": d.get("uploader"),
            "is_live": bool(d.get("is_live")), "url": url}


def fetch_audio(url: str, dest: str, should_stop=None) -> str:
    """Download the audio-only rendition and decode it to 16kHz mono wav.

    When `should_stop` returns true, the download is killed and
    `stream.Cancelled` is raised. A two-hour stream takes minutes just to
    download, and pressing "stop" during that used to stop only once the
    download had finished.
    """
    if is_local_source(url):
        return convert_local(local_path(url), dest, should_stop)
    if os.path.exists(dest):
        print(f"[vod] reusing cached audio {dest}", file=sys.stderr)
        return dest
    tmp = dest + ".src"
    proc = subprocess.Popen(
        stream.ytdlp_args("-f", "bestaudio", "-o", tmp, url=url),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **stream.child_io(stderr=False))
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if should_stop and should_stop():
                proc.kill()
                proc.wait()
                # While downloading, yt-dlp leaves piece files like `.part`.
                for leftover in glob.glob(glob.escape(tmp) + "*"):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
                raise stream.Cancelled()
    if proc.returncode != 0:
        raise VodError(f"yt-dlp download failed: {(err or '').strip()[:300]}")
    try:
        subprocess.run([stream.ffmpeg_cmd(), "-loglevel", "error", "-nostdin", "-y",
                        "-i", tmp, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), dest],
                       check=True, stdout=subprocess.DEVNULL, **stream.child_io())
    except subprocess.CalledProcessError as exc:
        raise VodError(f"ffmpeg failed: {exc}") from exc
    except FileNotFoundError as exc:
        # ffmpeg is not there. The downloaded source is left in place -- put it
        # in again after getting the tool and, since `dest` does not exist, it
        # starts again from here, and yt-dlp overwrites the source under the
        # same name.
        raise VodError(str(exc)) from exc
    os.remove(tmp)
    return dest


def _pcm_span(path: str) -> tuple[int, int]:
    """Where in the file the wav's sample chunk starts and how many bytes it is.

    The `wave` module does not hand this out (`_data_chunk` is private). RIFF is
    a repetition of "4 bytes of name + 4 bytes of length + content", so walking
    it directly is shorter. The length is clamped once by the file size --
    trusting the length written in the header as it stands lets the memory
    mapping run past the end of the file on a half-written wav.
    """
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise VodError(f"Not a wav: {os.path.basename(path)}")
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise VodError(f"The wav has no data chunk: {os.path.basename(path)}")
            name = hdr[:4]
            size = int.from_bytes(hdr[4:8], "little")
            if name == b"data":
                start = f.tell()
                return start, min(size, os.path.getsize(path) - start)
            f.seek(size + (size & 1), 1)      # Chunks are padded to an even byte count


class WavSamples:
    """Does not load the wav whole; hands out only the slice asked for, as float32.

    It used to be `np.frombuffer(w.readframes(everything))`. On a 116-minute
    stream the 222MB int16 original and the 445MB float32 copy were alive at the
    same time for a while, so the peak was 670MB. The way transcription uses
    this array is only `len()` and slicing from the front (the remote
    transcriber slices by window too), and that slice becomes a copy anyway. So
    the file is mapped into memory and converted at the moment it is sliced --
    what stays resident is only page cache the OS discards on its own.

    The "machine where integrated graphics is a struggle" that section 33 talks
    about is what this tool is for, and on such a machine 445MB is larger than
    the transcription model (SenseVoice Small, 241MB).
    """

    def __init__(self, path: str):
        with wave.open(path, "rb") as w:
            if not (w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1
                    and w.getsampwidth() == 2):
                raise VodError(f"Not a 16kHz mono 16-bit wav: {os.path.basename(path)}")
        start, size = _pcm_span(path)
        self._mm = np.memmap(path, dtype="<i2", mode="r", offset=start,
                             shape=(size // 2,))

    def __len__(self) -> int:
        return int(self._mm.shape[0])

    def __getitem__(self, key) -> np.ndarray:
        return self._mm[key].astype(np.float32) / 32768.0


def read_wav(path: str) -> WavSamples:
    """The samples transcription will sweep. It acts like an array but holds a file
    (WavSamples)."""
    return WavSamples(path)


# The refinement pass's share of the whole transcription time. In the section 49
# measurement, adding refinement made it 30-50% longer. It is used only to split
# the progress into two shares -- appearing to run on for a long while after the
# fast pass has reached 100% reads as having stalled.
REFINE_SHARE = 0.35


def refine_groups(spans: list[tuple[int, int]]) -> list[list[int]]:
    """Groups the final segments into refinement units. The value is a list of
    indices per group.

    The rule for closing a group is the same as live's
    (`stream.Refiner.maybe_refine`). Live judges "2 seconds of quiet after the
    last utterance ends it" against a running clock, and on audio that has all
    been fetched already that becomes "the next utterance comes 2 seconds
    later". Cutting at 25 seconds even when someone does not stop talking is the
    same. The reason the constants are read from `stream` instead of being
    copied here is that if that side moves, this side has to move with it.
    """
    gap = int(stream.GROUP_GAP_S * SAMPLE_RATE)
    mx = int(stream.GROUP_MAX_S * SAMPLE_RATE)
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, (start, end) in enumerate(spans):
        if cur and (start - spans[cur[-1]][1] >= gap
                    or end - spans[cur[0]][0] >= mx):
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def _speaker_for(cues: list[dict], idx: list[int], start: float, end: float) -> str:
    """The speaker to attach to a new subtitle line. It inherits the label the final
    already took.

    CAM++ could be run again for each re-split line, but the finals already put
    a label on every segment and the piece used then is longer -- the condition
    section 33 stated, "a segment must have enough voice in it", is better met
    on that side. The label of the final line that overlaps the most in time is
    used.
    """
    best, best_overlap = "", 0.0
    for i in idx:
        c = cues[i]
        overlap = min(end, c["end"]) - max(start, c["start"])
        if overlap > best_overlap:
            best, best_overlap = c.get("speaker", ""), overlap
    return best


def _soft_resplit(cues: list[dict], text: str, lang: str) -> list[dict]:
    """Lay one group's re-decoded text back on its own VAD boundaries.

    The transcriber cannot give segment timestamps, so there is no timing the
    re-decode can own. What it does have is the text, and the text is the side
    that improves with the surrounding context (section 48). Laying it over
    the boundaries the fast pass already cut keeps the player's timestamp
    lookup working as it was. The measured loss of section 49 was the timing
    of one line stretched across the group, not the characters, and the
    characters here are the re-decode's, so the soft side takes the gain
    without that loss.

    The cut points divide the text in proportion to how long each final was --
    a line that said more takes a longer piece. A piece that comes out empty
    is not a subtitle line, so that final is kept as it was.
    """
    weights = [max(1, len(c["text"].strip())) for c in cues]
    total = sum(weights)
    cuts = [0]
    acc = 0
    for w in weights:
        acc += w
        cuts.append(int(round(acc * len(text) / total)))
    out = []
    for c, lo, hi in zip(cues, cuts, cuts[1:]):
        piece = text[lo:hi].strip()
        n = dict(c)
        n["text"] = piece if piece else c["text"]
        n["lang"] = lang or c["lang"]
        out.append(n)
    return out


def refine_cues(samples, cues: list[dict], spans: list[tuple[int, int]], asr,
                on_progress=None, should_stop=None) -> list[dict]:
    """Decodes an utterance group whole again and re-splits it by segment timestamps.

    The finals are segments decoded one by one, so they have no surrounding
    context. Joining a group and passing it again is better by that much
    (section 48: overall error rate 57.8 → 55.9).

    **The re-split is the heart of this function.** Live's refinement sends one
    group out as one line and that is enough -- that line passes by soon --
    whereas a VOD subtitle stays, and is what the player looks up by timestamp.
    Leaving a group as one line stretched the subtitle line from 4.8 s to 11.7 s
    and the tail quality actually dropped (section 49: chrF 29.3 → 27.7, while
    the whole-text concatenation stayed the same at 39.6 -- what was mangled was
    the timing, not the characters). Asking the runtime for segment timestamps
    and dividing it back up gives 32.4 on the same sample, the highest of the
    settings measured.

    A transcriber that cannot give timestamps (the light defaults) cannot be
    re-split that way, and one line per group is the side that lost in section
    49. It runs the same decode and lands the text on the group's own VAD
    boundaries instead (`_soft_resplit`): the times are the ones the fast pass
    already had, and only the text is swapped.

    A group caught by the rollback, whatever the transcriber is, keeps its
    finals as they are. Where it does not get better, it must at least not get
    worse.
    """
    soft = not getattr(asr, "supports_segments", False)
    groups = refine_groups(spans)
    pre = int(stream.PREROLL_S * SAMPLE_RATE)
    out: list[dict] = []
    for n, g in enumerate(groups):
        if should_stop and should_stop():
            raise stream.Cancelled()
        keep = [cues[i] for i in g]
        first, last = spans[g[0]][0], spans[g[-1]][1]
        base = max(0, first - pre)
        lo, hi = first / SAMPLE_RATE, last / SAMPLE_RATE
        buf = samples[base:last]
        joined = " ".join(c["text"] for c in keep if c["text"].strip())
        if len(buf) >= SAMPLE_RATE // 2:
            got = asr.transcribe(buf, SAMPLE_RATE, speech_s=len(buf) / SAMPLE_RATE,
                                 live=False, segments=not soft)
            text = got["text"].strip()
            if soft:
                # The re-decode has no timestamps to cut by, so the text goes
                # back over the boundaries that made the finals.
                if len(text) >= stream.REFINE_MIN_KEEP * len(joined):
                    keep = _soft_resplit(keep, text,
                                         got.get("lang") or keep[0]["lang"])
            else:
                segs = got.get("segments") or []
                # The rollback uses the same threshold as live. If the re-decode is
                # noticeably shorter than the finals concatenated, it swallowed
                # words, and swapping perfectly good subtitles for a decode that
                # swallowed words is the worst outcome.
                if segs and len(text) >= stream.REFINE_MIN_KEEP * len(joined):
                    lang = got.get("lang") or keep[0]["lang"]
                    keep = []
                    for sg in segs:
                        start = min(max(base / SAMPLE_RATE + sg["start"], lo), hi)
                        end = min(max(base / SAMPLE_RATE + sg["end"], lo), hi)
                        # A line that came out only within the 1 s preroll is the
                        # tail of the previous group, and a piece whose timing is
                        # mangled cannot be a subtitle. Both are thrown away.
                        if end - start < 0.05:
                            continue
                        cue = {"start": round(start, 3), "end": round(end, 3),
                               "lang": lang, "text": sg["text"]}
                        who = _speaker_for(cues, g, start, end)
                        if who:
                            cue["speaker"] = who
                        keep.append(cue)
                    if not keep:                   # All filtered out: put the finals back
                        keep = [cues[i] for i in g]
        out.extend(keep)
        if on_progress:
            on_progress((n + 1) / len(groups))
    return out


def transcribe(samples: "np.ndarray | WavSamples", lang: str | None, on_progress=None,
               speakers: bool = False, speaker_solo: bool = False,
               speaker_threshold: float | None = None, asr=None, should_stop=None,
               refine: bool = True) -> list[dict]:
    """VAD-segment the whole file and decode each segment.

    Segment.start is a sample index, which is exactly the media timestamp the
    player needs -- the realtime pipeline throws this away because it only
    ever cared about "now".

    All that is used from `samples` is `len()` and slicing. That is why the
    file-mapped window `read_wav` gives (WavSamples) is taken as it is -- it was
    left that way so as not to turn a two-hour stream into an array.

    With `refine`, the group is decoded again after the finals have been
    produced (`refine_cues`). A VOD has no latency constraint, so it is on by
    default. The cost is that transcription takes 30-50% longer -- it can be
    turned off for the places where that is not worth it.
    """
    if asr is None:
        asr = build_live_asr(None, lang, threads=4)
    vad = build_vad(min_silence=0.35, max_speech=12.0)
    # CAM++ needs enough voice in a segment to place a speaker. The 12s
    # splits here give it that; the live path splits at 3-4s to keep up with
    # a talker and cannot, which is why tagging lives on this side. The
    # factory is the surface that survives a missing model: a disappeared
    # onnx file used to stop the whole VOD, now it is a transcript without
    # labels and a line in the log.
    from speaker_id import make_labeler
    labeler = make_labeler(speakers, solo=speaker_solo,
                           threshold=speaker_threshold)
    cues: list[dict] = []
    # The sample spans paired with the subtitle lines. Refinement uses them to
    # group utterances.
    spans: list[tuple[int, int]] = []
    total = len(samples)
    # Refinement is one set of moves only as far as the re-split. On a model
    # that cannot give segment timestamps (SenseVoice Small, moonshine -- the
    # light defaults) the group is decoded again and the text lands on the VAD
    # boundaries instead (`_soft_resplit`). It is settled here ahead of time
    # because how many shares to split the progress into hangs on it.
    if refine and not getattr(asr, "supports_segments", False):
        print(f"[vod] {getattr(asr, 'label', 'this transcriber')} cannot give segment "
              "timestamps, so the refinement pass lands the text on the VAD "
              "boundaries -- the times are kept, the text is swapped",
              file=sys.stderr, flush=True)
    fast_share = (1.0 - REFINE_SHARE) if refine else 1.0

    def drain():
        while not vad.empty():
            # Checked once per segment. Looking only in the chunk loop stops as
            # late as the decode has fallen behind -- the slower the machine,
            # the bigger that gap.
            if should_stop and should_stop():
                raise stream.Cancelled()
            seg = vad.front
            buf = np.asarray(seg.samples, dtype=np.float32)
            start_s = seg.start / SAMPLE_RATE
            end_s = (seg.start + len(buf)) / SAMPLE_RATE
            vad.pop()
            res = asr.transcribe(buf, SAMPLE_RATE, speech_s=len(buf) / SAMPLE_RATE)
            text = res["text"].strip()
            if text:
                cue = {"start": round(start_s, 3), "end": round(end_s, 3),
                       "lang": res["lang"], "text": text}
                if labeler is not None:
                    cue["speaker"] = labeler.label(buf, SAMPLE_RATE)
                cues.append(cue)
                spans.append((seg.start, seg.start + len(buf)))

    for i in range(0, total, CHUNK):
        if should_stop and should_stop():
            raise stream.Cancelled()
        vad.accept_waveform(samples[i:i + CHUNK])
        drain()
        if on_progress and i % (CHUNK * 300) == 0:
            on_progress(i / total * fast_share)
    vad.flush()
    drain()
    if refine and cues:
        def note(p):
            if on_progress:
                on_progress(fast_share + p * REFINE_SHARE)
        cues = refine_cues(samples, cues, spans, asr, on_progress=note,
                           should_stop=should_stop)
    return cues


# ---- Command line ------------------------------------------------------------

PHASE_LABEL = {"probe": "Checking the video", "download": "Taking the audio",
               "convert": "Converting the audio",
               "transcribe": "Transcribing", "translate": "Translating", "done": "Done"}


def main():
    # The Windows console's default encoding is cp949, which mangles Korean
    # titles. This line used to sit at the very top of the module, so the moment
    # the server imported this module it changed the server's stdout too. It is
    # done only on the command line.
    sys.stdout.reconfigure(encoding="utf-8")
    import translate as mw_translate
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--lang", help="Pins the source language (left empty, it is detected)")
    ap.add_argument("--viewer-lang", default="ko",
                    help="My language. Nothing is translated when it matches the source")
    ap.add_argument("--genre", default=mw_translate.DEFAULT_GENRE,
                    choices=sorted(mw_translate.GENRE_PROMPTS),
                    help="Picks the translation prompt that fits how people are speaking")
    ap.add_argument("--asr", default="",
                    help="Transcription engine ID (left empty, the config default)")
    ap.add_argument("--backend", default="",
                    help="Translation engine ID (left empty, the config default)")
    ap.add_argument("--speakers", action="store_true",
                    help="Attaches speaker labels (S1, S2, ...)")
    ap.add_argument("--speaker-solo", action="store_true",
                    help="Single-speaker mode with --speakers: everything is S1 "
                         "instead of the voice splitting into several")
    ap.add_argument("--speaker-threshold", type=float, default=None,
                    help="Similarity threshold for --speakers (0.25~0.9, default 0.45; "
                         "lower splits people more, higher lumps them)")
    ap.add_argument("--no-refine", action="store_true",
                    help="Skips the refinement pass (transcription 30~50%% shorter, "
                         "quality goes down)")
    args = ap.parse_args()

    import jobs
    import store
    store.init()
    res = jobs.start_transcribe(args.url, args.lang, args.viewer_lang,
                                backend_id=args.backend, asr_id=args.asr,
                                speakers=args.speakers, genre=args.genre,
                                speaker_solo=args.speaker_solo,
                                speaker_threshold=args.speaker_threshold,
                                refine=not args.no_refine)
    job_id = res["id"]
    last = ""
    while True:
        st = jobs.job_status(job_id) or {}
        line = f"[vod] {PHASE_LABEL.get(st.get('phase'), st.get('phase', ''))}"
        if st.get("total"):
            line += f" {st.get('done', 0)}/{st['total']}"
        if st.get("title"):
            line += f"  · {st['title'][:40]}"
        if line != last:
            print("\r" + line.ljust(78), end="", file=sys.stderr, flush=True)
            last = line
        if st.get("state") in ("done", "error", "cancelled", "interrupted"):
            break
        time.sleep(0.5)
    print(file=sys.stderr)
    if st.get("state") != "done":
        raise VodError(st.get("error") or st.get("state") or "failed")
    print(f"[vod] done ({st.get('elapsed', 0)}s). Video {st.get('video')} -- "
          f"start the server and it shows up in the list.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except VodError as exc:
        # On the command line we speak in exit codes. It has to stay an
        # exception on the server thread, so SystemExit is not used inside the
        # functions.
        raise SystemExit(str(exc))
