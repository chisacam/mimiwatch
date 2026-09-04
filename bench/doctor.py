"""Print in one pass how far an installed machine gets.

When looking at an installation problem remotely, having the other side run
this one thing and paste the output is faster than asking for several separate
commands and receiving the answers piecemeal. What works, what does not, and
which exception it is when it does not, all stay on one screen.

    .venv/bin/python bench/doctor.py                 (Windows: .venv\\Scripts\\python.exe)
    .venv/bin/python bench/doctor.py <youtube url>   also checks URL resolution

It does nothing heavy. The transcription model is loaded and released right
away, and the translation model (5GB) is not touched.
"""
from __future__ import annotations

import os, platform, subprocess, sys, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD = "  [OK]", "  [X ]"


def section(t):
    print(f"\n=== {t} " + "=" * max(0, 52 - len(t)))


def line(good, what, extra=""):
    print(f"{OK if good else BAD} {what}" + (f"  {extra}" if extra else ""))


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None

    section("기계")
    print(f"  {platform.platform()}")
    print(f"  Python {sys.version.split()[0]} · 논리 코어 {os.cpu_count()}")

    section("준비물")
    import paths
    path = paths.which("ffmpeg")
    ver = ""
    if path:
        try:
            r = subprocess.run([path, "-version"], capture_output=True,
                               text=True, timeout=20, stdin=subprocess.DEVNULL)
            ver = (r.stdout or r.stderr).strip().splitlines()[0][:40]
        except Exception as e:                              # noqa: BLE001
            ver = f"(판을 묻지 못했습니다: {e})"
    line(bool(path), "ffmpeg", ver or "없음")

    # Look at **what actually gets called**, not what is installed on the
    # system. If it is in the virtualenv that one is used, otherwise we fall
    # back to PATH.
    import stream, live
    cmd = stream.ytdlp_cmd()
    # Which one gets called: tool directory (standalone binary) / bundle / virtualenv / PATH.
    if paths.tool("yt-dlp") and cmd[0] == paths.tool("yt-dlp"):
        where = "도구 디렉터리(독립 실행 파일)"
    elif cmd[0] == "yt-dlp":
        where = "PATH(시스템)"
    else:
        where = "묶음 안" if paths.frozen() else "가상환경"
    v = live.ytdlp_version()
    line(bool(v), f"yt-dlp ({where})", v or "부를 수 없습니다")
    # A stale copy gets no formats at all from YouTube. Issue #1 was that --
    # 234, 233 and bestaudio were all "not available", and it was not that
    # the formats were missing but that the list could not be read.
    # Since 2025.11 YouTube needs a JS runtime (deno) to be whole. A public
    # live stream (HLS) works without it, but the formats vanish on the VOD
    # and cookie (membership) paths.
    deno = stream.deno_path()
    line(bool(deno), "deno (유튜브 JS 런타임)",
         deno or "없음 -- 녹화본·멤버십 방송은 「모델·도구」에서 받으십시오")
    if live.ytdlp_stale(v):
        print(f"       ↳ 석 달이 넘었습니다. 설치 스크립트를 다시 돌리면 "
              f"가상환경 것이 최신으로 올라갑니다.")
    elif where.startswith("PATH"):
        print("       ↳ 예전 설치본입니다. 설치 스크립트를 다시 돌리면 "
              "가상환경 안으로 들어와 판올림이 자동이 됩니다.")

    section("전사 런타임")
    try:
        import transcribe_cpp as tc
        kinds = sorted({b.kind for b in tc.backends()})
        line(True, "transcribe_cpp 적재", f"백엔드: {', '.join(kinds)}")
        for b in tc.backends():
            print(f"       {b.kind:<7} {b.description}")
    except Exception:
        line(False, "transcribe_cpp 적재")
        traceback.print_exc()
        return 1

    section("전사 모델 올리기")
    import config, stream, tcpp_asr
    # Look at the model of the default transcription engine. This used to have
    # whisper hard-coded, so on an installation whose default is the light
    # engine it stopped at "model file missing".
    spec = config.find_asr(config.active("asr")) or {"backend": "tcpp"}
    if spec.get("backend", "tcpp") != "tcpp":
        line(True, f"기본 전사기는 원격({spec.get('id')})입니다. 로컬 적재는 건너뜁니다")
        spec = {"backend": "tcpp"}
    path = os.path.join(stream.model_dir(), spec.get("model") or "whisper-large-v3-turbo-Q8_0.gguf")
    line(os.path.exists(path), "모델 파일", path)
    if not os.path.exists(path):
        print("       ↳ 화면의 「엔진 관리 › 모델·도구」에서 받거나 modelhub.py download default")
        return 1
    # auto and cpu are checked separately. There are cases that blow up only
    # on the GPU, and then writing device: cpu into backends.json is itself
    # the fix.
    for device in ("auto", "cpu"):
        t0 = time.time()
        try:
            asr = tcpp_asr.build_live_asr({**spec, "device": device}, "ja")
            line(True, f"device={device}", f"{asr.device} · {asr.threads}스레드 · "
                                           f"{time.time() - t0:.1f}초")
            del asr
        except Exception as e:                              # noqa: BLE001
            line(False, f"device={device}", f"{type(e).__name__}: {e}")
            traceback.print_exc()

    section("번역 백엔드 (모델은 올리지 않습니다)")
    try:
        import translate
        for spec in ({"backend": "gemma"}, {"backend": "gemma", "device": "cpu"}):
            g = translate.build(spec).primary
            line(True, str(spec), f"n_gpu_layers={g.n_gpu_layers} threads={g._threads}")
        line(os.path.exists(translate.LocalGemma().model_path), "Gemma 파일",
             translate.LocalGemma().model_path)
    except Exception as e:                                  # noqa: BLE001
        line(False, "translate", f"{type(e).__name__}: {e}")

    if url:
        section("주소 해석")
        import live
        try:
            r = subprocess.run(["yt-dlp", "--no-warnings", "-j", url],
                               capture_output=True, text=True, timeout=90,
                               stdin=subprocess.DEVNULL)
            import json
            d = json.loads(r.stdout) if r.returncode == 0 else {}
            line(r.returncode == 0, "yt-dlp -j",
                 f"is_live={d.get('is_live')} live_status={d.get('live_status')}"
                 if d else (r.stderr or "").strip().splitlines()[-1:][0][:90])
        except Exception as e:                              # noqa: BLE001
            line(False, "yt-dlp -j", f"{type(e).__name__}: {e}")
        try:
            src, info = live.resolve_audio(url)
            line(True, "resolve_audio",
                 f"{info.get('segments')}개 구간 / {info.get('window_s', 0):.0f}초 창")
        except Exception as e:                              # noqa: BLE001
            line(False, "resolve_audio", f"{type(e).__name__}: {e}")

    print("\n끝났습니다. 위 내용을 그대로 붙여 주시면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
