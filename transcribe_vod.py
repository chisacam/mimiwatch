"""녹화본 한 편을 시각이 붙은 자막으로 만드는 조각들과, 그것을 명령줄에서
돌리는 껍데기.

녹화본 흐름에는 시계를 맞출 일이 없습니다. 재생보다 먼저 전부 전사하고,
자막마다 미디어 기준 시각이 붙어 있으니 브라우저는 유튜브 플레이어의
`getCurrentTime()`에 맞춰 찾기만 합니다. 2026-08-27 라이브 실측이 이 경로를
먼저 만든 이유입니다 -- 정제는 빠른 화자에게 최대 20초 뒤처지는데, 다 만든
뒤에 재생하는 녹화본에서는 그것이 문제가 되지 않습니다.

    python transcribe_vod.py --url https://youtu.be/... --viewer-lang ko

**명령줄은 서버와 같은 길을 씁니다.** 예전에는 여기 번역 루프가 한 벌 더
있었고 결과를 옛 모양(`data/<영상id>.json`)으로 썼습니다 -- 서버는 그 파일을
다음 기동에서 표로 옮겨야 알아봤습니다. 이제 `jobs.start_transcribe`를 그대로
부르고 진행률만 터미널에 찍습니다. 결과는 서버와 같은 `data/mimiwatch.db`에
들어가고, 서버를 켜면 목록에 바로 보입니다.
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

import stream
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
        subprocess.run([stream.ffmpeg_cmd(), "-loglevel", "error", "-y", "-i", tmp,
                        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), dest], check=True)
    except subprocess.CalledProcessError as exc:
        raise VodError(f"ffmpeg failed: {exc}") from exc
    except FileNotFoundError as exc:
        # ffmpeg가 없습니다. 내려받은 원본은 남겨 둡니다 -- 도구를 받은 뒤
        # 다시 넣으면 `dest`가 없으니 여기부터 다시 하고, 원본은 yt-dlp가
        # 같은 이름으로 덮어씁니다.
        raise VodError(str(exc)) from exc
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


# ---- 명령줄 ------------------------------------------------------------------

PHASE_LABEL = {"probe": "영상 정보 확인", "download": "오디오 내려받는 중",
               "transcribe": "전사 중", "translate": "번역 중", "done": "완료"}


def main():
    # 윈도우 콘솔의 기본 인코딩이 cp949라 한글 제목이 깨집니다. 예전에는 이 줄이
    # 모듈 맨 위에 있어서 서버가 이 모듈을 import 하는 순간 서버의 stdout까지
    # 바꿔 놓았습니다. 명령줄에서만 합니다.
    sys.stdout.reconfigure(encoding="utf-8")
    import translate as mw_translate
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--lang", help="원본 언어를 고정합니다 (비우면 자동 판별)")
    ap.add_argument("--viewer-lang", default="ko",
                    help="내 언어. 원본과 같으면 번역하지 않습니다")
    ap.add_argument("--genre", default=mw_translate.DEFAULT_GENRE,
                    choices=sorted(mw_translate.GENRE_PROMPTS),
                    help="발화의 성격에 맞는 번역 프롬프트를 고릅니다")
    ap.add_argument("--asr", default="", help="전사 엔진 id (비우면 설정의 기본)")
    ap.add_argument("--backend", default="", help="번역 엔진 id (비우면 설정의 기본)")
    ap.add_argument("--speakers", action="store_true",
                    help="화자 딱지를 붙입니다 (S1, S2, ...)")
    args = ap.parse_args()

    import jobs
    import store
    store.init()
    res = jobs.start_transcribe(args.url, args.lang, args.viewer_lang,
                                backend_id=args.backend, asr_id=args.asr,
                                speakers=args.speakers, genre=args.genre)
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
        raise VodError(st.get("error") or st.get("state") or "실패")
    print(f"[vod] 끝났습니다 ({st.get('elapsed', 0)}초). 영상 {st.get('video')} -- "
          f"서버를 켜면 목록에 보입니다.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except VodError as exc:
        # 명령줄에서는 종료 코드로 말합니다. 서버 스레드에서는 예외로 남겨야
        # 하므로 함수 안에서는 SystemExit을 쓰지 않습니다.
        raise SystemExit(str(exc))
