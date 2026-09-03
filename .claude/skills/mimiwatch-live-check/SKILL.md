---
name: mimiwatch-live-check
description: Verify a mimiwatch change in the running app against a real stream — server up, session started, subtitles arriving, overlay on the YouTube page, resume after restart. Use for "서버를 띄워주세요", "테스트해주세요", "확인해보세요", "오버레이가 안 보입니다", "자막이 안 나옵니다", "실제로 동작하나요", "run the app and check", and before reporting any front-end or extension change as working. Tests never load models, so this is the only way those paths get exercised. Not for unit-testable logic — that belongs in tests/.
---

# Verify against the running app

The failure this skill exists to prevent: a page served by the agent for testing was
mistaken for the real server, the change "did not work", and the next half hour went into
a bug that was never there. So the first step is not starting anything.

## Step 0 — say which server this check runs against

State it in one line before touching the browser. Only three answers are valid:

| Target | How | When |
|---|---|---|
| The repo server | `./run.sh` → `127.0.0.1:8900` | default |
| A worktree copy | `PORT=8951 ./run.sh` from the worktree | main's server is up and in use |
| An installed bundle | the app the owner launched | verifying a release |

Never a page the agent serves itself, and never a static copy of `web/`. If the owner is
already running a server, ask which port rather than starting a second one — two servers
on one `data/mimiwatch.db` is not a supported configuration.

## Step 0b — the decisions to confirm with the requester

1. **Which surface.** Standalone page, 대본 모드 popup, the YouTube overlay (extension),
   or multiview. They are four different code paths and a fix in one proves nothing about
   the others.
2. **Which source.** URL, tab audio (extension capture), or a local file. Tab audio has
   no title of its own and takes the sharing prompt — the owner has to click it.
3. **Live or VOD.** A live check needs a stream that is live *now*; the owner usually
   supplies one, and a link from an earlier session is almost certainly over.

## Step 0c — the facts to read now

```sh
lsof -nP -iTCP:8900 -sTCP:LISTEN            # is something already serving?
.venv/bin/python bench/doctor.py            # what this machine can actually do
.venv/bin/python bench/doctor.py <url>      # ... including whether yt-dlp resolves it
```

`doctor.py` is cheap on purpose — it loads the ASR model and drops it, and never touches
the 5 GB translator.

## Step 1 — if `web/` changed, copy to `ext/` first

`overlay.js`, `cuestore.js`, `capture.js`, `ytid.js`, `capture-worklet.js` exist twice and
must be byte-identical (MV3 forbids remote code).

```sh
for f in overlay.js cuestore.js capture.js ytid.js capture-worklet.js; do cp "web/$f" "ext/$f"; done
.venv/bin/python bench/ext_check.py
```

## Step 2 — answer "does this need a restart?" before testing

This question has been asked in nearly every session, so answer it up front rather than
letting a stale process look like a broken change:

- **Python changed** → restart the server. A live session survives it (resume exists),
  but the process does not reload.
- **`web/` changed** → a browser reload is enough.
- **`ext/` changed** → a page reload is **not** enough. Reload the extension at
  `chrome://extensions`, then reload the YouTube tab. The extension is loaded unpacked
  from this repo's `ext/`, so the files are live but the service worker is not.
- **`backends.json` / engine defaults** → only `config.py` writes that file; new default
  engines are seeded once and a machine that has seen them will not re-seed.

## Step 3 — the checks, in this order

Each one has produced a real bug; run the ones your change can touch, and say which you
skipped.

1. **Subtitles arrive.** Finals appear, then refined lines replace them under the same
   cue id. The panel follows cues by id, not by index.
2. **Overlay on the YouTube page.** Selecting a session should not need a page reload; if
   the code deliberately forces one, say so.
3. **자막 내역 in the chat column** — and, critically, that it *leaves* when you navigate
   to a different video. A panel that survives navigation and cannot be dismissed was the
   top-priority bug once already.
4. **Resume (이어받기)** after a server restart: earlier cues still present, numbering
   continues, state flips back to receiving. Resume starts from *now*, not from the gap.
5. **Fullscreen.** Only the fullscreen element's subtree renders; controls must stay
   reachable and must not cover the subtitles.
6. **Multiview**, if touched: only the focused tile transcribes, the others keep a 30 s
   ring. Removing or reordering tiles has repeatedly left a player in infinite loading —
   check the remaining tiles, not just the one you moved.

Read the console and the network log rather than inferring:
`read_console_messages` with a pattern, and the SSE request on `/api/events`.

## Step 4 — stop cleanly

Use the app's own shutdown (`⏻`). It closes live sessions first. A killed process leaves
them as `중단됨`, which is a *different state* from a user stop and will confuse the next
session's reading of the same row.

## Traps that have bitten before

- **Never trigger `alert` / `confirm` / a modal.** It blocks the Chrome MCP connection
  for the rest of the session; `console.log` plus `read_console_messages` instead.
- **The content script cannot use `innerHTML`** (YouTube enforces Trusted Types) and must
  not `fetch` the server itself — the service worker does, which is why the server needs
  no CORS.
- **Live SSE, never WebSocket.** The extension hand-parses SSE from a streamed `fetch`;
  MV3 service workers have no `EventSource`.
- **A live seek must never overshoot the live edge** — that alone flips the player into
  "ended".
- **Switching engines keeps the session id** on purpose (cues are stored by session id);
  a "history was wiped" report means that invariant broke.
- **A red check is not automatically your change.** Before blaming a diff, check whether
  it touches any `.py` at all. `bench/live_errors.py` now builds its session through the
  real `LiveSession.__init__` because a hand-listed attribute set went stale when
  multiview added `group`, and the failure surfaced at "engine swap keeps the session" —
  reading exactly like a broken feature. Equally: do not quietly fix an unrelated check
  inside another task.
- **A session left over from a previous run** can serve cues that look like your change
  working. Start a fresh session when the result matters.

## What this skill does not do

- Log in to membership-gated content, or handle cookies for it.
- Test Windows, AMD or CUDA paths — no such machine here; the owner has testers and
  `doctor.py` output is what to ask them for.
- Replace `tests/`. Anything expressible without a model belongs there, where it runs in
  CI; `tests/conftest.py` makes a model load raise, and that is deliberate.
