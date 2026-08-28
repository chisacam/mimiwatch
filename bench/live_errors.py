"""라이브 세션이 실패했을 때 무엇이 남는지 확인합니다.

이슈 #1의 두 번째 보고에서, 화면과 로그에 뜬 것은 진짜 원인이 아니라
`UnboundLocalError: cannot access local variable 'asr'` 였습니다. `_run`의
`finally`가 `del asr, vad, history, refiner` 를 하는데, try가 그 이름들이
만들어지기 전에 실패하면 그 `del` 자체가 터집니다. 그러면

  - 진짜 예외가 그 오류로 덮여 무엇이 잘못됐는지 알 수 없고,
  - 뒤따르는 `_release()`와 `_retire()`가 실행되지 않아 3GB짜리 모델이
    얹힌 채 남습니다.

실패는 흔합니다 -- 주소가 라이브가 아니거나, yt-dlp가 낡았거나, 포맷을
못 풀거나. 그때마다 원인이 가려지면 보고를 받아도 손댈 곳을 모릅니다.

    .venv/bin/python bench/live_errors.py
"""
from __future__ import annotations

import os, sys, types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live                                                  # noqa: E402

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def make_session():
    """모델을 올리지 않고 _run 만 돌릴 수 있는 최소한의 세션."""
    s = live.LiveSession.__new__(live.LiveSession)
    for k, v in dict(
            id="t", url="https://example.invalid/x", lang="ja", viewer_lang="ko",
            backend_id="local-gemma", asr_backend_id="tcpp-best", genre="general",
            refine=True, state="starting", error=None, title="", video_id="",
            media_base=0.0, window_s=0.0, audio_s=0.0, started=0.0, lines=0,
            translated=0, profile="broadcast", max_speech=4.0, min_silence=0.3,
            asr_label="", _ff=None, _asr=None, _tr=None, _recent=[], _subs=[],
            _seq=0).items():
        setattr(s, k, v)
    s._persist = lambda: None
    s.emit = lambda e: None
    return s


def run_failing(monkey):
    """_run 을 실패시키고, 밖으로 새는 예외와 정리 여부를 함께 돌려줍니다."""
    released, retired = [], []
    real_retire, real_sub = live._retire, live.subprocess
    live._retire = lambda sid: retired.append(sid)
    live.subprocess = monkey
    s = make_session()
    s._release = lambda: released.append(True)
    try:
        s._run()
        escaped = None
    except BaseException as e:                 # noqa: BLE001
        escaped = f"{type(e).__name__}: {e}"
    finally:
        live._retire, live.subprocess = real_retire, real_sub
    return s, escaped, released, retired


def main():
    print("[1] yt-dlp 를 찾지 못할 때 (asr 가 만들어지기 전에 실패)")
    boom = types.SimpleNamespace(
        run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("yt-dlp 없음")),
        Popen=None)
    s, escaped, released, retired = run_failing(boom)
    check(escaped is None, f"예외가 밖으로 새지 않는다 ({escaped})")
    check(s.state == "error", f"상태가 error 다 ({s.state})")
    check("yt-dlp" in (s.error or ""), f"진짜 원인이 남는다 ({s.error})")
    check(bool(released), "_release() 가 불린다 (모델을 놓아준다)")
    check(retired == ["t"], f"_retire() 가 불린다 ({retired})")

    print("\n[2] 라이브가 아닌 주소일 때 (try 안에서 return)")
    class Done:
        returncode = 0
        stdout = '{"title": "x", "id": "y", "is_live": false}'
        stderr = ""
    vod = types.SimpleNamespace(run=lambda *a, **k: Done(), Popen=None)
    s, escaped, released, retired = run_failing(vod)
    check(escaped is None, f"예외가 밖으로 새지 않는다 ({escaped})")
    check(s.state == "error", f"상태가 error 다 ({s.state})")
    check("라이브" in (s.error or ""), f"안내가 남는다 ({s.error})")
    check(bool(released) and retired == ["t"], "정리가 돈다")

    print("\n[3] resolve_audio 가 yt-dlp 의 말을 실어 보내는가")
    class NoFmt:
        returncode = 1
        stdout = ""
        stderr = "ERROR: [youtube] requested format is not available"
    real = live.subprocess
    live.subprocess = types.SimpleNamespace(run=lambda *a, **k: NoFmt(), Popen=None)
    try:
        live.resolve_audio("https://example.invalid/x")
        msg = ""
    except RuntimeError as e:
        msg = str(e)
    finally:
        live.subprocess = real
    check("requested format is not available" in msg,
          "yt-dlp 가 한 말이 그대로 실린다")
    check("234" in msg, "어느 포맷을 시도했는지도 남는다")

    print()
    if FAIL:
        print(f"{len(FAIL)} 건 실패")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
