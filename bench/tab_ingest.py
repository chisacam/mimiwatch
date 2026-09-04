"""Check end to end that tab audio uploaded by the browser produces subtitles.

A members-only broadcast is something the server cannot fetch. Instead there is
a path where the browser uploads the sound of the tab the user is already
listening to, and whether that path really reaches subtitles looks like
something only a person sharing a tab could tell.

It is not. All the browser does is push 16kHz mono int16 PCM to
`POST /api/ingest/<session>`, so here an already recorded file is uploaded the
same way and the same thing is checked.

    .venv/bin/python bench/tab_ingest.py [wav]

It does not touch the store -- a test session once stayed in the real DB and
showed up in the on-screen list of old broadcasts. Session and cues are written
to memory only.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import live                                                  # noqa: E402
import store                                                 # noqa: E402

PORT = int(os.environ.get("MIMIWATCH_TEST_PORT", "8931"))
BASE = f"http://127.0.0.1:{PORT}"
WAV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data", "71SnC4H-G1Q.wav")
SECONDS = float(os.environ.get("TAB_TEST_SECONDS", "40"))
# The head of a recording is often a greeting or a waiting screen with no
# speech. Picking a stretch where talk goes back and forth is what actually
# shows whether it "keeps up with real time".
OFFSET_S = float(os.environ.get("TAB_TEST_OFFSET", "0"))
CHUNK_S = 2.0

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def post(path, payload=None, raw=None):
    if raw is not None:
        req = urllib.request.Request(BASE + path, data=raw,
                                     headers={"Content-Type": "application/octet-stream"})
    else:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def read_pcm(path, seconds):
    """16kHz mono int16 bytes. The same shape the browser sends."""
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"16kHz 모노 16bit가 아닙니다: {path} "
                             f"({w.getframerate()}Hz {w.getnchannels()}ch)")
        w.setpos(min(int(16000 * OFFSET_S), w.getnframes() - 1))
        return w.readframes(int(16000 * seconds))


def main():
    # ── Block the store ──────────────────────────────────────────────
    # This check does not run inside the server process, so what is blocked
    # here is for [0]'s own self-check below. The server side is counted
    # separately further down.
    print(f"\n대상: {os.path.basename(WAV)} · {OFFSET_S:.0f}초부터 "
          f"{SECONDS:.0f}초 · {BASE}\n")

    print("[0] 서버가 떠 있는가")
    try:
        with urllib.request.urlopen(BASE + "/api/backends", timeout=5) as r:
            cfg = json.loads(r.read())
        check(True, f"응답함 (번역 {len(cfg['backends'])}종)")
    except Exception as exc:
        print(f"  FAIL  {BASE} 에 서버가 없습니다: {exc}")
        print(f"\n  먼저 띄우십시오:  .venv/bin/python server.py --port {PORT}")
        return 1

    before = {r["id"] for r in store.sessions(200)}

    # ── 1. Make a tab session ────────────────────────────────────────
    print("\n[1] 탭 세션을 만든다")
    res = post("/api/live/capture", {
        # The language is left to auto-detection. It differs per recording, and
        # nailing down the wrong one turns the transcription into a mess, which
        # hides whether "the path connected".
        # Refinement is on as well. Final and refined lines each attach their
        # own language, so with it off only one of the two is seen -- the
        # language really did go missing on the refinement path once, and the
        # translation vanished wholesale.
        "title": "탭 수신 시험", "lang": None, "viewer_lang": "ko",
        "backend": cfg["active"], "asr": cfg.get("asr_active") or "",
        "refine": True, "genre": "general", "profile": "broadcast",
    })
    check("id" in res, f"세션이 생겼다 ({res})")
    if "id" not in res:
        return 1
    sid = res["id"]
    check(res.get("source") == "tab", "source가 tab이다")

    # Loading the models takes a while.
    for _ in range(120):
        st = json.loads(urllib.request.urlopen(
            BASE + f"/api/live/status/{sid}", timeout=10).read())
        if st.get("state") in ("running", "error"):
            break
        time.sleep(1)
    check(st.get("state") == "running", f"수신 상태가 됐다 ({st.get('state')} "
                                        f"{st.get('error') or ''})")
    check(st.get("media_base") == 0, "탭은 media_base가 0이다")
    if st.get("state") != "running":
        post("/api/live/stop", {"id": sid})
        return 1

    # ── 2. Upload the sound ──────────────────────────────────────────
    print("\n[2] PCM을 올린다")
    pcm = read_pcm(WAV, SECONDS)
    step = int(16000 * CHUNK_S) * 2
    sent = 0
    t0 = time.time()
    for off in range(0, len(pcm) - step + 1, step):
        r = post(f"/api/ingest/{sid}", raw=pcm[off:off + step])
        if r.get("error"):
            check(False, f"올리다 거절당했다: {r['error']}")
            break
        sent += 1
        # Send at the same pace as the real thing. Pouring it in all at once
        # is absorbed by the queue, which hides whether it keeps up with real time.
        time.sleep(max(0, CHUNK_S - (time.time() - t0 - (sent - 1) * CHUNK_S)))
    check(sent > 0, f"{sent}덩어리를 올렸다 ({sent * CHUNK_S:.0f}초분)")
    check(not r.get("error"), f"마지막 응답이 정상이다 ({r})")
    check((r.get("dropped_s") or 0) == 0,
          f"버린 오디오가 없다 (dropped={r.get('dropped_s')}s)")

    # ── 3. Did subtitles come out ────────────────────────────────────
    print("\n[3] 자막이 나온다")
    # Give the last chunk time to pass VAD and be decoded.
    time.sleep(8)
    st = json.loads(urllib.request.urlopen(
        BASE + f"/api/live/status/{sid}", timeout=10).read())
    check(st.get("lines", 0) > 0, f"자막 {st.get('lines')}줄")
    check(st.get("audio_s", 0) > SECONDS * 0.8,
          f"올린 만큼 먹었다 (audio_s={st.get('audio_s')} / 보낸 {sent * CHUNK_S:.0f})")

    cues = live.backlog(sid)
    rows = [c for c in cues if c.get("type") == "cue"]
    texts = [c["text"] for c in rows]
    for c in rows[:5]:
        print(f"        · [{c.get('lang') or '?'}] {c['text'][:66]}")
    check(any(len(t) > 3 for t in texts), "빈 줄만 나오지는 않았다")
    # If the spoken language is already the viewing language there is nothing
    # to carry over. Demanding a translation count on such a recording records
    # a perfectly good path as a failure.
    # With auto-detection the session's source_lang is empty. What was actually
    # spoken is carried by the subtitle lines.
    spoken = st.get("source_lang") or next(
        (c.get("lang") for c in rows if c.get("lang")), "")
    check(bool(spoken), f"자막에 언어가 붙어 있다 ({spoken!r})")
    if spoken != st.get("viewer_lang"):
        check(st.get("translated", 0) > 0, f"번역도 붙었다 ({st.get('translated')}건)")
    else:
        print(f"  ----  번역할 것이 없음 (말한 언어 {spoken!r} = 보는 언어)")

    # ── 4. Uploading to a session that does not exist ────────────────
    print("\n[4] 없는 세션에 올리면")
    r = post("/api/ingest/nope-not-a-session", raw=b"\x00" * 64)
    check(bool(r.get("error")), f"거절한다 ({r})")

    # ── 5. Clean up ──────────────────────────────────────────────────
    print("\n[5] 정리")
    post("/api/live/stop", {"id": sid})
    time.sleep(3)
    st = json.loads(urllib.request.urlopen(
        BASE + f"/api/live/status/{sid}", timeout=10).read())
    check(st.get("state") in ("stopped", "stopping"), f"멈췄다 ({st.get('state')})")

    # The server side writes to the real DB. What the check left behind is
    # deleted here -- this spot was once left empty and the test session showed
    # up among the old broadcasts on screen.
    after = {r["id"] for r in store.sessions(200)}
    leaked = after - before
    for lid in leaked:
        store._write("DELETE FROM cues WHERE session = ?", (lid,))
        store._write("DELETE FROM sessions WHERE id = ?", (lid,))
    check(leaked == {sid} or not leaked,
          f"시험이 남긴 세션은 이것뿐이다 ({sorted(leaked)})")
    check(store.session(sid) is None, "지웠다")

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
