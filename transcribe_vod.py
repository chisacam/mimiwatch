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
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np

HAYAMIMI = os.environ.get("HAYAMIMI_DIR", "/Users/chiyak/hobby/hayamimi")
sys.path.insert(0, os.path.join(HAYAMIMI, "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from asr_engine import RoutedASR  # noqa: E402
from realtime_transcribe import build_vad  # noqa: E402

import translate as mw_translate  # noqa: E402

SAMPLE_RATE = 16000
CHUNK = 1600  # 0.1s per VAD feed


def probe(url: str) -> dict:
    out = subprocess.run(["yt-dlp", "--no-warnings", "-j", url],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"yt-dlp failed: {out.stderr.strip()[:300]}")
    d = json.loads(out.stdout)
    return {"id": d.get("id"), "title": d.get("title"),
            "duration": d.get("duration"), "uploader": d.get("uploader"),
            "is_live": bool(d.get("is_live")), "url": url}


def fetch_audio(url: str, dest: str) -> str:
    """Download the audio-only rendition and decode it to 16kHz mono wav."""
    if os.path.exists(dest):
        print(f"[vod] reusing cached audio {dest}", file=sys.stderr)
        return dest
    tmp = dest + ".src"
    dl = subprocess.run(["yt-dlp", "--no-warnings", "-f", "bestaudio",
                         "-o", tmp, url], capture_output=True, text=True)
    if dl.returncode != 0:
        raise SystemExit(f"yt-dlp download failed: {dl.stderr.strip()[:300]}")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp,
                    "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), dest], check=True)
    os.remove(tmp)
    return dest


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(samples: np.ndarray, lang: str | None, on_progress=None) -> list[dict]:
    """VAD-segment the whole file and decode each segment.

    Segment.start is a sample index, which is exactly the media timestamp the
    player needs -- the realtime pipeline throws this away because it only
    ever cared about "now".
    """
    asr = RoutedASR(threads=4, forced_lang=lang)
    vad = build_vad(min_silence=0.35, max_speech=12.0)
    cues: list[dict] = []
    total = len(samples)

    def drain():
        while not vad.empty():
            seg = vad.front
            buf = np.asarray(seg.samples, dtype=np.float32)
            start_s = seg.start / SAMPLE_RATE
            end_s = (seg.start + len(buf)) / SAMPLE_RATE
            vad.pop()
            res = asr.transcribe(buf, SAMPLE_RATE, speech_s=len(buf) / SAMPLE_RATE)
            text = res["text"].strip()
            if text:
                cues.append({"start": round(start_s, 3), "end": round(end_s, 3),
                             "lang": res["lang"], "text": text})

    for i in range(0, total, CHUNK):
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
    cues = transcribe(samples, args.lang,
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
        tr = mw_translate.build(None)
        print(f"[vod] translating {source_lang} -> {args.viewer_lang} "
              f"({tr.name})...", file=sys.stderr)
        t0 = time.time()
        skipped = 0
        for i, c in enumerate(cues):
            if not tr.should_translate(c["text"], source_lang, args.viewer_lang):
                skipped += 1
                continue
            out = tr.translate(c["text"], source_lang, args.viewer_lang)
            # Only carry a translation that actually says something new; a
            # line that came back as its own source just doubles the overlay.
            if out and out.strip() != c["text"].strip():
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
    main()
