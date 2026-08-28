"""mimiwatch demo server: serves the player UI and the transcribed cue files.

Deliberately a plain stdlib HTTP server. The recorded-video flow has no
streaming component -- transcription finishes before playback starts -- so
there is nothing here that a framework would earn its dependency on.
"""
from __future__ import annotations

import json
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import export
import jobs
import live
import store
import stream
import translate

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
DATA = os.path.join(BASE, "data")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = posixpath.normpath(self.path.split("?")[0])

        if path == "/":
            return self._serve_file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")

        if path == "/api/videos":
            items = []
            names = sorted(os.listdir(DATA)) if os.path.isdir(DATA) else []
            # Newest first: after adding a video the one you just waited for
            # should be at the top, not wherever its id happens to sort.
            names.sort(key=lambda n: os.path.getmtime(os.path.join(DATA, n)),
                       reverse=True)
            for name in names:
                if not name.endswith(".json"):
                    continue
                d = jobs.load_video(name[:-5])
                items.append({k: d.get(k) for k in
                              ("id", "title", "duration", "uploader", "source_lang",
                               "viewer_lang", "translated", "audio_seconds",
                               "backends_done")} |
                             {"cues": len(d.get("cues", []))})
            return self._send(json.dumps(items, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")

        if path.startswith("/api/video/"):
            vid = os.path.basename(path[len("/api/video/"):])
            if not os.path.exists(os.path.join(DATA, f"{vid}.json")):
                return self._send(b'{"error":"not found"}', "application/json", 404)
            doc = jobs.load_video(vid)
            return self._json(doc)

        if path == "/api/backends":
            cfg = jobs.load_config()
            cfg["live_profiles"] = [{"id": k, **v} for k, v in live.PROFILES.items()]
            # 장르는 프롬프트만 바꾸므로 라이브·녹화본 양쪽에 씁니다.
            # 프롬프트 본문은 보내지 않습니다 -- 화면에 쓸 것은 이름과
            # 한 줄 설명뿐입니다.
            cfg["genres"] = [{"id": k, "label": v["label"], "hint": v["hint"]}
                             for k, v in translate.GENRE_PROMPTS.items()]
            return self._json(cfg)

        if path == "/api/live/sessions":
            return self._json(live.recent())

        if path.startswith("/api/live/events/"):
            sid = os.path.basename(path)
            sess = live.get(sid)
            if sess is None and live.status_of(sid) is None:
                return self._send(b'{"error":"no such session"}', "application/json", 404)
            return self._sse(sid, sess)

        if path.startswith("/api/live/status/"):
            st = live.status_of(os.path.basename(path))
            if st is None:
                return self._json({"error": "no such session"}, 404)
            return self._json(st)

        if path.startswith("/api/job/"):
            st = jobs.job_status(os.path.basename(path))
            if st is None:
                return self._send(b'{"error":"no such job"}', "application/json", 404)
            return self._json(st)

        if path == "/api/export":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            value = (q.get("id") or [""])[0]
            fmt = (q.get("fmt") or ["srt"])[0]
            view = (q.get("view") or ["both"])[0]
            try:
                meta, rows = export.collect(value)
                body, ctype = export.render(meta, rows, fmt, view)
            except KeyError:
                return self._send(b"not found", "text/plain", 404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # 제목에 한글과 일본어가 들어갑니다. filename= 은 ASCII 만 담을
            # 수 있으므로 RFC 5987 의 filename* 을 같이 보냅니다 -- 브라우저는
            # 둘 중 읽을 수 있는 쪽을 씁니다.
            name = export.filename(meta, fmt)
            quoted = urllib.parse.quote(name, safe="")
            # 앞의 filename= 은 filename* 을 못 읽는 도구를 위한 자리라
            # ASCII 여야 합니다. 세션 값을 그대로 쓰면 `live:xxx.srt` 가 되어
            # 윈도우에서 만들 수 없는 이름이 됩니다.
            plain = "".join(c for c in (value or "mimiwatch")
                            if c.isalnum() or c in "-_") or "mimiwatch"
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{plain}.{fmt}\"; "
                             f"filename*=UTF-8''{quoted}")
            self.end_headers()
            return self.wfile.write(body)

        if path.startswith("/static/"):
            target = os.path.join(WEB, os.path.basename(path))
            ctype = ("text/css; charset=utf-8" if target.endswith(".css")
                     else "text/javascript; charset=utf-8")
            return self._serve_file(target, ctype)

        self._send(b"not found", "text/plain", 404)

    def _sse(self, sid, sess):
        """Stream a live session's cues. Chosen over a WebSocket mirror, which
        dropped events under the same load (92 delivered vs 15 over the same
        window). That mirror lived in hayamimi and is not in this repo.

        The stored subtitles go out first, so a reloaded tab -- or a tab
        opening a session that outlived a restart -- sees the whole broadcast
        instead of only what is said from now on. Subscribing BEFORE reading
        the backlog is deliberate: the other order drops whatever is published
        in between, and a line arriving twice is harmless because the browser
        keys cues by id and updates in place.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = sess.subscribe() if sess is not None else None
        try:
            # 첫 프레임에 type이 없어서 브라우저가 그냥 흘려보내고 있었습니다.
            # 재시작으로 끊긴 세션은 이 한 번이 "왜 멈췄는지"를 말할 유일한
            # 기회이므로, 나머지 상태 알림과 같은 모양으로 맞춥니다.
            status = sess.status() if sess is not None else live.status_of(sid)
            for event in [{"type": "status", **status}, *live.backlog(sid)]:
                self.wfile.write(
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            if q is None:
                # 끝난 세션은 보낼 것을 다 보냈습니다. Content-Length가 없는
                # 응답이라 소켓을 닫아 주지 않으면 클라이언트는 끝을 알 수
                # 없습니다 -- 위의 keep-alive 헤더가 그 소켓을 살려 두므로
                # 여기서 되돌립니다.
                self.close_connection = True
                return
            while True:
                try:
                    data = q.get(timeout=15)
                except Exception:
                    self.wfile.write(b": keepalive\n\n")   # keep proxies honest
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
        except ConnectionError:
            # 시청자가 탭을 닫으면 끝나는 정상 경로입니다. 운영체제마다
            # 다른 예외를 냅니다 -- 리눅스/맥은 BrokenPipe나 ConnectionReset,
            # 윈도우는 ConnectionAborted(WinError 10053). 셋 다 ConnectionError
            # 아래에 있으므로 부모로 받습니다.
            pass
        finally:
            if q is not None:
                sess.unsubscribe(q)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def do_POST(self):
        path = posixpath.normpath(self.path.split("?")[0])
        length = int(self.headers.get("Content-Length") or 0)

        # 오디오만 JSON이 아닙니다. 아래에서 본문을 json.loads로 읽어 버리므로
        # 그 앞에서 갈라 냅니다. 몸통은 16kHz 모노 int16 PCM 날것입니다 --
        # base64로 감싸면 3분의 4가 되고, 초당 32KB짜리를 그럴 이유가 없습니다.
        if path.startswith("/api/ingest/"):
            sid = posixpath.basename(path)
            raw = self.rfile.read(length) if length else b""
            return self._json(live.feed(sid, raw))

        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)

        if path == "/api/translate":
            vid, backend = body.get("video"), body.get("backend")
            if not vid or not backend:
                return self._json({"error": "video and backend are required"}, 400)
            return self._json(jobs.start(vid, backend, body.get("genre")))

        if path == "/api/probe":
            # The UI needs to know which flow a URL belongs to before it
            # commits: a live broadcast and a finished video are different
            # pipelines with different waiting behaviour.
            import subprocess as sp
            out = sp.run(stream.ytdlp_cmd() + ["--no-warnings", "-j", body.get("url", "")],
                         capture_output=True, text=True)
            if out.returncode != 0:
                return self._json({"error": out.stderr.strip()[:200] or "주소를 해석할 수 없습니다"}, 400)
            d = json.loads(out.stdout)
            return self._json({"id": d.get("id"), "title": d.get("title"),
                               "is_live": bool(d.get("is_live")),
                               "duration": d.get("duration"),
                               "uploader": d.get("uploader")})

        if path == "/api/live/start":
            url = (body.get("url") or "").strip()
            if not url:
                return self._json({"error": "주소를 입력해 주세요"}, 400)
            return self._json(live.start(
                url, body.get("lang") or None,
                body.get("viewer_lang") or "ko",
                body.get("backend") or "local-m2m100",
                profile=body.get("profile") or "broadcast",
                asr_backend_id=body.get("asr") or "",
                refine=bool(body.get("refine", True)),
                genre=body.get("genre")))

        if path == "/api/live/capture":
            # 브라우저가 자기 탭에서 들리는 소리를 올려 주는 세션입니다.
            # 주소를 풀 것도, 받아 올 것도 없으므로 제목만 받습니다.
            return self._json(live.start(
                "", body.get("lang") or None,
                body.get("viewer_lang") or "ko",
                body.get("backend") or "local-m2m100",
                profile=body.get("profile") or "broadcast",
                asr_backend_id=body.get("asr") or "",
                refine=bool(body.get("refine", True)),
                genre=body.get("genre"),
                source="tab",
                title=(body.get("title") or "").strip() or "탭 오디오"))

        if path == "/api/live/title":
            return self._json(live.set_title(body.get("id", ""),
                                             body.get("title", "")))

        if path == "/api/live/backend":
            return self._json(live.set_backend(body.get("id", ""),
                                               body.get("backend", "")))

        if path == "/api/shutdown":
            # 라이브 세션을 먼저 제대로 닫습니다. 그래야 기록에 "종료됨"으로
            # 남습니다 -- 그냥 죽이면 `running`인 채 남아, 다음 기동의 복구
            # 스윕이 서버가 죽은 것과 똑같이 "중단됨"으로 적습니다.
            stopped = live.shutdown()
            self._json({"ok": True, "sessions_stopped": stopped})
            # 응답을 다 흘려보낸 뒤에 멈춥니다. 핸들러 안에서 곧바로
            # shutdown()을 부르면 브라우저는 답 대신 끊어진 연결을 봅니다.
            threading.Thread(target=_stop_server, daemon=True).start()
            return

        if path == "/api/live/resume":
            return self._json(live.resume(body.get("id", "")))

        if path == "/api/live/asr":
            return self._json(live.set_asr(body.get("id", ""),
                                           body.get("asr", "")))

        if path == "/api/live/stop":
            return self._json(live.stop(body.get("id", "")))

        if path == "/api/video/delete":
            return self._json(jobs.delete_video(body.get("id", ""),
                                                bool(body.get("keep_audio"))))

        if path == "/api/transcribe":
            url = (body.get("url") or "").strip()
            if not url:
                return self._json({"error": "주소를 입력해 주세요"}, 400)
            return self._json(jobs.start_transcribe(
                url, body.get("lang") or None,
                body.get("viewer_lang") or "ko",
                body.get("backend") or "local-m2m100",
                body.get("asr") or "local-hayamimi",
                bool(body.get("speakers")),
                body.get("genre")))

        if path == "/api/asr-backends":
            cfg = jobs.load_config()
            entry = {k: body.get(k, "") for k in
                     ("id", "label", "backend", "base_url", "model", "api_key")}
            entry["window_s"] = float(body.get("window_s") or 240)
            if not entry["id"]:
                return self._json({"error": "id is required"}, 400)
            cfg.setdefault("asr_backends", [])
            cfg["asr_backends"] = [b for b in cfg["asr_backends"] if b["id"] != entry["id"]]
            cfg["asr_backends"].append(entry)
            jobs.save_config(cfg)
            return self._json(cfg)

        if path == "/api/asr-backends/delete":
            bid = body.get("id", "")
            if bid == "local-hayamimi":
                return self._json({"error": "기본 로컬 전사 엔진은 삭제할 수 없습니다"})
            cfg = jobs.load_config()
            cfg["asr_backends"] = [b for b in cfg.get("asr_backends", []) if b["id"] != bid]
            jobs.save_config(cfg)
            return self._json(cfg)

        if path == "/api/backends/delete":
            return self._json(jobs.delete_backend(body.get("id", "")))

        if path == "/api/job/cancel":
            return self._json(jobs.cancel(body.get("id", "")))

        if path == "/api/backends":
            # The viewer edits an endpoint in the UI; persisting it here keeps
            # the demo usable across restarts without editing a file by hand.
            cfg = jobs.load_config()
            entry = {k: body.get(k, "") for k in
                     ("id", "label", "backend", "base_url", "model", "api_key")}
            entry["min_chars"] = int(body.get("min_chars") or 0)
            entry["no_reasoning"] = bool(body.get("no_reasoning", True))
            if not entry["id"]:
                return self._json({"error": "id is required"}, 400)
            cfg["backends"] = [b for b in cfg["backends"] if b["id"] != entry["id"]]
            cfg["backends"].append(entry)
            jobs.save_config(cfg)
            return self._json(cfg)

        self._json({"error": "not found"}, 404)

    def _serve_file(self, path: str, ctype: str):
        if not os.path.exists(path):
            return self._send(b"not found", "text/plain", 404)
        with open(path, "rb") as f:
            self._send(f.read(), ctype)

    def log_message(self, fmt, *args):
        pass  # the demo's own progress output is the interesting log


# serve_forever()를 멈추려면 서버 객체가 있어야 하는데, 핸들러는 클래스라
# 인스턴스를 알 방법이 없습니다. 모듈에 하나 둡니다.
_srv: ThreadingHTTPServer | None = None


def _stop_server():
    time.sleep(0.3)          # 응답이 소켓을 빠져나갈 틈
    if _srv is not None:
        _srv.shutdown()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8900)
    args = ap.parse_args()

    # 스키마 생성과 복구를 요청을 받기 전에 끝냅니다. 재시작 전에 돌던 작업과
    # 세션은 이어질 수 없으므로, 계속 도는 척하지 않고 중단됨으로 적습니다.
    store.init()
    stale_jobs, stale_live = jobs.restore(), live.restore()
    if stale_jobs or stale_live:
        print(f"mimiwatch: 재시작 전 작업 {stale_jobs}건, 라이브 세션 "
              f"{stale_live}건을 중단됨으로 표시했습니다", flush=True)

    global _srv
    _srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mimiwatch: http://localhost:{args.port}/")
    try:
        _srv.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C도 화면의 종료 단추와 같은 자리로 모읍니다. 어느 쪽으로 끄든
        # 세션은 "종료됨"으로 남아야 합니다.
        print("\nmimiwatch: 종료합니다", flush=True)
        live.shutdown()
    _srv.server_close()
    print("mimiwatch: 종료되었습니다", flush=True)


if __name__ == "__main__":
    main()
