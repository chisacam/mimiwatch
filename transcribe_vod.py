"""Transcribe a finished video into timestamped, optionally translated cues.

The recorded-video flow needs no clock negotiation: the audio is transcribed
ahead of playback, every cue carries a media-relative timestamp, and the
browser lines them up against the YouTube player's own getCurrentTime(). The
2026-08-27 live measurements showed why this path is worth doing first --
the refine pass lags up to 20s behind a fast talker, which is fatal live but
irrelevant when nothing is played until the whole file is done.

    python transcribe_vod.py --url https://youtu.be/... --viewer-lang ko
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

import stream
import translate as mw_translate
from stream import build_vad
from tcpp_asr import build_live_asr

SAMPLE_RATE = 16000
CHUNK = 1600  # 0.1s per VAD feed


# 실패는 RuntimeError로 냅니다. 예전에는 SystemExit이었는데, 이 함수들은
# 서버의 작업 스레드(jobs._run_transcribe)에서도 불립니다. SystemExit은
# Exception이 아니라 그쪽의 `except Exception`이 잡지 못하고, 스레드의
# 기본 excepthook은 SystemExit을 조용히 무시합니다 -- 그러면 작업이
# `running`인 채로 영원히 남고 「중단」도 듣지 않습니다. 다운로드 도중
# 네트워크가 끊기면 정확히 이 경로였습니다. CLI 쪽(main)이 종료 코드로
# 바꿉니다.
class VodError(RuntimeError):
    """yt-dlp나 ffmpeg가 실패했습니다."""


def probe(url: str) -> dict:
    try:
        out = subprocess.run(stream.ytdlp_cmd() + ["--no-warnings", "-j", url],
                             capture_output=True, text=True,
                             timeout=stream.YTDLP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise VodError(f"yt-dlp가 {stream.YTDLP_TIMEOUT_S:.0f}초 안에 답하지 않았습니다")
    if out.returncode != 0:
        raise VodError(f"yt-dlp failed: {out.stderr.strip()[:300]}")
    d = json.loads(out.stdout)
    return {"id": d.get("id"), "title": d.get("title"),
            "duration": d.get("duration"), "uploader": d.get("uploader"),
            "is_live": bool(d.get("is_live")), "url": url}


def fetch_audio(url: str, dest: str, should_stop=None) -> str:
    """Download the audio-only rendition and decode it to 16kHz mono wav.

    `should_stop`이 참을 돌려주면 내려받기를 죽이고 `stream.Cancelled`를
    냅니다. 두 시간짜리 방송은 내려받기만 몇 분인데, 예전에는 그 사이에
    「중단」을 눌러도 다 받은 뒤에야 멈췄습니다.
    """
    if os.path.exists(dest):
        print(f"[vod] reusing cached audio {dest}", file=sys.stderr)
        return dest
    tmp = dest + ".src"
    proc = subprocess.Popen(
        stream.ytdlp_cmd() + ["--no-warnings", "-f", "bestaudio", "-o", tmp, url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if should_stop and should_stop():
                proc.kill()
                proc.wait()
                # yt-dlp는 받는 동안 `.part` 같은 조각 파일을 남깁니다.
                for leftover in glob.glob(glob.escape(tmp) + "*"):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
                raise stream.Cancelled()
    if proc.returncode != 0:
        raise VodError(f"yt-dlp download failed: {(err or '').strip()[:300]}")
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp,
                        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), dest], check=True)
    except subprocess.CalledProcessError as exc:
        raise VodError(f"ffmpeg failed: {exc}") from exc
    os.remove(tmp)
    return dest


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(samples: np.ndarray, lang: str | None, on_progress=None,
               speakers: bool = False, asr=None, should_stop=None) -> list[dict]:
    """VAD-segment the whole file and decode each segment.

    Segment.start is a sample index, which is exactly the media timestamp the
    player needs -- the realtime pipeline throws this away because it only
    ever cared about "now".
    """
    if asr is None:
        asr = build_live_asr(None, lang, threads=4)
    vad = build_vad(min_silence=0.35, max_speech=12.0)
    # CAM++ needs enough voice in a segment to place a speaker. The 12s
    # splits here give it that; the live path splits at 3-4s to keep up with
    # a talker and cannot, which is why tagging lives on this side.
    labeler = None
    if speakers:
        from speaker_id import SpeakerLabeler
        labeler = SpeakerLabeler()
    cues: list[dict] = []
    total = len(samples)

    def drain():
        while not vad.empty():
            # 구간 하나마다 확인합니다. 청크 루프에서만 보면 해독이 뒤처진
            # 만큼 늦게 멈춥니다 -- 느린 기계일수록 그 차이가 큽니다.
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

    for i in range(0, total, CHUNK):
        if should_stop and should_stop():
            raise stream.Cancelled()
        vad.accept_waveform(samples[i:i + CHUNK])
        drain()
        if on_progress and i % (CHUNK * 300) == 0:
            on_progress(i / total)
    vad.flush()
    drain()
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--lang", help="pin the source language (skip detection)")
    ap.add_argument("--viewer-lang", default="ko",
                    help="the viewer's language; translation is skipped when it matches")
    ap.add_argument("--no-translate", action="store_true")
    ap.add_argument("--genre", default=mw_translate.DEFAULT_GENRE,
                    choices=sorted(mw_translate.GENRE_PROMPTS),
                    help="발화의 성격에 맞는 번역 프롬프트를 고릅니다")
    ap.add_argument("--speakers", action="store_true",
                    help="label each cue with a speaker id (S1, S2, ...)")
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    meta = probe(args.url)
    if meta["is_live"]:
        raise SystemExit("this is a live stream; the recorded-video flow needs a finished video")
    os.makedirs(args.outdir, exist_ok=True)
    vid = meta["id"]
    print(f"[vod] {meta['title']}  ({meta['duration']}s)", file=sys.stderr)

    wav = fetch_audio(args.url, os.path.join(args.outdir, f"{vid}.wav"))
    samples = read_wav(wav)
    audio_s = len(samples) / SAMPLE_RATE
    print(f"[vod] transcribing {audio_s:.0f}s of audio...", file=sys.stderr)

    t0 = time.time()
    cues = transcribe(samples, args.lang, speakers=args.speakers,
                      on_progress=lambda p: print(f"\r[vod] {p*100:5.1f}%",
                                                  end="", file=sys.stderr, flush=True))
    took = time.time() - t0
    print(f"\r[vod] {len(cues)} cues in {took:.0f}s  (RTF {took/max(audio_s,1):.3f}, "
          f"{audio_s/max(took,0.001):.0f}x realtime)", file=sys.stderr)

    # R3.10: the detected language is reported, not silently assumed.
    langs: dict[str, int] = {}
    for c in cues:
        langs[c["lang"]] = langs.get(c["lang"], 0) + 1
    source_lang = args.lang or (max(langs, key=langs.get) if langs else "unknown")

    # R3.8: matching languages means no translation at all -- no latency, no
    # cost, and no chance of an mistranslation degrading a line the viewer
    # could already read.
    needs = source_lang != args.viewer_lang and not args.no_translate
    if needs:
        tr = mw_translate.build(None, args.genre)
        print(f"[vod] translating {source_lang} -> {args.viewer_lang} "
              f"({tr.name})...", file=sys.stderr)
        t0 = time.time()
        skipped = 0
        for i, c in enumerate(cues):
            if not tr.should_translate(c["text"], source_lang, args.viewer_lang):
                skipped += 1
                continue
            try:
                # 직전 자막 몇 줄을 참고로 함께 넘깁니다. 뒤쪽은 넘기지
                # 않습니다 -- 라이브에는 없는 정보라 결과가 갈립니다.
                out = tr.translate(
                    c["text"], source_lang, args.viewer_lang,
                    [p["text"] for p in
                     cues[max(0, i - mw_translate.CONTEXT_LINES):i]])
            except Exception as exc:
                print(f"\n[vod] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
                out = c["text"]
            if (out or "").strip():
                c["translation"] = out
            if i % 50 == 0:
                print(f"\r[vod] {i}/{len(cues)}", end="", file=sys.stderr, flush=True)
        done = sum(1 for c in cues if "translation" in c)
        note = f" ({skipped} fragments skipped)" if skipped else ""
        print(f"\r[vod] translated {done}/{len(cues)} in {time.time()-t0:.0f}s{note}",
              file=sys.stderr)
    else:
        why = "source matches viewer language" if source_lang == args.viewer_lang else "disabled"
        print(f"[vod] translation skipped ({why})", file=sys.stderr)

    doc = {**meta, "source_lang": source_lang, "lang_counts": langs,
           "viewer_lang": args.viewer_lang, "translated": needs,
           "audio_seconds": round(audio_s, 1), "cues": cues}
    out = os.path.join(args.outdir, f"{vid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"[vod] -> {out}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except VodError as exc:
        # 명령줄에서는 종료 코드로 말합니다. 서버 스레드에서는 예외로 남겨야
        # 하므로 함수 안에서는 SystemExit을 쓰지 않습니다.
        raise SystemExit(str(exc))
