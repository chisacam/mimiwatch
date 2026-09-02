# AGENTS.md

Guidance for coding agents working in this repository. Humans read
[README.md](README.md) and [docs/GUIDE.md](docs/GUIDE.md); this file is for you.

## What this is

mimiwatch is a local tool that transcribes and translates YouTube/Twitch live
streams, VODs and local media files, and overlays subtitles on the player.
Everything runs on the user's machine: Python stdlib HTTP server (`server.py`),
vanilla-JS front end (`web/`), a Chrome MV3 extension (`ext/`), SQLite storage.
Solo hobby project; the owner communicates in Korean.

## Workflow

- **Always work in a git worktree.** Never edit `main` directly. Create a
  worktree under `.claude/worktrees/<task>` on its own branch, do the work
  there, run the checks, then merge (or hand the branch back) when green.
- **Use subagents actively.** Fan out independent work: exploration and code
  mapping, parallel edits to unrelated modules, running the check suite,
  reviewing a diff. Each subagent works from the same worktree path. Keep the
  final decision and the merge in the coordinating agent.
- One task, one branch, one worktree. Remove the worktree when merged.
- Commit only when asked. When you do, one commit per logical change.
- Do not push, tag or create releases unless explicitly asked. Tags `v*`
  trigger the release build (`.github/workflows/build.yml`).

## Language and style

- **All Korean, in prose, explaining *why*.** Comments, docstrings, commit
  messages, UI strings, error messages, docs. Only this file is English.
- Comments typically open with the prior bad behaviour that motivated the code,
  often citing a measurement. Match that. Use `--` as the dash inside code
  comments (e.g. `# 예시 설정은 프로그램과 함께 다닙니다 -- 저장소 안, 또는 묶음 안.`).
- Commit message: one Korean sentence in the `~합니다 / ~고칩니다` register,
  optionally followed by ` -- ` and the reason. See `git log` for the pattern.
- Module docstrings are long by design. Do not trim them.

## Checks (run before you call anything done)

```sh
.venv/bin/python bench/check_all.py   # py_compile, bench/*_check.py, pytest
.venv/bin/ruff check .                # CI runs this too
```

`check_all.py` runs, in order: compile every root `*.py`; `bench/edit_check.py`,
`export_check.py`, `ext_check.py`, `live_errors.py`; then `pytest -q tests/`.
Nothing in it loads a model or touches the network. CI
(`.github/workflows/check.yml`) runs `ruff check .`, `pytest`, `bench/ext_check.py`
on pushes and PRs to `main`; there the transcribe.cpp runtime is absent and
`tests/conftest.py` stubs it.

Run the app: `./run.sh` (default `PORT=8900`, binds `127.0.0.1` only).
Diagnostics: `.venv/bin/python bench/doctor.py [url]`.

## Hard rules

- **Tests never load models.** `tests/conftest.py` makes any model load raise.
  A test that needs a model is a wrong test. The `isolated` fixture redirects
  `store`, `jobs` and `config` paths to a tmpdir; never touch the real
  `data/mimiwatch.db` or `backends.json` from tests.
- **`backends.json` is read and written only through `config.py`.** It holds
  real endpoints and API keys, is never overwritten wholesale, and new default
  engines are seeded only if never seen before (`seeded` list). `config.py`
  imports no other project module; keep it that way (`jobs` imports `live`,
  so `live` cannot import `jobs`). `config.PROTECTED` must match the UI `LOCKED`.
- **`web/` is the source, `ext/` holds byte-identical copies** of
  `overlay.js`, `cuestore.js`, `capture.js`, `ytid.js`, `capture-worklet.js`
  (MV3 forbids remote code). After editing any of them in `web/`, `cp` to
  `ext/`. `bench/ext_check.py` fails otherwise.
- **`web/app/*.js` are plain scripts sharing one global scope.** No modules,
  no bundler. Load order is fixed in `web/index.html`: `state.js` first,
  `main.js` last. Adding a file means adding a `<script>` tag in the right place.
- **Heavy models go through `models.shared()` / `touch()`.** Never construct
  a model ad hoc; one resident copy per process, optional idle eviction.
- **Live subtitles travel over SSE, never WebSocket.** The extension parses
  SSE by hand from a streamed `fetch` (MV3 service workers have no
  `EventSource`).
- **One translate loop** (`jobs._translate_rows`). Do not add another; it is
  what keeps hand-edited translations from being clobbered.
- **Server routing is a table** (exact dict + prefix list in `server.py`), not
  an if-chain. Origin/Host checks and JSON parsing happen once before dispatch.
- **Extension content script never uses `innerHTML`** (YouTube enforces
  Trusted Types) and never `fetch`es the server itself; the service worker does
  (so the server needs no CORS for youtube.com).
