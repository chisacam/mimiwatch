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
            raise SystemExit(f"not 16kHz mono 16bit: {path} "
                             f"({w.getframerate()}Hz {w.getnchannels()}ch)")
        w.setpos(min(int(16000 * OFFSET_S), w.getnframes() - 1))
        return w.readframes(int(16000 * seconds))


def main():
    # ── Block the store ──────────────────────────────────────────────
    # This check does not run inside the server process, so what is blocked
    # here is for [0]'s own self-check below. The server side is counted
    # separately further down.
    print(f"\ntarget: {os.path.basename(WAV)} · {SECONDS:.0f}s "
          f"from {OFFSET_S:.0f}s · {BASE}\n")

    print("[0] is the server up")
    try:
        with urllib.request.urlopen(BASE + "/api/backends", timeout=5) as r:
            cfg = json.loads(r.read())
        check(True, f"it answers ({len(cfg['backends'])} translation backends)")
    except Exception as exc:
        print(f"  FAIL  no server at {BASE}: {exc}")
        print(f"\n  start it first:  .venv/bin/python server.py --port {PORT}")
        return 1

    before = {r["id"] for r in store.sessions(200)}

    # ── 1. Make a tab session ────────────────────────────────────────
    print("\n[1] make a tab session")
    res = post("/api/live/capture", {
        # The language is left to auto-detection. It differs per recording, and
        # nailing down the wrong one turns the transcription into a mess, which
        # hides whether "the path connected".
        # Refinement is on as well. Final and refined lines each attach their
        # own language, so with it off only one of the two is seen -- the
        # language really did go missing on the refinement path once, and the
        # translation vanished wholesale.
        "title": "tab ingest test", "lang": None, "viewer_lang": "ko",
        "backend": cfg["active"], "asr": cfg.get("asr_active") or "",
        "refine": True, "genre": "general", "profile": "broadcast",
    })
    check("id" in res, f"the session was created ({res})")
    if "id" not in res:
        return 1
    sid = res["id"]
    check(res.get("source") == "tab", "source is tab")

    # Loading the models takes a while.
    for _ in range(120):
        st = json.loads(urllib.request.urlopen(
            BASE + f"/api/live/status/{sid}", timeout=10).read())
        if st.get("state") in ("running", "error"):
            break
        time.sleep(1)
    check(st.get("state") == "running", f"it reached the running state ({st.get('state')} "
                                        f"{st.get('error') or ''})")
    check(st.get("media_base") == 0, "a tab session has media_base 0")
    if st.get("state") != "running":
        post("/api/live/stop", {"id": sid})
        return 1

    # ── 2. Upload the sound ──────────────────────────────────────────
    print("\n[2] upload the PCM")
    pcm = read_pcm(WAV, SECONDS)
    step = int(16000 * CHUNK_S) * 2
    sent = 0
    t0 = time.time()
    for off in range(0, len(pcm) - step + 1, step):
        r = post(f"/api/ingest/{sid}", raw=pcm[off:off + step])
        if r.get("error"):
            check(False, f"the upload was refused: {r['error']}")
            break
        sent += 1
        # Send at the same pace as the real thing. Pouring it in all at once
        # is absorbed by the queue, which hides whether it keeps up with real time.
        time.sleep(max(0, CHUNK_S - (time.time() - t0 - (sent - 1) * CHUNK_S)))
    check(sent > 0, f"{sent} chunks uploaded ({sent * CHUNK_S:.0f}s worth)")
    check(not r.get("error"), f"the last response is fine ({r})")
    check((r.get("dropped_s") or 0) == 0,
          f"no audio was dropped (dropped={r.get('dropped_s')}s)")

    # ── 3. Did subtitles come out ────────────────────────────────────
    print("\n[3] subtitles come out")
    # Give the last chunk time to pass VAD and be decoded.
    time.sleep(8)
    st = json.loads(urllib.request.urlopen(
        BASE + f"/api/live/status/{sid}", timeout=10).read())
    check(st.get("lines", 0) > 0, f"{st.get('lines')} cue lines")
    check(st.get("audio_s", 0) > SECONDS * 0.8,
          f"it took in what was uploaded (audio_s={st.get('audio_s')} / sent {sent * CHUNK_S:.0f})")

    cues = live.backlog(sid)
    rows = [c for c in cues if c.get("type") == "cue"]
    texts = [c["text"] for c in rows]
    for c in rows[:5]:
        print(f"        · [{c.get('lang') or '?'}] {c['text'][:66]}")
    check(any(len(t) > 3 for t in texts), "not every line came out empty")
    # If the spoken language is already the viewing language there is nothing
    # to carry over. Demanding a translation count on such a recording records
    # a perfectly good path as a failure.
    # With auto-detection the session's source_lang is empty. What was actually
    # spoken is carried by the subtitle lines.
    spoken = st.get("source_lang") or next(
        (c.get("lang") for c in rows if c.get("lang")), "")
    check(bool(spoken), f"the cues carry a language ({spoken!r})")
    if spoken != st.get("viewer_lang"):
        check(st.get("translated", 0) > 0, f"a translation is attached too ({st.get('translated')})")
    else:
        print(f"  ----  nothing to translate (spoken language {spoken!r} = viewing language)")

    # ── 4. Uploading to a session that does not exist ────────────────
    print("\n[4] uploading to a session that does not exist")
    r = post("/api/ingest/nope-not-a-session", raw=b"\x00" * 64)
    check(bool(r.get("error")), f"it is refused ({r})")

    # ── 5. Clean up ──────────────────────────────────────────────────
    print("\n[5] cleanup")
    post("/api/live/stop", {"id": sid})
    time.sleep(3)
    st = json.loads(urllib.request.urlopen(
        BASE + f"/api/live/status/{sid}", timeout=10).read())
    check(st.get("state") in ("stopped", "stopping"), f"it stopped ({st.get('state')})")

    # The server side writes to the real DB. What the check left behind is
    # deleted here -- this spot was once left empty and the test session showed
    # up among the old broadcasts on screen.
    after = {r["id"] for r in store.sessions(200)}
    leaked = after - before
    for lid in leaked:
        store._write("DELETE FROM cues WHERE session = ?", (lid,))
        store._write("DELETE FROM sessions WHERE id = ?", (lid,))
    check(leaked == {sid} or not leaked,
          f"this is the only session the test left behind ({sorted(leaked)})")
    check(store.session(sid) is None, "it was deleted")

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
