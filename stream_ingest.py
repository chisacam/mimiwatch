"""Feed a live HLS/YouTube stream's audio into hayamimi's /ingest endpoint.

Measurement harness for the mimiwatch requirements draft: it answers the two
questions the spec could not settle on paper -- whether the yt-dlp -> ffmpeg
-> hayamimi path works at all on a real live stream, and how far behind the
speaker the subtitles land.

    python stream_ingest.py --url https://www.youtube.com/watch?v=...
    python stream_ingest.py --m3u8 https://.../index.m3u8

hayamimi must already be running with --input ws, e.g.
    python scripts/realtime_transcribe.py --input ws --serve \
        --mode single --lang ja --translate ko
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time

HAYAMIMI = os.environ.get("HAYAMIMI_DIR", "/Users/chiyak/hobby/hayamimi")
sys.path.insert(0, os.path.join(HAYAMIMI, "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from ws_protocol import (OP_BINARY, OP_TEXT, build_handshake_request,  # noqa: E402
                         decode_frame, encode_frame)

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
audio_sent_s = 0.0   # seconds of audio handed to the engine so far
CHUNK_S = 0.1
CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_S) * BYTES_PER_SAMPLE


def resolve_audio_m3u8(url: str) -> str:
    """Ask yt-dlp for the audio-only rendition.

    Format 234 is YouTube's high-bitrate audio-only HLS for live broadcasts;
    'bestaudio' falls back to whatever the extractor offers for VODs.
    """
    for fmt in ("234", "233", "bestaudio"):
        out = subprocess.run(["yt-dlp", "--no-warnings", "-f", fmt, "-g", url],
                             capture_output=True, text=True)
        line = out.stdout.strip().splitlines()
        if out.returncode == 0 and line:
            print(f"[ingest] yt-dlp format={fmt}", file=sys.stderr)
            return line[0]
    raise SystemExit("yt-dlp could not resolve an audio stream for this URL")


def program_date_time(m3u8_url: str):
    """First EXT-X-PROGRAM-DATE-TIME in the media playlist, if the stream
    publishes one. This is what makes automatic subtitle alignment possible
    on a live stream instead of a hand-tuned offset."""
    try:
        import urllib.request
        with urllib.request.urlopen(m3u8_url, timeout=10) as r:
            for raw in r.read().decode("utf-8", "replace").splitlines():
                if raw.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                    return raw.split(":", 1)[1].strip()
    except Exception as exc:
        print(f"[ingest] PDT probe failed: {exc}", file=sys.stderr)
    return None


def ws_connect(host: str, port: int, path: str = "/ingest") -> socket.socket:
    sock = socket.create_connection((host, port), timeout=10.0)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock.sendall(build_handshake_request(host, port, path, key))
    buf = b""
    while b"\r\n\r\n" not in buf:
        data = sock.recv(4096)
        if not data:
            raise ConnectionError("server closed during handshake")
        buf += data
    return sock


def reader(sock: socket.socket, started: float, events: list):
    """Record every subtitle event with the audio position at arrival.

    The engine is fed at real-time pace, so `audio_sent_s` at the moment an
    event lands is how much of the broadcast the engine has heard. The gap
    between that and the event is the pipeline delay a viewer would feel.
    """
    buf = b""
    while True:
        try:
            data = sock.recv(65536)
        except OSError:
            return
        if not data:
            return
        buf += data
        while True:
            frame = decode_frame(buf)
            if frame is None:
                break
            opcode, payload, consumed = frame
            buf = buf[consumed:]
            if opcode != OP_TEXT:
                continue
            try:
                ev = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            t = time.time() - started
            kind = ev.get("type")
            if kind in ("final", "refine", "translation"):
                events.append({"t": round(t, 3), "audio_s": round(audio_sent_s, 3),
                               "type": kind, "lang": ev.get("lang", ""),
                               "text": ev.get("text", "")})
                tag = {"final": "확정", "refine": "정제", "translation": "번역"}[kind]
                print(f"[t={t:7.2f}s audio={audio_sent_s:7.2f}s] {tag} "
                      f"({ev.get('lang','')}) {ev.get('text','')}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="YouTube (or other yt-dlp supported) page URL")
    ap.add_argument("--m3u8", help="direct HLS manifest URL, bypassing yt-dlp")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = run until killed)")
    ap.add_argument("--out", metavar="PATH", help="write the event log as JSON for latency analysis")
    args = ap.parse_args()

    if not (args.url or args.m3u8):
        raise SystemExit("--url or --m3u8 is required")

    src = args.m3u8 or resolve_audio_m3u8(args.url)
    pdt = program_date_time(src)
    print(f"[ingest] EXT-X-PROGRAM-DATE-TIME: {pdt or '없음 (자동 정렬 불가, 수동 오프셋 필요)'}",
          file=sys.stderr)

    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", src,
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE)

    sock = ws_connect(args.host, args.port)
    sock.sendall(encode_frame(
        json.dumps({"sr": SAMPLE_RATE, "format": "pcm_s16le", "channels": 1}).encode(),
        OP_TEXT, mask=True))

    started = time.time()
    events: list = []
    threading.Thread(target=reader, args=(sock, started, events), daemon=True).start()
    print(f"[ingest] streaming audio to ws://{args.host}:{args.port}/ingest", file=sys.stderr)

    global audio_sent_s
    sent = 0
    try:
        while True:
            chunk = ff.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            sock.sendall(encode_frame(chunk, OP_BINARY, mask=True))
            sent += len(chunk)
            audio_sent_s = sent / (SAMPLE_RATE * BYTES_PER_SAMPLE)
            if args.seconds and time.time() - started >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        audio_s = sent / (SAMPLE_RATE * BYTES_PER_SAMPLE)
        print(f"[ingest] sent {audio_s:.1f}s of audio in {time.time()-started:.1f}s wall clock",
              file=sys.stderr)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=1)
            print(f"[ingest] {len(events)} events -> {args.out}", file=sys.stderr)
        ff.terminate()
        sock.close()


if __name__ == "__main__":
    main()
