"""mimiwatch 서버: 화면(web/)과 API를 냅니다.

일부러 표준 라이브러리의 HTTP 서버만 씁니다. 녹화본 흐름에는 스트리밍이
없고(재생 전에 전사가 끝납니다), 라이브 자막은 SSE 한 줄기라 프레임워크가
의존을 정당화할 만한 일이 여기 없습니다.

**라우팅은 표입니다.** 예전에는 `do_GET`/`do_POST`가 if 사슬이었고, 새 끝점을
하나 넣을 때마다 그 사슬의 어디에 끼울지(`/api/ingest/`는 JSON을 읽기 **전**에
갈라야 합니다 같은) 순서를 따져야 했습니다. 이제 정확히 맞는 경로는 사전에서,
`/api/video/<id>`처럼 뒤가 붙는 경로는 접두 목록에서 찾습니다. 쓰기 요청의
출처 검사와 JSON 읽기는 표에 들어가기 전에 한 번만 합니다.
"""
from __future__ import annotations

import json
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import export
import jobs
import live
import store
import stream
import translate

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
DATA = os.path.join(BASE, "data")

# 미디어 타입. 화면의 파일은 몇 종류뿐입니다.
CTYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    # ---- 공통 ---------------------------------------------------------------

    def _send(self, body: bytes, ctype: str, code: int = 200, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass            # 답을 기다리지 않고 떠난 브라우저. 흔하고 무해합니다.

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _query(self) -> dict[str, str]:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def _serve_file(self, path: str):
        # web/ 아래만 냅니다. normpath 뒤에도 그 안에 있는지 한 번 더 봅니다 --
        # `..`은 normpath가 접지만, 접힌 결과가 밖을 가리키면 안 됩니다.
        full = os.path.normpath(os.path.join(WEB, path))
        if not full.startswith(WEB + os.sep) and full != WEB:
            return self._send(b"not found", "text/plain", 404)
        if not os.path.isfile(full):
            return self._send(b"not found", "text/plain", 404)
        ctype = CTYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            self._send(f.read(), ctype)

    def log_message(self, fmt, *args):
        pass  # 우리 진행 로그가 볼 것이고, 요청 한 줄씩은 소음입니다

    # ---- GET ----------------------------------------------------------------

    def do_GET(self):
        path = posixpath.normpath(self.path.split("?")[0])
        exact = GET_ROUTES.get(path)
        if exact is not None:
            return exact(self)
        for prefix, handler in GET_PREFIX:
            if path.startswith(prefix):
                return handler(self, path[len(prefix):])
        self._send(b"not found", "text/plain", 404)

    def get_index(self):
        self._serve_file("index.html")

    def get_static(self, rest: str):
        # `/static/app/live.js`처럼 한 단계 아래도 냅니다. 화면을 파일 여럿으로
        # 나눈 뒤부터입니다.
        self._serve_file(rest)

    def get_videos(self):
        # 최근에 손댄 것부터. 방금 기다린 영상이 맨 위에 있어야지, id가
        # 어디에 정렬되느냐에 달릴 일이 아닙니다.
        items = []
        for vid in store.doc_ids():
            d = store.doc(vid) or {}
            items.append({k: d.get(k) for k in
                          ("id", "title", "duration", "uploader", "source_lang",
                           "viewer_lang", "translated", "audio_seconds",
                           "backends_done")} |
                         {"cues": store.cue_count(vid)})
        self._json(items)

    def get_video(self, vid: str):
        try:
            doc = jobs.load_video(os.path.basename(vid))
        except KeyError:
            return self._json({"error": "not found"}, 404)
        self._json(doc)

    def get_backends(self):
        cfg = config.load()
        cfg["live_profiles"] = [{"id": k, **v} for k, v in live.PROFILES.items()]
        # 장르는 프롬프트만 바꾸므로 라이브·녹화본 양쪽에 씁니다. 프롬프트
        # 본문은 보내지 않습니다 -- 화면에 쓸 것은 이름과 한 줄 설명뿐입니다.
        cfg["genres"] = [{"id": k, "label": v["label"], "hint": v["hint"]}
                         for k, v in translate.GENRE_PROMPTS.items()]
        self._json(cfg)

    def get_sessions(self):
        try:
            limit = max(1, min(500, int(self._query().get("limit", "50"))))
        except ValueError:
            limit = 50
        self._json(live.recent(limit))

    def get_live_status(self, sid: str):
        st = live.status_of(os.path.basename(sid))
        if st is None:
            return self._json({"error": "no such session"}, 404)
        self._json(st)

    def get_job(self, job_id: str):
        st = jobs.job_status(os.path.basename(job_id))
        if st is None:
            return self._json({"error": "no such job"}, 404)
        self._json(st)

    def get_export(self):
        q = self._query()
        value = q.get("id", "")
        fmt = q.get("fmt", "srt")
        view = q.get("view", "both")
        # 어느 엔진의 번역을 담을지. 화면이 지금 보고 있는 것을 넘깁니다.
        backend = q.get("backend", "")
        try:
            meta, rows = export.collect(value, backend)
            body, ctype = export.render(meta, rows, fmt, view)
        except KeyError:
            return self._send(b"not found", "text/plain", 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        # 제목에 한글과 일본어가 들어갑니다. filename= 은 ASCII 만 담을 수
        # 있으므로 RFC 5987 의 filename* 을 같이 보냅니다 -- 브라우저는 둘 중
        # 읽을 수 있는 쪽을 씁니다. 앞의 filename= 은 filename* 을 못 읽는
        # 도구를 위한 자리라 ASCII 여야 합니다. 세션 값을 그대로 쓰면
        # `live:xxx.srt` 가 되어 윈도우에서 만들 수 없는 이름이 됩니다.
        name = export.filename(meta, fmt)
        quoted = urllib.parse.quote(name, safe="")
        plain = "".join(c for c in (value or "mimiwatch")
                        if c.isalnum() or c in "-_") or "mimiwatch"
        self._send(body, ctype, headers={
            "Content-Disposition": f"attachment; filename=\"{plain}.{fmt}\"; "
                                   f"filename*=UTF-8''{quoted}"})

    def get_events(self, sid: str):
        sid = os.path.basename(sid)
        sess = live.get(sid)
        if sess is None and live.status_of(sid) is None:
            return self._json({"error": "no such session"}, 404)
        self._sse(sid, sess)

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

        **다시 붙는 클라이언트는 빠진 것만 받습니다.** 보내는 프레임마다
        `id:`를 붙이고, 브라우저(EventSource)나 확장이 `Last-Event-ID`를 들고
        오면 세션이 들고 있는 최근 이벤트 기록에서 그 뒤만 다시 보냅니다.
        예전에는 끊길 때마다 두 시간치 백로그를 통째로 다시 보냈습니다 --
        확장은 서버가 잠깐 멎을 때마다 3초마다 다시 붙으므로 그때마다였습니다.
        기록 밖(너무 오래 끊겼거나 서버가 재시작됨)이면 전부 다시 보냅니다.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = sess.subscribe() if sess is not None else None
        try:
            last = self.headers.get("Last-Event-ID")
            replay = sess.replay_since(last) if (sess is not None and last) else None
            if replay is not None:
                for seq, data in replay:
                    self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
            else:
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
                    seq, data = q.get(timeout=15)
                except Exception:
                    self.wfile.write(b": keepalive\n\n")   # keep proxies honest
                    self.wfile.flush()
                    continue
                self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
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

    # ---- POST ---------------------------------------------------------------

    def _same_origin_write(self) -> bool:
        """쓰기 요청이 우리 화면이나 우리 확장에서 왔는가.

        서버는 127.0.0.1에만 묶여 있지만, 그것이 곧 우리 화면만 부를 수 있다는
        뜻은 아닙니다. 사용자가 열어 둔 **아무 웹사이트**나 `fetch(..., {mode:
        "no-cors"})`로 여기에 POST를 던질 수 있고, 답은 못 읽어도 요청은
        닿습니다 -- `/api/shutdown`, `/api/video/delete`, 번역 엔진의 주소를
        남의 서버로 바꿔 자막 본문을 내보내게 하는 `/api/backends` 같은 것들.
        크롬은 공용 페이지가 사설망을 부르는 것을 막아 주지만(PNA) 파이어폭스는
        그렇지 않습니다.

        규칙은 단순합니다. `Origin`이 없으면(curl, 시험, 같은 창의 폼) 통과.
        있으면 우리 자신(Host와 같은 곳)이나 브라우저 확장만 통과. 확장의
        서비스 워커는 `chrome-extension://…` 출처로 오므로 그것이 우리
        확장인지까지는 가리지 않습니다 -- 확장 id는 설치마다 다르고, 확장을
        깐 것은 사용자 자신입니다.
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin or origin == "null":
            # `Sec-Fetch-Site`가 있으면 그것이 더 정직한 답입니다. cross-site인데
            # Origin을 비운 요청(no-cors 일부)이 여기로 옵니다.
            return (self.headers.get("Sec-Fetch-Site") or "same-origin") in (
                "same-origin", "none")
        if origin.startswith(("chrome-extension://", "moz-extension://",
                              "safari-web-extension://")):
            return True
        host = (self.headers.get("Host") or "").strip()
        try:
            o = urllib.parse.urlsplit(origin)
        except ValueError:
            return False
        return o.scheme == "http" and (o.netloc == host or o.hostname in (
            "localhost", "127.0.0.1") and (o.port or 80) == self.server.server_address[1])

    def do_POST(self):
        path = posixpath.normpath(self.path.split("?")[0])
        if not self._same_origin_write():
            return self._json({"error": "다른 출처에서 온 쓰기 요청은 받지 않습니다"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad Content-Length"}, 400)

        # 오디오만 JSON이 아닙니다. 몸통은 16kHz 모노 int16 PCM 날것입니다 --
        # base64로 감싸면 3분의 4가 되고, 초당 32KB짜리를 그럴 이유가 없습니다.
        if path.startswith("/api/ingest/"):
            raw = self.rfile.read(length) if length else b""
            return self._json(live.feed(posixpath.basename(path), raw))

        handler = POST_ROUTES.get(path)
        if handler is None:
            return self._json({"error": "not found"}, 404)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "bad json"}, 400)
        return handler(self, body)

    def post_translate(self, body):
        # 전체 번역은 「전부 고른 재번역」과 같은 길입니다. 예전에는 따로 짠
        # 루프가 자막을 통째로 다시 써서 손편집을 지웠습니다.
        vid, backend = body.get("video"), body.get("backend")
        if not vid or not backend:
            return self._json({"error": "video and backend are required"}, 400)
        self._json(jobs.start_retranslate(vid, backend, None, body.get("genre")))

    def post_probe(self, body):
        # 주소가 라이브인지 녹화본인지는 서버가 정합니다. 두 흐름은 기다리는
        # 방식부터 다르므로 화면이 시작하기 전에 알아야 합니다.
        import subprocess as sp
        try:
            out = sp.run(stream.ytdlp_cmd() + ["--no-warnings", "-j", body.get("url", "")],
                         capture_output=True, text=True,
                         timeout=stream.YTDLP_TIMEOUT_S)
        except sp.TimeoutExpired:
            # 상한이 없으면 이 요청 스레드가 영영 기다립니다. 브라우저도
            # 함께 기다리므로 「추가」 대화상자가 멈춘 것처럼 보입니다.
            return self._json({"error": f"yt-dlp가 {stream.YTDLP_TIMEOUT_S:.0f}초 "
                                        "안에 답하지 않았습니다"}, 504)
        if out.returncode != 0:
            return self._json({"error": out.stderr.strip()[:200] or "주소를 해석할 수 없습니다"}, 400)
        d = json.loads(out.stdout)
        self._json({"id": d.get("id"), "title": d.get("title"),
                    "is_live": bool(d.get("is_live")),
                    "duration": d.get("duration"),
                    "uploader": d.get("uploader")})

    def post_live_start(self, body):
        url = (body.get("url") or "").strip()
        if not url:
            return self._json({"error": "주소를 입력해 주세요"}, 400)
        self._json(live.start(
            url, body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or config.active("tr"),
            profile=body.get("profile") or "broadcast",
            asr_backend_id=body.get("asr") or config.active("asr"),
            refine=bool(body.get("refine", True)),
            genre=body.get("genre")))

    def post_live_capture(self, body):
        # 브라우저가 자기 탭에서 들리는 소리를 올려 주는 세션입니다. 주소를
        # 풀 것도, 받아 올 것도 없으므로 제목만 받습니다.
        self._json(live.start(
            "", body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or config.active("tr"),
            profile=body.get("profile") or "broadcast",
            asr_backend_id=body.get("asr") or config.active("asr"),
            refine=bool(body.get("refine", True)),
            genre=body.get("genre"),
            source="tab",
            title=(body.get("title") or "").strip() or "탭 오디오"))

    def post_retranslate(self, body):
        # 고른 자막만 다시 번역합니다. cues 를 빼면 전부입니다.
        cues = body.get("cues")
        self._json(jobs.start_retranslate(
            (body.get("id") or "").strip(), body.get("backend") or "",
            cue_ids=cues if cues else None, genre=body.get("genre")))

    def post_cue(self, body):
        # 자막 한 줄을 사람이 고칩니다. 녹화본이든 라이브든 같은 길입니다 --
        # 저장소를 하나로 모은 값이 여기서 돌아옵니다.
        owner = store.owner_of((body.get("id") or "").strip())
        cue_id = body.get("cue")
        if not owner or cue_id is None:
            return self._json({"error": "id 와 cue 가 필요합니다"}, 400)
        got = store.edit_cue(
            owner, int(cue_id),
            text=body.get("text"), tr=body.get("tr"),
            backend=body.get("backend") or "",
            start=body.get("start"), end=body.get("end"))
        if got is None:
            return self._json({"error": "no such cue"}, 404)
        live.notify_edit(owner, got, body.get("backend") or "")
        self._json({"ok": True, "cue": got})

    def post_cue_delete(self, body):
        owner = store.owner_of((body.get("id") or "").strip())
        cue_id = body.get("cue")
        if not owner or cue_id is None:
            return self._json({"error": "id 와 cue 가 필요합니다"}, 400)
        if not store.delete_cue(owner, int(cue_id)):
            return self._json({"error": "no such cue"}, 404)
        live.notify_drop(owner, int(cue_id))
        self._json({"ok": True, "deleted": int(cue_id)})

    def post_live_title(self, body):
        self._json(live.set_title(body.get("id", ""), body.get("title", "")))

    def post_live_backend(self, body):
        self._json(live.set_backend(body.get("id", ""), body.get("backend", "")))

    def post_live_asr(self, body):
        self._json(live.set_asr(body.get("id", ""), body.get("asr", "")))

    def post_live_stop(self, body):
        self._json(live.stop(body.get("id", "")))

    def post_live_resume(self, body):
        self._json(live.resume(body.get("id", "")))

    def post_live_delete(self, body):
        self._json(live.delete((body.get("id") or "").strip()))

    def post_shutdown(self, body):
        # 라이브 세션을 먼저 제대로 닫습니다. 그래야 기록에 "종료됨"으로
        # 남습니다 -- 그냥 죽이면 `running`인 채 남아, 다음 기동의 복구
        # 스윕이 서버가 죽은 것과 똑같이 "중단됨"으로 적습니다.
        stopped = live.shutdown()
        self._json({"ok": True, "sessions_stopped": stopped})
        # 응답을 다 흘려보낸 뒤에 멈춥니다. 핸들러 안에서 곧바로
        # shutdown()을 부르면 브라우저는 답 대신 끊어진 연결을 봅니다.
        threading.Thread(target=_stop_server, daemon=True).start()

    def post_video_delete(self, body):
        self._json(jobs.delete_video(body.get("id", ""), bool(body.get("keep_audio"))))

    def post_transcribe(self, body):
        url = (body.get("url") or "").strip()
        if not url:
            return self._json({"error": "주소를 입력해 주세요"}, 400)
        self._json(jobs.start_transcribe(
            url, body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or "",
            body.get("asr") or "",
            bool(body.get("speakers")),
            body.get("genre")))

    def post_asr_backends(self, body):
        entry = {k: body.get(k, "") for k in
                 ("id", "label", "backend", "base_url", "model", "api_key")}
        entry["window_s"] = float(body.get("window_s") or 240)
        if not entry["id"]:
            return self._json({"error": "id is required"}, 400)
        self._json(config.upsert("asr", entry))

    def post_backends(self, body):
        # 화면에서 고친 엔드포인트를 여기서 남겨 두면 재시작해도 파일을 손으로
        # 고칠 일이 없습니다.
        entry = {k: body.get(k, "") for k in
                 ("id", "label", "backend", "base_url", "model", "api_key")}
        entry["min_chars"] = int(body.get("min_chars") or 0)
        entry["no_reasoning"] = bool(body.get("no_reasoning", True))
        if not entry["id"]:
            return self._json({"error": "id is required"}, 400)
        self._json(config.upsert("tr", entry))

    def post_asr_backends_delete(self, body):
        self._json(jobs.delete_asr_backend(body.get("id", "")))

    def post_backends_delete(self, body):
        self._json(jobs.delete_backend(body.get("id", "")))

    def post_job_cancel(self, body):
        self._json(jobs.cancel(body.get("id", "")))


GET_ROUTES = {
    "/": Handler.get_index,
    "/api/videos": Handler.get_videos,
    "/api/backends": Handler.get_backends,
    "/api/live/sessions": Handler.get_sessions,
    "/api/export": Handler.get_export,
}
# 뒤가 붙는 경로. 긴 접두가 먼저여야 `/api/video/`가 `/api/videos`를 삼키지
# 않습니다 -- 정확한 경로는 위 사전에서 먼저 찾으므로 여기서는 순서만 지킵니다.
GET_PREFIX = [
    ("/api/video/", Handler.get_video),
    ("/api/live/events/", Handler.get_events),
    ("/api/live/status/", Handler.get_live_status),
    ("/api/job/", Handler.get_job),
    ("/static/", Handler.get_static),
]
POST_ROUTES = {
    "/api/translate": Handler.post_translate,
    "/api/retranslate": Handler.post_retranslate,
    "/api/probe": Handler.post_probe,
    "/api/transcribe": Handler.post_transcribe,
    "/api/live/start": Handler.post_live_start,
    "/api/live/capture": Handler.post_live_capture,
    "/api/live/title": Handler.post_live_title,
    "/api/live/backend": Handler.post_live_backend,
    "/api/live/asr": Handler.post_live_asr,
    "/api/live/stop": Handler.post_live_stop,
    "/api/live/resume": Handler.post_live_resume,
    "/api/live/delete": Handler.post_live_delete,
    "/api/cue": Handler.post_cue,
    "/api/cue/delete": Handler.post_cue_delete,
    "/api/video/delete": Handler.post_video_delete,
    "/api/backends": Handler.post_backends,
    "/api/backends/delete": Handler.post_backends_delete,
    "/api/asr-backends": Handler.post_asr_backends,
    "/api/asr-backends/delete": Handler.post_asr_backends_delete,
    "/api/job/cancel": Handler.post_job_cancel,
    "/api/shutdown": Handler.post_shutdown,
}


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
    # 예전 판이 남긴 `data/<영상id>.json` 을 표로 옮깁니다. 옮길 것이 없으면
    # 아무것도 하지 않으므로 매번 불러도 됩니다. 원본은 지우지 않고
    # data/legacy/ 로 옮깁니다.
    store.import_legacy_docs()
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
