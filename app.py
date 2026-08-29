"""묶음(PyInstaller)의 진입점. 저장소에서 돌 때는 `server.py`를 그대로 씁니다.

실행 파일 하나가 세 얼굴을 갖습니다.

  mimiwatch                  서버를 띄우고 브라우저로 화면을 엽니다 (두 번 눌러 띄우는 경우)
  mimiwatch --port 8951      다른 포트. `--no-browser`로 브라우저를 열지 않습니다
  mimiwatch --ytdlp ...      yt-dlp로 동작합니다 -- 묶음 안에는 `python -m yt_dlp`를
                             부를 파이썬이 없으므로 서버가 자기 자신을 이렇게 다시
                             띄웁니다(`stream.ytdlp_cmd`). 자식 프로세스여야 시간
                             상한을 걸고 죽일 수 있습니다.
  mimiwatch --doctor [주소]  bench/doctor.py -- 준비물·백엔드·모델 적재를 한 번에 찍습니다.
                             묶음에는 파이썬이 없어 그 스크립트를 따로 돌릴 수 없으므로
                             여기 붙였습니다.

창 없이 돌 때(맥의 .app) 표준 출력은 아무 데도 가지 않습니다. 그때는 사용자
영역의 `mimiwatch.log`로 돌립니다 -- 무엇이 잘못됐는지 볼 곳이 그것뿐입니다.
"""
from __future__ import annotations

import os
import sys


def _ytdlp(argv: list[str]) -> int:
    import yt_dlp
    sys.argv = ["yt-dlp"] + argv
    try:
        yt_dlp.main()
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 1
    return 0


def _trust_store():
    """묶음 안의 파이썬에 CA 목록을 알려 줍니다.

    PyInstaller 가 넣어 주는 OpenSSL 은 만든 기계의 인증서 경로를 기억하고 있어
    다른 기계에서는 그 파일이 없습니다. 그러면 허깅페이스도 깃허브도
    CERTIFICATE_VERIFY_FAILED 가 됩니다 -- 맥의 파이썬은 키체인을 읽지 않습니다.
    certifi 의 묶음을 가리키면 우리 요청과, 자식으로 뜨는 yt-dlp 도 함께 봅니다.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _log_to_file():
    """표준 출력이 없으면(창 없는 묶음) 로그 파일로 보냅니다."""
    import paths
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return
    except Exception:
        pass
    os.makedirs(paths.home(), exist_ok=True)
    f = open(paths.log_path(), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f


def main() -> int:
    argv = sys.argv[1:]
    _trust_store()
    if argv and argv[0] == "--ytdlp":
        return _ytdlp(argv[1:])
    if argv and argv[0] == "--doctor":
        import runpy
        import paths
        sys.argv = ["doctor"] + argv[1:]
        runpy.run_path(os.path.join(paths.BASE, "bench", "doctor.py"), run_name="__main__")
        return 0
    # 윈도우 콘솔의 기본 인코딩(cp949)으로는 한글 제목이 깨집니다.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    _log_to_file()
    want_browser = "--no-browser" not in argv
    argv = [a for a in argv if a != "--no-browser"]
    if want_browser and "--open" not in argv:
        argv.append("--open")
    import server
    return server.main(argv) or 0


if __name__ == "__main__":
    raise SystemExit(main())
