"""mimiwatch demo server: serves the player UI and the transcribed cue files.

Deliberately a plain stdlib HTTP server. The recorded-video flow has no
streaming component -- transcription finishes before playback starts -- so
there is nothing here that a framework would earn its dependency on.
"""
from __future__ import annotations

import json
import os
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jobs
import live
import store

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

        if path.startswith("/static/"):
            target = os.path.join(WEB, os.path.basename(path))
            ctype = ("text/css; charset=utf-8" if target.endswith(".css")
                     else "text/javascript; charset=utf-8")
            return self._serve_file(target, ctype)

        self._send(b"not found", "text/plain", 404)

    def _sse(self, sid, sess):
        """Stream a live session's cues. Chosen over the WebSocket mirror in
        ws_ingest.py, which dropped events under the same load.

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
        except (BrokenPipeError, ConnectionResetError):
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
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)

        if path == "/api/translate":
            vid, backend = body.get("video"), body.get("backend")
            if not vid or not backend:
                return self._json({"error": "video and backend are required"}, 400)
            return self._json(jobs.start(vid, backend))

        if path == "/api/probe":
            # The UI needs to know which flow a URL belongs to before it
            # commits: a live broadcast and a finished video are different
            # pipelines with different waiting behaviour.
            import subprocess as sp
            out = sp.run(["yt-dlp", "--no-warnings", "-j", body.get("url", "")],
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
            return self._json(live.start(url, body.get("lang") or None,
                                         body.get("viewer_lang") or "ko",
                                         body.get("backend") or "local-m2m100",
                                         body.get("profile") or "broadcast",
                                         body.get("asr") or ""))

        if path == "/api/live/backend":
            return self._json(live.set_backend(body.get("id", ""),
                                               body.get("backend", "")))

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
                bool(body.get("speakers"))))

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

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mimiwatch: http://localhost:{args.port}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()
