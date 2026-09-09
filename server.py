"""The mimiwatch server: it serves the screen (web/) and the API.

Deliberately nothing but the standard library's HTTP server. The VOD flow has
no streaming (transcription finishes before playback) and live subtitles are a
single SSE stream, so there is nothing here that would justify a framework
dependency.

**Routing is a table.** `do_GET`/`do_POST` used to be if-chains, and every time
a new endpoint went in, where in the chain it belonged had to be worked out
(`/api/ingest/` has to branch off **before** the JSON is read, for one). Now an
exactly matching path is found in a dict, and a path with a tail like
`/api/video/<id>` in a prefix list. The origin check on write requests and
reading the JSON happen once, before entering the table.
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bus
import config
import export
import jobs
import live
import modelhub
import paths
import store
import stream
import translate
import update as mw_update

# The screen's files travel with the program -- inside the repo, or inside
# the PyInstaller bundle.
BASE = paths.BASE
WEB = os.path.join(BASE, "web")

# Placeholder for the API key sent to the screen. The real key never leaves
# backends.json.
KEY_MASK = "••••••••"
# The lifetime of one SSE connection. Kept shorter than the MV3 service
# worker's "5 minutes per request" rule. Tests cut it down to a few seconds with
# the environment variable to see that the socket really closes after a rotation.
SSE_ROTATE_S = float(os.environ.get("MIMIWATCH_SSE_ROTATE_S") or 270.0)
# Upper bound on a JSON body. Editing one subtitle line and the engine settings
# is all there is, so 1MB is plenty. Audio (ingest) goes its own way.
JSON_MAX = 1 << 20

# Media types. The screen has only a few kinds of file.
CTYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    # ---- Common -------------------------------------------------------------

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
            pass            # The browser left without waiting for the answer. Common, harmless.

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _query(self) -> dict[str, str]:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def _serve_file(self, path: str):
        # Only what is under web/ is served. After normpath it is checked once
        # more that the result is still inside -- normpath folds `..` away, but
        # the folded result must not point outside.
        full = os.path.normpath(os.path.join(WEB, path))
        if not full.startswith(WEB + os.sep) and full != WEB:
            return self._send(b"not found", "text/plain", 404)
        if not os.path.isfile(full):
            return self._send(b"not found", "text/plain", 404)
        ctype = CTYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            self._send(f.read(), ctype)

    def log_message(self, fmt, *args):
        pass  # Our own progress log is what gets read; a line per request is noise

    def handle(self):
        # Chrome also drops a socket it opened ahead of time without ever
        # sending a request. The ConnectionResetError that comes of it is
        # normal, not something to leave a stack trace in the log for.
        try:
            super().handle()
        except ConnectionError:
            pass

    # ---- GET ----------------------------------------------------------------

    def _host_ok(self) -> bool:
        """Is `Host` us. Applied to every request.

        The server is bound to 127.0.0.1 only, but DNS rebinding walks around
        that restriction: make an attacker's domain point at 127.0.0.1 for a
        moment and that page's script sends a GET here as the **same origin**
        and reads the answer -- `/api/backends` holds the API keys. The Origin
        check stops writes, but reads were wide open. A browser sends Host as
        the domain that was asked for, so if that is not localhost or
        127.0.0.1 it is not our screen.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True                   # HTTP/1.0 tools (an old curl). Browsers always send it
        name, _, port = host.rpartition(":") if host.count(":") == 1 else (host, "", "")
        if host.startswith("["):          # [::1]:8900
            name, _, port = host.partition("]")
            name += "]"; port = port.lstrip(":")
        if name not in ("localhost", "127.0.0.1", "[::1]"):
            return False
        return not port or port == str(self.server.server_address[1])

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "This server can only be called from localhost"}, 421)
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
        # One level down like `/static/app/live.js` is served too. Ever since
        # the screen was split into several files.
        self._serve_file(rest)

    def get_videos(self):
        # Most recently touched first. The video just waited on should be at
        # the top; it should not depend on where its id happens to sort.
        items = []
        for vid in store.doc_ids():
            d = store.doc(vid) or {}
            items.append({k: d.get(k) for k in
                          ("id", "title", "duration", "uploader", "source_lang",
                           "viewer_lang", "translated", "audio_seconds",
                           "backends_done", "url", "source")} |
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
        # There is no reason to send API keys to the screen. Only the mark that
        # one exists is left and the value is masked -- if the edit form sends
        # the masked value straight back, upsert keeps the stored key
        # (post_backends).
        for key, _ in config.KINDS.values():
            cfg[key] = [{**b, "api_key": KEY_MASK if b.get("api_key") else ""}
                        for b in cfg.get(key, [])]
        cfg["live_profiles"] = [{"id": k, **v} for k, v in live.PROFILES.items()]
        # A genre changes nothing but the prompt, so it is used for both live
        # and VOD. The prompt body is not sent -- all the screen needs is the
        # name and the one-line description.
        cfg["genres"] = [{"id": k, "label_en": v["label_en"], "label_ko": v["label_ko"],
                          "hint_en": v["hint_en"], "hint_ko": v["hint_ko"]}
                         for k, v in translate.GENRE_PROMPTS.items()]
        # The remembered translation target. It is added explicitly rather than
        # left to ride the file because a config written before this setting
        # exists has no key, and the select still has to seat on a value -- the
        # getter falls back to the default, so the answer is always total.
        cfg["viewer_lang"] = config.viewer_lang()
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
        # Which engine's translation to put in. The screen passes what it is
        # looking at right now.
        backend = q.get("backend", "")
        try:
            meta, rows = export.collect(value, backend)
            body, ctype = export.render(meta, rows, fmt, view)
        except KeyError:
            return self._send(b"not found", "text/plain", 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        # Titles carry Korean and Japanese. filename= can only hold ASCII, so
        # RFC 5987's filename* goes along with it -- the browser uses whichever
        # of the two it can read. The leading filename= is the slot for tools
        # that cannot read filename*, so it has to be ASCII. Using the session
        # value as it is gives `live:xxx.srt`, a name Windows cannot create.
        name = export.filename(meta, fmt)
        quoted = urllib.parse.quote(name, safe="")
        plain = "".join(c for c in (value or "mimiwatch")
                        if c.isalnum() or c in "-_") or "mimiwatch"
        self._send(body, ctype, headers={
            "Content-Disposition": f"attachment; filename=\"{plain}.{fmt}\"; "
                                   f"filename*=UTF-8''{quoted}"})

    def get_models(self):
        # The list and state of the models and tools. The first-run screen
        # learns from this what is missing.
        self._json(modelhub.overview())

    # ---- Local files ---------------------------------------------------------
    # Local video and audio are transcription targets too. The original is not
    # moved, it is read where it lies, and for playback this endpoint hands the
    # original out as it is for <video> to play. Why Range is accepted: a
    # browser gives up seeking on media without Range -- and pressing a
    # subtitle line to go to that point is this screen's basic move.

    def get_media(self, vid: str):
        d = store.doc(os.path.basename(vid))
        path = (d or {}).get("media_path") or ""
        if not path or not os.path.isfile(path):
            return self._send(b"not found", "text/plain", 404)
        size = os.path.getsize(path)
        start, end = parse_range(self.headers.get("Range"), size)
        if start is None and self.headers.get("Range"):
            return self._send(b"bad range", "text/plain", 416,
                              {"Content-Range": f"bytes */{size}"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        partial = start is not None
        lo, hi = (start, end) if partial else (0, size - 1)
        length = hi - lo + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(lo)
                left = length
                while left > 0:
                    buf = f.read(min(1 << 16, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except ConnectionError:
            pass                    # On every seek the browser drops the previous request. Normal.

    def post_upload(self, length: int):
        """Take in an original picked with the file chooser. The body is the
        file as it is -- neither multipart nor base64. The name comes in the
        query.

        A file whose path is known does not need this route (the path goes
        straight into the address box). With the chooser the browser does not
        tell us the path, so a copy is unavoidable. What comes in stays in
        data/uploads/ -- the same rule as ever: deleting a transcription does
        not delete the original.
        """
        if length <= 0:
            return self._json({"error": "The file is empty"}, 400)
        name = os.path.basename(self._query().get("name") or "upload")
        # The extension is ffmpeg's hint so it is kept; the rest is reduced to
        # characters the filesystem is safe with.
        stem, ext = os.path.splitext(name)
        stem = re.sub(r"[^\w.\-\u00a0-\uffff]+", "_", stem).strip("._") or "upload"
        ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:8]
        updir = os.path.join(store.DATA, "uploads")
        os.makedirs(updir, exist_ok=True)
        dest = os.path.join(updir, stem + ext)
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(updir, f"{stem}-{n}{ext}")
        try:
            with open(dest + ".part", "wb") as f:
                left = length
                while left > 0:
                    buf = self.rfile.read(min(1 << 20, left))
                    if not buf:
                        raise ConnectionError("The upload was cut off partway")
                    f.write(buf)
                    left -= len(buf)
            os.replace(dest + ".part", dest)
        except (ConnectionError, OSError) as exc:
            try:
                os.remove(dest + ".part")
            except OSError:
                pass
            return self._json({"error": str(exc)[:200]}, 400)
        self._json({"path": dest, "name": os.path.basename(dest)})

    # ---- YouTube cookies -----------------------------------------------------
    # The extension reads the browser's login cookies and hands them over (only
    # when the user presses it each time). It is the only way to take a
    # members-only stream in "by address". Because they are the keys to an
    # account: the file is 0600, the contents are written down nowhere, only
    # present/absent and the time they arrived show on the screen, and they can
    # be deleted at any time.

    def get_cookies(self):
        self._json(_cookies_status())

    def post_cookies_youtube(self, body):
        text = body.get("cookies")
        if not isinstance(text, str) or "\t" not in text:
            return self._json({"error": "Cookie text in Netscape format is required"}, 400)
        lines = [ln for ln in text.splitlines() if _is_cookie_line(ln)]
        if not lines:
            return self._json({"error": "The cookies are empty -- are you logged in to YouTube?"},
                              400)
        path = paths.cookies_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n"
                    "# YouTube login cookies handed over by the mimiwatch extension.\n"
                    "# They are the key to the account.\n")
            f.write(text if text.endswith("\n") else text + "\n")
        os.replace(path + ".tmp", path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass                        # Windows has no modes
        stream.reset_tool_cache()       # --cookies goes on from the next yt-dlp call
        print(f"[cookies] took in {len(lines)} YouTube cookies", file=sys.stderr, flush=True)
        self._json(_cookies_status())

    def post_cookies_delete(self, body):
        path = paths.cookies_path()
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        stream.reset_tool_cache()
        self._json(_cookies_status())

    def get_setup(self):
        # The choices on the initial setup screen: which models each engine
        # needs, their size and whether they are there.
        self._json(modelhub.setup_options())

    def get_bus(self):
        """The global change feed (bus.py) over SSE. The screen keeps one
        attached and fixes up the lists and the job box as they change. The
        first frame is the mark that it is attached."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = bus.subscribe()
        try:
            self.wfile.write(b'data: {"type": "hello"}\n\n')
            self.wfile.flush()
            started = time.time()
            while True:
                # Wait only as long as is left until the rotation time. It used
                # to wake in 15 second steps only, so a rotation was up to
                # 15 seconds late.
                left = SSE_ROTATE_S - (time.time() - started)
                if left <= 0:
                    self.wfile.write(b'data: {"type": "rotate"}\n\n')
                    self.wfile.flush()
                    # The socket has to be **really** closed here. The SSE
                    # header's `Connection: keep-alive` puts close_connection
                    # back to False, so on simply returning, handle() waits for
                    # the next request on the same socket while the browser
                    # waits for more body -- with neither an onerror nor a
                    # reconnect, subtitles quietly stopped every 4.5 minutes.
                    self.close_connection = True
                    return
                try:
                    data = q.get(timeout=min(15, left))
                except Exception:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
        except ConnectionError:
            pass
        finally:
            bus.unsubscribe(q)

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

        **A client that reattaches gets only what it missed.** Every frame
        that goes out carries an `id:`, and when the browser (EventSource) or
        the extension arrives holding a `Last-Event-ID`, only what follows it
        in the recent event record the session keeps is sent again. Previously
        every disconnect resent two hours of backlog whole -- and the extension
        reattaches every 3 seconds whenever the server stalls for a moment, so
        it was on every one of those. Outside the record (disconnected too
        long, or the server restarted) everything is sent again.
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
                # The first frame had no type, so the browser was letting it
                # slide past. For a session cut off by a restart this one frame
                # is the only chance to say "why it stopped", so it is shaped
                # like the rest of the status notifications.
                status = sess.status() if sess is not None else live.status_of(sid)
                for event in [{"type": "status", **status}, *live.backlog(sid)]:
                    self.wfile.write(
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            if q is None:
                # A finished session has sent everything it had to send. The
                # response has no Content-Length, so unless the socket is
                # closed the client cannot tell it is over -- the keep-alive
                # header above keeps that socket alive, so it is undone here.
                self.close_connection = True
                return
            started = time.time()
            while True:
                # Wait only as long as is left until the rotation time. It used
                # to wake in 15 second steps only, so a rotation was up to
                # 15 seconds late.
                left = SSE_ROTATE_S - (time.time() - started)
                if left <= 0:
                    # The stream is cut on purpose. Chrome takes the
                    # extension's service worker down once a single request
                    # goes past 5 minutes, so we close before that and let the
                    # client reattach right away with Last-Event-ID. The screen
                    # (EventSource) reattaches by the same rule.
                    self.wfile.write(b'data: {"type": "rotate"}\n\n')
                    self.wfile.flush()
                    # The socket has to be **really** closed here. The SSE
                    # header's `Connection: keep-alive` puts close_connection
                    # back to False, so on simply returning, handle() waits for
                    # the next request on the same socket while the browser
                    # waits for more body -- with neither an onerror nor a
                    # reconnect, subtitles quietly stopped every 4.5 minutes.
                    self.close_connection = True
                    return
                try:
                    seq, data = q.get(timeout=min(15, left))
                except Exception:
                    self.wfile.write(b": keepalive\n\n")   # keep proxies honest
                    self.wfile.flush()
                    continue
                self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
                self.wfile.flush()
        except ConnectionError:
            # The normal path, which ends when a viewer closes the tab. Each
            # operating system raises a different exception -- BrokenPipe or
            # ConnectionReset on Linux and macOS, ConnectionAborted
            # (WinError 10053) on Windows. All three sit under ConnectionError,
            # so the parent is what is caught.
            pass
        finally:
            if q is not None:
                sess.unsubscribe(q)

    # ---- POST ---------------------------------------------------------------

    def _same_origin_write(self) -> bool:
        """Did the write request come from our screen or our extension.

        The server is bound to 127.0.0.1 only, but that does not mean our
        screen is the only thing that can call it. **Any website** the user has
        open can throw a POST here with `fetch(..., {mode: "no-cors"})`, and
        even though it cannot read the answer the request lands --
        `/api/shutdown`, `/api/video/delete`, or `/api/backends`, which points
        the translation engine's address at someone else's server and has the
        subtitle text sent out. Chrome stops a public page from calling a
        private network (PNA); Firefox does not.

        The rule is simple. No `Origin` (curl, the tests, a form in the same
        window) passes. With one, only ourselves (the same place as Host) or a
        browser extension passes. The extension's service worker arrives with a
        `chrome-extension://…` origin, so it is not told apart down to whether
        it is our extension -- an extension id differs per installation, and it
        was the user who installed it.
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin or origin == "null":
            # If `Sec-Fetch-Site` is there it is the more honest answer.
            # Requests that are cross-site but leave Origin empty (some no-cors
            # ones) come through here.
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
        if not self._host_ok():
            return self._json({"error": "This server can only be called from localhost"}, 421)
        path = posixpath.normpath(self.path.split("?")[0])
        if not self._same_origin_write():
            return self._json({"error": "A write request from another origin is not accepted"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad Content-Length"}, 400)

        # Audio alone is not JSON. The body is raw 16kHz mono int16 PCM --
        # wrapping it in base64 makes it four thirds, and there is no reason to
        # do that to something that runs at 32KB a second.
        if path.startswith("/api/ingest/"):
            raw = self.rfile.read(length) if length else b""
            return self._json(live.feed(posixpath.basename(path), raw))

        # An upload is not JSON either. The body is the media file as it is, so
        # its upper bound is different too.
        if path == "/api/upload":
            return self.post_upload(length)

        handler = POST_ROUTES.get(path)
        if handler is None:
            return self._json({"error": "not found"}, 404)
        if length > JSON_MAX:
            return self._json({"error": "The request is too large"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "bad json"}, 400)
        return handler(self, body)

    def post_translate(self, body):
        # Translating everything is the same route as "retranslate with all
        # selected". A separately written loop used to rewrite the subtitles
        # whole and wipe out the hand edits.
        vid, backend = body.get("video"), body.get("backend")
        if not vid or not backend:
            return self._json({"error": "video and backend are required"}, 400)
        self._json(jobs.start_retranslate(vid, backend, None, body.get("genre")))

    def post_probe(self, body):
        # Whether an address is live or a VOD is decided by the server. The
        # two flows differ from the way they are waited on onward, so the
        # screen has to know before it starts.
        import subprocess as sp

        import transcribe_vod as vod
        url = (body.get("url") or "").strip()
        # A local file path does not go through yt-dlp. If the pasted path is
        # not there or cannot be read, that is said right here.
        if vod.is_local_source(url):
            try:
                meta = vod.probe_local(url)
            except vod.VodError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({**meta, "site": "file", "channel": ""})
        try:
            out = sp.run(stream.ytdlp_args("-j", url=url),
                         capture_output=True, text=True,
                         timeout=stream.YTDLP_TIMEOUT_S)
        except sp.TimeoutExpired:
            # Without an upper bound this request thread waits forever. The
            # browser waits along with it, so the "Add" dialog looks stuck.
            return self._json({"error": f"yt-dlp did not answer within "
                                        f"{stream.YTDLP_TIMEOUT_S:.0f} seconds"}, 504)
        if out.returncode != 0:
            return self._json({"error": out.stderr.strip()[:200]
                                        or "Could not resolve the URL"}, 400)
        try:
            d = json.loads(out.stdout)
        except json.JSONDecodeError:
            # A playlist or channel address prints one line per video. Point
            # at a single video.
            return self._json({"error": "Give the URL of a single video "
                                        "(not a playlist or channel URL)"}, 400)
        info = live.site_of(d, url)
        is_live = d.get("is_live")
        if is_live is None and info["site"] == "other" and live.looks_like_m3u8(url):
            # For a bare m3u8 the generic extractor does not know whether it
            # is live. This input is live's fallback route (R1.2), so when it
            # is unknown it is taken as live.
            is_live = True
        self._json({"id": d.get("id"), "title": d.get("title"),
                    "is_live": bool(is_live),
                    "duration": d.get("duration"),
                    "uploader": d.get("uploader"), **info})

    def post_live_start(self, body):
        url = (body.get("url") or "").strip()
        if not url:
            return self._json({"error": "Enter a URL"}, 400)
        self._json(live.start(
            url, body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or config.active("tr"),
            profile=body.get("profile") or "broadcast",
            asr_backend_id=body.get("asr") or config.active("asr"),
            refine=bool(body.get("refine", True)),
            genre=body.get("genre")))

    def post_live_capture(self, body):
        # A session where the browser uploads the sound heard in its own tab.
        # There is no address to resolve and nothing to fetch, so only the
        # title is taken.
        self._json(live.start(
            "", body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or config.active("tr"),
            profile=body.get("profile") or "broadcast",
            asr_backend_id=body.get("asr") or config.active("asr"),
            refine=bool(body.get("refine", True)),
            genre=body.get("genre"),
            source="tab",
            title=(body.get("title") or "").strip() or "Tab audio"))

    # ---- Multiview -----------------------------------------------------------
    # Several streams on one screen. The rules for groups and focus are in
    # live.py's multiview section; here the JSON is only unpacked and passed
    # on. The live start arguments are the same as /api/live/start.

    def _live_args(self, body) -> dict:
        return dict(lang=body.get("lang") or None,
                    viewer_lang=body.get("viewer_lang") or "ko",
                    backend_id=body.get("backend") or config.active("tr"),
                    profile=body.get("profile") or "broadcast",
                    asr_backend_id=body.get("asr") or config.active("asr"),
                    refine=bool(body.get("refine", True)),
                    genre=body.get("genre"))

    def post_multiview(self, body):
        """Make a group. `sessions` is the ids of sessions being received now
        (they are folded in), `sources` is new sources (`{url}` or
        `{source:"tab", title}`). 1~MULTIVIEW_MAX of them together."""
        sources = [{"session": sid} for sid in (body.get("sessions") or []) if sid]
        extra = body.get("sources") or []
        if not isinstance(extra, list) or not all(isinstance(s, dict) for s in extra):
            return self._json({"error": "sources has to be a list of objects"}, 400)
        sources += extra
        if not sources:
            return self._json({"error": "There are no sources"}, 400)
        if len(sources) > live.MULTIVIEW_MAX:
            return self._json({"error": f"Multiview holds at most {live.MULTIVIEW_MAX}"}, 400)
        res = live.multiview_start(sources, focus=body.get("focus") or None,
                                   **self._live_args(body))
        self._json(res, 400 if "error" in res else 200)

    def post_multiview_focus(self, body):
        self._json(live.multiview_focus(body.get("group") or "", body.get("id") or ""))

    def post_multiview_add(self, body):
        src = {k: body[k] for k in ("url", "source", "title", "session") if body.get(k)}
        self._json(live.multiview_add(body.get("group") or "", src, **self._live_args(body)))

    def post_multiview_remove(self, body):
        self._json(live.multiview_remove(body.get("group") or "", body.get("id") or ""))

    def post_multiview_stop(self, body):
        self._json(live.multiview_stop(body.get("group") or ""))

    def get_multiview(self, gid: str):
        st = live.multiview_status(os.path.basename(gid))
        if st is None:
            return self._json({"error": "no such group"}, 404)
        self._json(st)

    def post_retranslate(self, body):
        # Retranslate only the selected subtitles. Leave cues out and it is
        # all of them.
        cues = body.get("cues")
        self._json(jobs.start_retranslate(
            (body.get("id") or "").strip(), body.get("backend") or "",
            cue_ids=cues if cues else None, genre=body.get("genre")))

    def post_cue(self, body):
        # A person fixes one subtitle line. VOD or live, it is the same route
        # -- what gathering the storage into one bought comes back here.
        owner = store.owner_of((body.get("id") or "").strip())
        cue_id = body.get("cue")
        if not owner or cue_id is None:
            return self._json({"error": "id and cue are required"}, 400)
        got = store.edit_cue(
            owner, int(cue_id),
            text=body.get("text"), tr=body.get("tr"),
            backend=body.get("backend") or "",
            start=body.get("start"), end=body.get("end"))
        if got is None:
            return self._json({"error": "no such cue"}, 404)
        live.notify_edit(owner, got, body.get("backend") or "")
        self._json({"ok": True, "cue": got})

    def post_cue_add(self, body):
        # A person writes a new subtitle line in. The time and the source text
        # are required, the translation is optional. The number is made by the
        # storage (insert_cue).
        owner = store.owner_of((body.get("id") or "").strip())
        text = (body.get("text") or "").strip()
        try:
            start = max(0.0, float(body.get("start")))
        except (TypeError, ValueError):
            return self._json({"error": "start (seconds) is required"}, 400)
        if not owner or not text:
            return self._json({"error": "id and text are required"}, 400)
        meta = store.doc(owner) or store.session(owner)
        if not meta:
            return self._json({"error": "no such video or session"}, 404)
        lang = ((body.get("lang") or meta.get("source_lang")
                 or meta.get("lang") or "")).strip()
        got = store.insert_cue(owner, start, text, lang=lang,
                               tr=(body.get("tr") or "").strip(),
                               backend=body.get("backend") or "")
        # If the session is being received it reaches the script panel and the
        # other windows too. The same channel as an edit -- the browser finds
        # lines by id, so a new id becomes a new line.
        live.notify_edit(owner, got, body.get("backend") or "")
        self._json({"ok": True, "cue": got})

    def post_cue_delete(self, body):
        owner = store.owner_of((body.get("id") or "").strip())
        cue_id = body.get("cue")
        if not owner or cue_id is None:
            return self._json({"error": "id and cue are required"}, 400)
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
        # The engine can be changed on resume. It used to take the stored
        # session's engine as it was, so changing it in "Manage" and then
        # resuming still ran on the old engine.
        self._json(live.resume(body.get("id", ""), asr_backend_id=body.get("asr") or "",
                               backend_id=body.get("backend") or "",
                               source=body.get("source") or "", url=body.get("url") or ""))

    def post_active(self, body):
        """Change the default transcription and translation engines. Called by
        the screen's "Manage" picker.

        That choice used to stay in the browser (localStorage) only. The
        extension reads the server's `active` to start a session, so whatever
        was picked on the screen, the extension always started on the default
        (light) engines. Now the picker is the server's default.
        """
        kind = body.get("kind")
        if kind not in config.KINDS:
            return self._json({"error": "kind is asr or tr"}, 400)
        got = config.set_active(kind, str(body.get("id") or ""))
        if "error" in got:
            return self._json(got, 400)
        self.get_backends()

    def post_live_delete(self, body):
        self._json(live.delete((body.get("id") or "").strip()))

    def post_shutdown(self, body):
        # Live sessions are closed properly first. That is what leaves them
        # in the record as "ended" -- just killing the process leaves them
        # `running`, and the next start's recovery sweep writes them as
        # "interrupted", exactly as if the server had died.
        stopped = _wind_down()
        self._json({"ok": True, "sessions_stopped": stopped})
        # It stops after the response has been flushed all the way out.
        # Calling shutdown() straight away inside the handler makes the browser
        # see a broken connection instead of an answer.
        threading.Thread(target=_stop_server, daemon=True).start()

    def post_video_delete(self, body):
        self._json(jobs.delete_video(body.get("id", ""), bool(body.get("keep_audio"))))

    def get_glossaries(self):
        self._json(store.all_glossaries())

    def get_search(self):
        q = (self._query().get("q") or "").strip()
        results = []
        for r in store.search(q) if q else []:
            kind = store.owner_kind(r["owner"])
            results.append({
                "value": ("live:" + r["owner"]) if kind == "live" else r["owner"],
                "title": store.owner_title(r["owner"]),
                "start": r["start"], "text": r["text"],
                "snip": r["snip"], "snip_tr": r["snip_tr"],
            })
        self._json({"q": q, "results": results})

    def post_burn(self, body):
        value = (body.get("value") or "").strip()
        if not value:
            return self._json({"error": "No video"}, 400)
        if value.startswith("live:"):
            return self._json(
                {"error": "Burn-in works on a finished VOD, not on a live session"},
                400)
        res = jobs.start_burn(value, body.get("view") or "both")
        if res.get("error"):
            return self._json({"error": res["error"]}, 400)
        self._json(res)

    def post_glossaries(self, body):
        out = store.save_glossary(body.get("channel_key") or "",
                                  body.get("name") or "",
                                  body.get("terms") or [])
        # The same channel as an edit -- the browser finds the open screens on it.
        bus.publish({"type": "glossary", "id": body.get("channel_key") or "",
                     "reason": "saved" if out is not None else "deleted"})
        self._json(out if out is not None
                   else {"channel_key": body.get("channel_key") or ""})

    def post_transcribe(self, body):
        url = (body.get("url") or "").strip()
        if not url:
            return self._json({"error": "Enter a URL"}, 400)
        self._json(jobs.start_transcribe(
            url, body.get("lang") or None,
            body.get("viewer_lang") or "ko",
            body.get("backend") or "",
            body.get("asr") or "",
            bool(body.get("speakers")),
            body.get("genre"),
            bool(body.get("refine", True)),
            body.get("site") or "",
            body.get("channel") or "",
            body.get("channel_name") or ""))

    def _keep_masked_key(self, kind: str, entry: dict) -> dict:
        """If the screen sent the masked key (KEY_MASK) straight back, the
        stored key is kept. An empty string means "delete the key" and is left
        as it is."""
        if entry.get("api_key") == KEY_MASK:
            old = config.find(kind, entry["id"]) or {}
            entry["api_key"] = old.get("api_key", "")
        return entry

    def post_asr_backends(self, body):
        entry = {k: body.get(k, "") for k in
                 ("id", "label", "backend", "base_url", "model", "api_key")}
        entry["window_s"] = float(body.get("window_s") or 240)
        if not entry["id"]:
            return self._json({"error": "id is required"}, 400)
        config.upsert("asr", self._keep_masked_key("asr", entry))
        self.get_backends()

    def post_backends(self, body):
        # Leaving the endpoint edited on the screen here means there is no
        # fixing the file by hand across a restart.
        entry = {k: body.get(k, "") for k in
                 ("id", "label", "backend", "base_url", "model", "api_key")}
        entry["min_chars"] = int(body.get("min_chars") or 0)
        entry["no_reasoning"] = bool(body.get("no_reasoning", True))
        if not entry["id"]:
            return self._json({"error": "id is required"}, 400)
        config.upsert("tr", self._keep_masked_key("tr", entry))
        self.get_backends()

    def post_asr_backends_delete(self, body):
        self._json(jobs.delete_asr_backend(body.get("id", "")))

    def post_backends_delete(self, body):
        self._json(jobs.delete_backend(body.get("id", "")))

    def post_job_cancel(self, body):
        self._json(jobs.cancel(body.get("id", "")))

    # ---- Models and tools ----------------------------------------------------
    # Downloads run on a background thread and the progress is pushed out over
    # /api/events.

    def post_models_download(self, body):
        ids = body.get("ids")
        if isinstance(ids, str):
            ids = modelhub.default_ids() if ids == "default" else [ids]
        if not isinstance(ids, list) or not ids:
            return self._json({"error": "ids is required"}, 400)
        self._json(modelhub.download([str(i) for i in ids], body.get("token") or None))

    def post_models_cancel(self, body):
        self._json(modelhub.cancel(str(body.get("id") or "")))

    def post_models_delete(self, body):
        self._json(modelhub.delete(str(body.get("id") or "")))

    def post_uilang(self, body):
        # Which language the UI draws itself in. It lives in backends.json rather
        # than in the browser because the extension reads it from the server too,
        # and because a machine's language is a property of the machine, not of
        # whichever browser profile opened the page first.
        self._json(config.set_ui_lang(str(body.get("lang") or "")))

    def post_viewerlang(self, body):
        # Which language the subtitles are translated into. Like ui_lang it
        # lives in backends.json, so the web page and the extension remember
        # one value instead of each holding its own copy -- the last choice
        # follows the user from one surface to the other.
        got = config.set_viewer_lang(str(body.get("lang") or ""))
        if "error" in got:
            return self._json(got, 400)
        self._json(got)

    def post_setup(self, body):
        # Settle the two default engines and start fetching what that
        # combination needs. One default cannot fit machines of every spec, so
        # the choice is made on the first run.
        self._json(modelhub.apply_setup(str(body.get("asr") or ""), str(body.get("tr") or ""),
                                        start_download=bool(body.get("download", True))))

    def post_models_add(self, body):
        self._json(modelhub.add_custom(
            body.get("kind", ""), body.get("repo", ""), body.get("file", ""),
            label=body.get("label", ""), engine_id=body.get("id", ""),
            device=body.get("device") or "auto", token=body.get("token") or None))

    # ---- Updates -------------------------------------------------------------
    # Check GitHub releases for a new version and, in a bundle, fetch it and
    # swap it in. The rules for checking, downloading and applying are all in
    # update.py.

    def get_update(self):
        # It answers with the state only. It does not go out to the network,
        # so the screen calls it freely.
        self._json(mw_update.status())

    def post_update_check(self, body):
        self._json(mw_update.check(force=True, token=(body or {}).get("token") or ""))

    def post_update_download(self, body):
        self._json(mw_update.download(token=(body or {}).get("token") or ""))

    def post_update_apply(self, body):
        res = mw_update.apply()
        if "error" in res:
            return self._json(res, 400)
        # The same rule as the shutdown button: close the streams being
        # received properly, then stop after the response has been flushed
        # out. The swap script waits for this process to end.
        _wind_down()
        self._json(res)
        threading.Thread(target=_stop_server, daemon=True).start()


GET_ROUTES = {
    "/": Handler.get_index,
    "/api/videos": Handler.get_videos,
    "/api/backends": Handler.get_backends,
    "/api/live/sessions": Handler.get_sessions,
    "/api/export": Handler.get_export,
    "/api/events": Handler.get_bus,
    "/api/models": Handler.get_models,
    "/api/setup": Handler.get_setup,
    "/api/cookies": Handler.get_cookies,
    "/api/update": Handler.get_update,
    "/api/glossaries": Handler.get_glossaries,
    "/api/search": Handler.get_search,
}
# Paths with a tail. The longer prefix has to come first so that `/api/video/`
# does not swallow `/api/videos` -- an exact path is looked up in the dict above
# first, so all that is kept here is the order.
GET_PREFIX = [
    ("/api/video/", Handler.get_video),
    ("/api/live/events/", Handler.get_events),
    ("/api/multiview/", Handler.get_multiview),
    ("/api/live/status/", Handler.get_live_status),
    ("/api/job/", Handler.get_job),
    ("/api/media/", Handler.get_media),
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
    "/api/active": Handler.post_active,
    "/api/uilang": Handler.post_uilang,
    "/api/viewerlang": Handler.post_viewerlang,
    "/api/live/delete": Handler.post_live_delete,
    "/api/multiview": Handler.post_multiview,
    "/api/multiview/focus": Handler.post_multiview_focus,
    "/api/multiview/add": Handler.post_multiview_add,
    "/api/multiview/remove": Handler.post_multiview_remove,
    "/api/multiview/stop": Handler.post_multiview_stop,
    "/api/cue": Handler.post_cue,
    "/api/cue/add": Handler.post_cue_add,
    "/api/cue/delete": Handler.post_cue_delete,
    "/api/video/delete": Handler.post_video_delete,
    "/api/backends": Handler.post_backends,
    "/api/backends/delete": Handler.post_backends_delete,
    "/api/asr-backends": Handler.post_asr_backends,
    "/api/asr-backends/delete": Handler.post_asr_backends_delete,
    "/api/job/cancel": Handler.post_job_cancel,
    "/api/shutdown": Handler.post_shutdown,
    "/api/models/download": Handler.post_models_download,
    "/api/models/cancel": Handler.post_models_cancel,
    "/api/models/delete": Handler.post_models_delete,
    "/api/models/add": Handler.post_models_add,
    "/api/setup": Handler.post_setup,
    "/api/cookies/youtube": Handler.post_cookies_youtube,
    "/api/cookies/delete": Handler.post_cookies_delete,
    "/api/update/check": Handler.post_update_check,
    "/api/update/download": Handler.post_update_download,
    "/api/update/apply": Handler.post_update_apply,
    "/api/glossaries": Handler.post_glossaries,
    "/api/burn": Handler.post_burn,
}


def parse_range(header: str | None, size: int) -> tuple[int | None, int | None]:
    """`Range: bytes=a-b` into (start, end). (None, None) when absent,
    (None, -1) when it cannot be read.

    Only the shape a browser sends for media is accepted -- one range, in
    bytes. `bytes=a-` (to the end) and `bytes=-n` (the last n bytes) are part
    of that.
    """
    if not header:
        return None, None
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or size <= 0 or (not m.group(1) and not m.group(2)):
        return None, -1
    if not m.group(1):                       # bytes=-n
        n = int(m.group(2))
        if n <= 0:
            return None, -1
        return max(0, size - n), size - 1
    start = int(m.group(1))
    if start >= size:
        return None, -1
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if end < start:
        return None, -1
    return start, end


def _is_cookie_line(ln: str) -> bool:
    ln = ln.strip()
    return bool(ln) and (not ln.startswith("#") or ln.startswith("#HttpOnly_"))


def _cookies_status() -> dict:
    """Whether the cookie file is there and when it arrived. The contents are
    never sent out."""
    path = paths.cookies_path()
    env = (os.environ.get("MIMIWATCH_YTDLP_COOKIES") or "").strip()
    if not os.path.isfile(path):
        return {"present": False, "env": bool(env)}
    try:
        with open(path, encoding="utf-8") as f:
            n = sum(1 for ln in f if _is_cookie_line(ln))
    except OSError:
        n = 0
    return {"present": True, "count": n, "updated": os.path.getmtime(path), "env": bool(env)}


# Stopping serve_forever() needs the server object, but the handler is a class
# and has no way to know the instance. One is kept on the module.
_srv: ThreadingHTTPServer | None = None


def _stop_server():
    time.sleep(0.3)          # room for the response to get out of the socket
    if _srv is not None:
        _srv.shutdown()


def _wind_down(timeout: float = 8.0):
    """Stop the background work before exit: close the live sessions, cancel
    the jobs, and wait.

    The shutdown button, Ctrl-C and applying an update have to pass the same
    place for the record to come out the same. While the jobs were not being
    cancelled, quitting with a transcription running left that thread holding
    the model, so interpreter shutdown could not let the model go and
    ggml-metal's exit destructor aborted (see `jobs.cancel_all`). The wait has
    an upper bound -- an exit that never comes because of a thread that will
    not stop is worse.
    """
    deadline = time.time() + timeout
    # The cancel marks have to go up first so that the jobs stop alongside
    # while the sessions are waited on. Waiting on them in order takes twice as
    # long in the worst case.
    cancelled = jobs.cancel_all()
    if cancelled:
        print(f"mimiwatch: cancelling {len(cancelled)} jobs", flush=True)
    stopped = live.shutdown(timeout)
    jobs.wait_idle(max(0.0, deadline - time.time()))
    # For live sessions the wait goes only as far as the DB state becoming
    # `stopped`. After that, `_run`'s finally (closing the translator and the
    # refiner, up to 20 seconds) may still be using the model, and what makes
    # that harmless is that `hard_exit` skips the destructors. Going back to an
    # ordinary exit (SystemExit) brings the same abort back on the live side.
    return stopped


def hard_exit(code: int = 0):
    """Flush the output and end with `os._exit`. Both the C destructors and
    finalization are skipped.

    This process has two copies of ggml loaded (transcribe.cpp and llama_cpp)
    and both release the Metal device from a destructor at exit. If a model is
    still holding GPU buffers then, it aborts with
    `GGML_ASSERT([rsets->data count] == 0)`, and if a thread is computing with
    that model it can even stall under a released device -- "the server is dead
    but the memory and the GPU are still there" is that shape. Once everything
    above has been stopped, leaving the rest of the cleanup to the operating
    system is the safer way. SQLite commits on every write, so nothing is lost.
    `main()` is left returning a value so the tests can call it, and only the
    entry points that come up as a process (`__main__`, app.py) call this.
    """
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:
            pass
    os._exit(code)


def main(argv: list[str] | None = None):
    """Bring the server up. `argv` is handed over by the tests and by the
    bundle's entry point (app.py).

    `--open` opens the screen in the default browser as soon as it is up.
    Someone who started the bundle with a double click has no terminal and so
    nowhere to see the address.
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--open", action="store_true", help="opens the screen in a browser")
    args = ap.parse_args(argv)

    # Schema creation and recovery are finished before any request is taken.
    # The jobs and sessions that were running before the restart cannot be
    # carried on, so rather than pretending they still run they are written as
    # interrupted.
    store.init()
    # The `data/<video id>.json` files an older version left behind are moved
    # into the table. With nothing to move it does nothing, so calling it every
    # time is fine. The originals are not deleted, they are moved to
    # data/legacy/.
    store.import_legacy_docs()
    stale_jobs, stale_live = jobs.restore(), live.restore()
    if stale_jobs or stale_live:
        print(f"mimiwatch: marked {stale_jobs} jobs and {stale_live} live "
              "sessions from before the restart as interrupted", flush=True)

    global _srv
    try:
        _srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        # A server that is already up (or another program) holds that port.
        # Double-clicking the bundle is mostly this case, so its screen is
        # opened and we step back.
        print(f"mimiwatch: cannot open port {args.port} ({exc}). "
              "If one is already up, its screen is used.", file=sys.stderr, flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(f"http://localhost:{args.port}/")
        return 1
    print(f"mimiwatch: http://localhost:{args.port}/", flush=True)
    print(f"mimiwatch: models {paths.model_dir()} · data {store.DATA} · config {config.CONFIG}",
          flush=True)
    if args.open:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{args.port}/")).start()
    # The check for a new version is once a day, in the background. It can be
    # turned off with `"update_check": false` in backends.json or with the
    # environment variable -- the one request that goes out is the release list
    # lookup.
    mw_update.start_auto_check()
    try:
        _srv.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is gathered into the same place as the screen's shutdown
        # button. Whichever way it is stopped, the sessions have to be left as
        # "ended".
        print("\nmimiwatch: shutting down", flush=True)
        _wind_down()
    _srv.server_close()
    print("mimiwatch: shut down", flush=True)
    return 0


if __name__ == "__main__":
    hard_exit(main())
