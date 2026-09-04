"""Rebuild the ground-truth samples: the subtitles and 16kHz audio of videos
that carry YouTube's **manual** subtitles.

    .venv/bin/python bench/gold_fetch.py            # the whole list below -> data/gold/
    .venv/bin/python bench/gold_fetch.py <video id> ja

data/ is not kept in the repository (copyrighted material), so this script is where the
samples come from. The first four samples are all music videos -- that is where videos
officially carrying subtitles in the original language are concentrated -- and singing is
the hardest case for transcription, so the absolute values are high. They are for looking
at the **difference** between settings.
"""
from __future__ import annotations
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stream

# (video id, subtitle language). Confirmed to have manual subtitles as of 2026-08-29.
GOLD = [("_xwOiIMM2a4", "ja"),   # 音乃瀬奏 - You＆合図 (karaoke-like styled subtitles)
        ("BgPBgTEvi08", "ja"),   # TAK - ニャニャニャチュニャ (repetitive lyrics)
        ("CkvWJNt77mU", "ja"),   # ヨルシカ - 晴る (singing over accompaniment, subtitle track named ja-*)
        ("_PSjoVXFGAQ", "en")]   # Mili - Fly, My Wings

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gold")


def fetch(vid: str, lang: str):
    os.makedirs(OUT, exist_ok=True)
    url = f"https://youtu.be/{vid}"
    subprocess.run(stream.ytdlp_args("--skip-download", "--write-subs", "--sub-langs", f"{lang},{lang}-.*",
                                     "--sub-format", "srt/vtt", "-o", os.path.join(OUT, "%(id)s.%(ext)s"),
                                     url=url), check=True)
    wav = os.path.join(OUT, f"{vid}.wav")
    if not os.path.exists(wav):
        src = os.path.join(OUT, f"{vid}.src")
        subprocess.run(stream.ytdlp_args("-f", "bestaudio", "-o", src, url=url), check=True)
        subprocess.run([stream.ffmpeg_cmd(), "-loglevel", "error", "-y", "-i", src, "-vn", "-ac", "1",
                        "-ar", "16000", wav], check=True)
        os.remove(src)
    print(f"  {vid} {lang} 준비됨")


if __name__ == "__main__":
    items = [(sys.argv[1], sys.argv[2])] if len(sys.argv) >= 3 else GOLD
    for vid, lang in items:
        fetch(vid, lang)