- Do not commit `backends.json`, `data/`, `bench/out_*.json`, or anything under
  `measurements/` you did not measure.

## Code map

| Path | Owns |
|---|---|
| `server.py` | HTTP server + API on stdlib `ThreadingHTTPServer`; serves `web/`; localhost-only with Host/Origin check |
| `app.py` | PyInstaller bundle entry (`--port`, `--ytdlp`, `--doctor`); repo mode runs `server.py` directly |
| `live.py` | Live sessions: yt-dlp → HLS → ffmpeg → 16kHz PCM, final/refined cue replacement, SSE delivery, multiview groups |
| `stream.py` | VAD → transcribe → refine loop (ported from hayamimi), yt-dlp cookie config |
| `tcpp_asr.py` / `asr.py` | transcribe.cpp GGUF adapter; OpenAI-compatible remote ASR (VOD chunked, live per-utterance) |
| `transcribe_vod.py` | VOD → timestamped cues; CLI calls `jobs.start_transcribe` (same path as the server) |
| `translate.py` | Translation backends behind one interface: local Gemma (default), M2M-100 (CT2, CPU), OpenAI-compatible; genre prompts |
| `jobs.py` | Background VOD transcription and (re)translation; the single translate loop |
| `store.py` | SQLite (`data/mimiwatch.db`): jobs/sessions as JSON blobs, cues as a real table; one locked connection |
| `export.py` | SRT/VTT/TXT/JSON export; synthesizes end times for live cues |
| `config.py` | The only reader/writer of `backends.json`; atomic, lock-guarded |
| `models.py` | Process-wide model cache (`shared`/`touch`), `MIMIWATCH_MODEL_IDLE_S` eviction |
| `modelhub.py` | Model/tool catalog and download queue (`.part` + Range resume); also a CLI |
| `paths.py` | Every file location for repo mode and frozen bundle; `MIMIWATCH_*` dir overrides |
| `update.py` | GitHub release check (once a day), download, swap-script apply; repo mode only notifies |
| `bus.py` | Global SSE change feed `/api/events` (session/video/job/model/multiview) |
| `speaker_id.py` | CAM++ speaker labels, VOD only |
| `web/app/` | Front end, load order: state, adapters, tiles, player, script-panel, engines, jobs, live, capture, export, library, bus, update, main |
| `ext/` | Chrome MV3 extension: `content.js` overlay + `<video>` clock, `background.js` server I/O, `offscreen.*` tab capture, `panel.js` transcript, `popup.*` |
| `tests/` | pytest, no models, no network |
| `bench/` | Measurement and check scripts; `*_check.py` run in CI-equivalent, the rest load models or hit the network |
| `docs/`, `measurements/RESULTS.md` | Design decisions and the measurements behind them; cite them, do not contradict them silently |

## Environment variables the code reads

`MIMIWATCH_HOME`, `MIMIWATCH_MODEL_DIR`, `MIMIWATCH_DATA_DIR`, `MIMIWATCH_CONFIG`
(locations, `paths.py`); `MIMIWATCH_MODEL_IDLE_S`; `MIMIWATCH_NO_UPDATE_CHECK`,
`MIMIWATCH_VERSION`; `MIMIWATCH_SSE_ROTATE_S`; `MIMIWATCH_YTDLP_COOKIES`,
`MIMIWATCH_YTDLP_COOKIES_BROWSER`; `MIMIWATCH_VAD_MODEL`, `MIMIWATCH_VAD_THRESHOLD`;
`HF_TOKEN`; `PORT` (run.sh). Tests use `MIMIWATCH_TEST_PORT` (8931).

## Design facts to keep in mind

- Default engines are the light CPU pair (SenseVoice Small + M2M-100); the
  quality pair (whisper-large-v3-turbo + Gemma 4) is opt-in. Do not flip the
  default; the reasoning is in `measurements/RESULTS.md` §33–34.
- Live cues carry only a start time; VOD cues carry ranges. Export invents live
  end times (next cue, max 6 s, min 0.8 s).
- Refined live lines replace finals under the same cue id. Cue arrays are
  sorted by start; the panel follows cues by id, not by index.
- Multiview transcribes only the focused tile; others keep a 30 s audio ring.
- Engine switch mid-session swaps in place and keeps the session id, because
  cues are stored by session id.
- Live seek must never overshoot the live edge (that alone flips the player
  into "ended").
- Server shutdown (`⏻`) closes live sessions cleanly first; a killed process
  leaves sessions as "중단됨", which is a different state from user stop.
- yt-dlp lives in `.venv` (system copies go stale and silently return no
  formats). YouTube VODs and cookie-based access need a JS runtime (deno).
- Windows GPU path is Vulkan for every vendor; see `docs/WINDOWS.md` before
  proposing CUDA/ROCm builds.
