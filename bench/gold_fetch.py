"""정답 표본을 다시 만듭니다: 유튜브의 **수동** 자막이 있는 영상의 자막과 16kHz 오디오.

    .venv/bin/python bench/gold_fetch.py            # 아래 목록 전부 → data/gold/
    .venv/bin/python bench/gold_fetch.py <영상id> ja

data/ 는 저장소에 두지 않으므로(저작물) 이 스크립트가 표본의 출처입니다. 첫 표본 넷은
전부 뮤직비디오입니다 -- 원어 자막을 공식으로 제공하는 영상이 그쪽에 몰려 있어서이고,
노래는 전사의 가장 어려운 경우라 절대값은 높습니다. 설정 사이의 **차이**를 보는 용도입니다.
"""
from __future__ import annotations
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stream

# (영상 id, 자막 언어). 2026-08-29 현재 수동 자막이 있음을 확인한 것들.
GOLD = [("_xwOiIMM2a4", "ja"),   # 音乃瀬奏 - You＆合図 (가라오케식 스타일 자막)
        ("BgPBgTEvi08", "ja"),   # TAK - ニャニャニャチュニャ (반복 가사)
        ("CkvWJNt77mU", "ja"),   # ヨルシカ - 晴る (반주 위의 노랫소리, 자막 트랙 이름이 ja-*)
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
