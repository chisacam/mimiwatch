"""브라우저가 올린 탭 오디오로 자막이 나오는지 끝까지 확인합니다.

멤버십 전용 방송은 서버가 받을 수 없습니다. 대신 사용자가 이미 듣고 있는
탭의 소리를 브라우저가 올려 주는 길을 냈는데, 그 길이 실제로 자막까지
이어지는지는 사람이 탭을 공유해 봐야만 알 수 있는 것처럼 보입니다.

그렇지 않습니다. 브라우저가 하는 일은 16kHz 모노 int16 PCM을
`POST /api/ingest/<세션>` 으로 흘려보내는 것뿐이므로, 여기서는 이미
받아 둔 녹음을 같은 방식으로 올려 같은 것을 확인합니다.

    .venv/bin/python bench/tab_ingest.py [wav]

저장소를 건드리지 않습니다 -- 예전에 시험용 세션이 진짜 DB에 남아 화면의
「옛 방송」 목록에 떴습니다. 세션도 자막도 메모리에만 씁니다.
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
# 녹음 앞머리는 인사나 대기 화면이라 말이 없는 경우가 많습니다. 말이 오가는
# 구간을 골라야 "실시간을 따라가는가"를 실제로 봅니다.
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
    """16kHz 모노 int16 바이트. 브라우저가 보내는 것과 같은 모양입니다."""
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"16kHz 모노 16bit가 아닙니다: {path} "
                             f"({w.getframerate()}Hz {w.getnchannels()}ch)")
        w.setpos(min(int(16000 * OFFSET_S), w.getnframes() - 1))
        return w.readframes(int(16000 * seconds))


def main():
    # ── 저장소를 막습니다 ────────────────────────────────────────────
    # 이 시험은 서버 프로세스 안에서 돌지 않으므로, 여기서 막는 것은
    # 아래 [0]의 자기 점검용입니다. 서버 쪽은 아래에서 따로 셉니다.
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

    # ── 1. 탭 세션 만들기 ────────────────────────────────────────────
    print("\n[1] 탭 세션을 만든다")
    res = post("/api/live/capture", {
        # 언어는 자동 판별에 맡깁니다. 녹음마다 다르고, 틀린 언어를 못 박으면
        # 받아 적은 것이 엉망이 되어 "길이 이어졌는가"를 못 보게 됩니다.
        # 정제도 켭니다. 확정본과 정제본은 언어를 각자 붙이므로, 끄면
        # 둘 중 한쪽만 보게 됩니다 -- 실제로 정제 경로에서 언어가 빠져
        # 번역이 통째로 사라진 적이 있습니다.
        "title": "탭 수신 시험", "lang": None, "viewer_lang": "ko",
        "backend": cfg["active"], "asr": cfg.get("asr_active") or "",
        "refine": True, "genre": "general", "profile": "broadcast",
    })
    check("id" in res, f"세션이 생겼다 ({res})")
    if "id" not in res:
        return 1
    sid = res["id"]
    check(res.get("source") == "tab", "source가 tab이다")

    # 모델을 올리는 데 시간이 걸립니다.
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

    # ── 2. 소리를 올린다 ─────────────────────────────────────────────
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
        # 실전과 같은 속도로 보냅니다. 몰아서 부으면 큐가 흡수해 버려
        # 실시간을 따라가는지를 못 봅니다.
        time.sleep(max(0, CHUNK_S - (time.time() - t0 - (sent - 1) * CHUNK_S)))
    check(sent > 0, f"{sent}덩어리를 올렸다 ({sent * CHUNK_S:.0f}초분)")
    check(not r.get("error"), f"마지막 응답이 정상이다 ({r})")
    check((r.get("dropped_s") or 0) == 0,
          f"버린 오디오가 없다 (dropped={r.get('dropped_s')}s)")

    # ── 3. 자막이 나왔는가 ───────────────────────────────────────────
    print("\n[3] 자막이 나온다")
    # 마지막 덩어리가 VAD를 통과하고 해독될 시간을 줍니다.
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
    # 말한 언어가 곧 보는 언어면 옮길 것이 없습니다. 그런 녹음에서
    # 번역 건수를 요구하면 멀쩡한 길을 실패로 적게 됩니다.
    # 자동 판별이면 세션의 source_lang은 비어 있습니다. 실제로 무슨 말이었는지는
    # 자막 줄이 들고 있습니다.
    spoken = st.get("source_lang") or next(
        (c.get("lang") for c in rows if c.get("lang")), "")
    check(bool(spoken), f"자막에 언어가 붙어 있다 ({spoken!r})")
    if spoken != st.get("viewer_lang"):
        check(st.get("translated", 0) > 0, f"번역도 붙었다 ({st.get('translated')}건)")
    else:
        print(f"  ----  번역할 것이 없음 (말한 언어 {spoken!r} = 보는 언어)")

    # ── 4. 없는 세션에 올리면 ────────────────────────────────────────
    print("\n[4] 없는 세션에 올리면")
    r = post("/api/ingest/nope-not-a-session", raw=b"\x00" * 64)
    check(bool(r.get("error")), f"거절한다 ({r})")

    # ── 5. 뒷정리 ────────────────────────────────────────────────────
    print("\n[5] 정리")
    post("/api/live/stop", {"id": sid})
    time.sleep(3)
    st = json.loads(urllib.request.urlopen(
        BASE + f"/api/live/status/{sid}", timeout=10).read())
    check(st.get("state") in ("stopped", "stopping"), f"멈췄다 ({st.get('state')})")

    # 서버 쪽은 진짜 DB에 씁니다. 시험이 남긴 것을 여기서 지웁니다 --
    # 예전에 이 자리를 비워 두었다가 화면의 「옛 방송」에 시험 세션이
    # 그대로 떴습니다.
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
