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
            _seq=0, source="hls", resume_from=0.0, gap_s=0.0,
            _queued=0, dropped_s=0.0, stopped_by="").items():
        setattr(s, k, v)
    s._q = __import__("queue").Queue()
    s._pub_lock = __import__("threading").RLock()
    s._persist = lambda: None
    s.emit = lambda e: None
    s._subs = []
    return s


def run_failing(monkey):
    """_run 을 실패시키고, 밖으로 새는 예외와 정리 여부를 함께 돌려줍니다."""
    released, retired = [], []
    real_retire, real_sub = live._retire, live.subprocess
    # _retire 는 이제 세션 객체를 받습니다(같은 id 의 이어받은 세션을 밀어내지 않으려고).
    live._retire = lambda s: retired.append(getattr(s, "id", s))
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

    print("\n[4] 전사 엔진을 갈아 끼워도 세션과 자막이 유지되는가")
    # 이것이 요점입니다. 예전에는 엔진을 바꾸려면 세션을 다시 시작해야
    # 했고, 자막은 세션 id로 저장되므로 그때까지의 스크립트가 사라졌습니다.
    swapped = {}

    class FakeAsr:
        label = "old-model"
        def swap(self, spec):
            swapped["spec"] = spec
            self.label = "new-model"
            return {"label": self.label, "device": "cpu", "threads": 8}

    s = make_session()
    s._asr = FakeAsr()
    s.asr_label = "old-model"
    s._recent = [{"t": 1.0, "text": "이미 받아 적은 줄"}]
    before_id = s.id
    live._sessions[s.id] = s
    # 설정은 config.py가 답합니다. 예전에는 live.py가 backends.json을 직접
    # 열어서 builtins.open을 갈아 끼웠는데, 이제 그 파일을 읽는 곳은
    # config 하나라 그쪽 함수를 바꿔 끼우면 됩니다.
    lite = {"id": "tcpp-lite", "backend": "tcpp", "model": "SenseVoiceSmall-Q8_0.gguf"}
    real_find_asr = live.config.find_asr
    live.config.find_asr = lambda bid: lite if bid == "tcpp-lite" else None
    try:
        res = live.set_asr(s.id, "tcpp-lite")
    finally:
        live.config.find_asr = real_find_asr
        live._sessions.pop(s.id, None)

    check(not res.get("error"), f"갈아 끼웠다 ({res})")
    check(s.id == before_id, f"세션 id가 그대로다 ({s.id})")
    check(s._recent == [{"t": 1.0, "text": "이미 받아 적은 줄"}],
          "이미 받아 적은 줄이 남아 있다")
    check(s.asr_backend_id == "tcpp-lite" and s.asr_label == "new-model",
          f"엔진 이름이 바뀌었다 ({s.asr_label})")
    check(swapped.get("spec", {}).get("model") == "SenseVoiceSmall-Q8_0.gguf",
          "설정이 그대로 전달됐다")

    print("\n[5] 갈아 끼우기가 실패하면 쓰던 것이 남는가")
    class Refuses(FakeAsr):
        def swap(self, spec):
            raise RuntimeError("이 모델은 'ja' 언어를 지원하지 않습니다")
    s2 = make_session()
    s2._asr = Refuses()
    s2.asr_backend_id = "tcpp-best"
    s2.asr_label = "old-model"
    live._sessions[s2.id] = s2
    live.config.find_asr = lambda bid: lite if bid == "tcpp-lite" else None
    try:
        res2 = live.set_asr(s2.id, "tcpp-lite")
    finally:
        live.config.find_asr = real_find_asr
        live._sessions.pop(s2.id, None)
    check("error" in res2, f"실패를 알린다 ({res2.get('error', '')[:40]})")
    check(s2.asr_backend_id == "tcpp-best" and s2.asr_label == "old-model",
          "쓰던 엔진이 그대로다")

    print("\n[6] 끊긴 세션을 같은 세션으로 이어받는가")
    started = {}
    real_store_session, real_store_cues = live.store.session, live.store.cues
    # **쓰기 경로도 막습니다.** 읽기만 가짜로 바꿨더니 resume() 안의
    # _persist() 가 실제 DB에 시험용 세션을 남겼고, 그것이 사용자의 영상
    # 목록에 「옛 방송」으로 떴습니다. 시험은 저장소를 건드리지 않아야 합니다.
    real_save_session, real_save_job = live.store.save_session, live.store.save_job
    live.store.save_session = lambda *a, **k: None
    live.store.save_job = lambda *a, **k: None
    live.store.session = lambda sid: {
        "id": sid, "state": "interrupted", "url": "https://example.invalid/live",
        "source_lang": "ja", "viewer_lang": "ko", "backend": "local-gemma",
        "asr_backend": "tcpp-best", "profile": "collab", "refine": True,
        "genre": "gaming", "title": "옛 방송", "video_id": "vid1",
        "media_base": 400.0, "audio_s": 1100.0,
    }
    live.store.cues = lambda sid: [{"id": 1}, {"id": 2}, {"id": 7}]
    real_start = live.LiveSession.start
    live.LiveSession.start = lambda self: started.setdefault("self", self)
    try:
        res = live.resume("sess-1")
        s = started.get("self")
    finally:
        live.LiveSession.start = real_start
        live.store.session, live.store.cues = real_store_session, real_store_cues
        live.store.save_session, live.store.save_job = real_save_session, real_save_job
        live._sessions.pop("sess-1", None)

    check(not res.get("error"), f"이어받기가 받아들여졌다 ({res})")
    check(s is not None and s.id == "sess-1", "세션 id를 그대로 쓴다")
    check(s and s._seq == 7, f"번호가 이어진다 (_seq={s and s._seq})")
    check(s and s.lines == 3, f"줄 수를 물려받는다 ({s and s.lines})")
    check(s and abs(s.resume_from - 1500.0) < 0.01,
          f"끊긴 미디어 위치를 계산한다 ({s and s.resume_from})")
    check(s and s.profile == "collab" and s.genre == "gaming" and s.refine,
          "설정을 그대로 물려받는다")

    print("\n[7] 이어받을 수 없는 경우")
    live.store.save_session = lambda *a, **k: None
    live.store.session = lambda sid: None
    check("error" in live.resume("없음"), "없는 세션은 거절한다")
    live.store.session = lambda sid: {"id": sid, "state": "running", "url": "x"}
    check("error" in live.resume("sess-1"), "이미 받는 중이면 거절한다")
    live.store.session = lambda sid: {"id": sid, "state": "interrupted", "url": ""}
    check("error" in live.resume("sess-1"), "주소가 없으면 거절한다")
    live.store.session = real_store_session
    live.store.save_session = real_save_session

    # 시험이 저장소에 아무것도 남기지 않았는지 스스로 확인합니다.
    check(live.store.session("sess-1") is None,
          "시험용 세션이 저장소에 남지 않았다")

    print("\n[8] 값이 None 인 자막을 저장해도 세션이 죽지 않는가")
    # 원본 언어를 자동 판별에 맡기면 lang 이 None 으로 흘러갑니다. cues 열은
    # NOT NULL 이고, `.get(k, "")` 는 **키가 있고 값이 None 이면** 기본값을
    # 내지 않으므로 None 이 그대로 바인딩되어 첫 확정 줄에서 세션이
    # 끝났습니다. 자막 한 줄을 잃는 것이 아니라 방송을 잃는 자리입니다.
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    real_db, real_data, real_conn = live.store.DB, live.store.DATA, live.store._db
    live.store.DB = os.path.join(tmp, "t.db")
    live.store.DATA = tmp
    live.store._db = None
    try:
        live.store.init()
        live.store.save_cue("s", {"id": 1, "kind": "final", "t": 1.0,
                                  "text": "안녕", "lang": None, "speaker": None})
        row = live.store.cues("s")[0]
        check(row["lang"] == "" and row["speaker"] == "",
              f"None 이 빈 문자열로 저장된다 (lang={row['lang']!r})")
        live.store.save_cue("s", {"id": 2, "kind": None, "t": None,
                                  "text": None, "lang": None, "speaker": None})
        check(len(live.store.cues("s")) == 2, "전부 None 이어도 저장된다")
    except Exception as exc:                                # noqa: BLE001
        check(False, f"저장에서 예외가 났다: {type(exc).__name__}: {exc}")
    finally:
        live.store.DB, live.store.DATA, live.store._db = real_db, real_data, real_conn
        shutil.rmtree(tmp, ignore_errors=True)

    # 자동 판별에 맡겨도 어댑터가 None 을 흘리지 않아야 합니다.
    import tcpp_asr
    a = tcpp_asr.TranscribeCppASR.__new__(tcpp_asr.TranscribeCppASR)
    for lang in (None, "", "ja"):
        a.forced_lang = lang or ""
        check(a.forced_lang is not None and isinstance(a.forced_lang, str),
              f"forced_lang 이 문자열이다 (입력 {lang!r} -> {a.forced_lang!r})")

    print()
    if FAIL:
        print(f"{len(FAIL)} 건 실패")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
