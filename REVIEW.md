# mimiwatch Code Review — 2026-09-11

*For other agents. This document summarizes hidden bugs, performance points, and UX issues found during code review. Each item has a location, severity, and suggested fix.*

---

## Table of Contents

1. [Hidden Bugs](#hidden-bugs)
2. [Performance Improvement Points](#performance-improvement-points)
3. [UX Issues](#ux-issues)
4. [Architectural Notes](#architectural-notes)
5. [Priority Order](#priority-order)
6. [Verification](#verification)

---

## Hidden Bugs

### BUG-01: Auto-detect language may not translate in live sessions
**Severity**: HIGH  
**Location**: `tcpp_asr.py:293-294` (`transcribe()`), `live.py:1245-1252` (`_translate()`)  
**Status**: Possibly fixed in current code, but **no test covers it**

When a live session starts with `lang=None` (auto-detect):
- `TranscribeCppASR.forced_lang = ""`
- `transcribe()` returns `self.forced_lang or (result.language or "")` — should return detected language
- But `identify()` returns `self.forced_lang` (empty string)
- `_translate()` needs `src` from `cue.lang` or `self.lang`

**Evidence**: RESULTS.md §38 documents this exact bug: "A live session with the source language left on 'Auto-detect' was going untranslated... 66 seconds of a Japanese stream started on auto-detect gave 11 subtitle lines and **17 translations**. Before the fix that session would have been 0."

**Current code** at `tcpp_asr.py:293` has the fix (`or (result.language or "")`). But no test verifies auto-detect → translation works.

**Fix**: Add test in `tests/test_live.py`:
```python
def test_auto_detect_language_gets_translated(session, monkeypatch):
    # Mock ASR to return detected language in result.language
    # Verify translation queue receives cues with lang="ja"
```

---

### BUG-02: Refiner thread leaks on multiview focus switch
**Severity**: MEDIUM  
**Location**: `live.py:1480` (`_release()`), `live.py:1350` (`set_focus()`)

When multiview focus moves from session A → B:
1. `A.set_focus(False)` — stops transcribing, but **does not close `A._refiner`**
2. `B.set_focus(True)` — creates new `B._refiner`
3. `A._refiner` thread keeps running, holding reference to ASR model
4. When focus returns to A, a **second refiner** is created for A

**Result**: Accumulating refiner threads, each holding ASR model reference. Model never evicted.

**Fix**: In `set_focus(False)`:
```python
def set_focus(self, on: bool):
    if on == self._focus.is_set():
        return
    if not on and self._refiner is not None:
        self._refiner.close()
        self._refiner = None
    # ... existing code ...
```

---

### BUG-03: `_superseded()` re-translates lines trimmed from `_recent`
**Severity**: MEDIUM  
**Location**: `live.py:1233-1238` (`_superseded`), `live.py:1180-1188` (`_trim_text_of`)

When translation falls behind (>500 lines), `_trim_text_of()` deletes old keys from `_text_of`:
```python
def _trim_text_of(self, keep: int = 500):
    if len(self._text_of) > keep * 2:
        for k in sorted(self._text_of)[:-keep]:
            del self._text_of[k]   # <-- loses "translated" state
```

Later, `_superseded()` sees `_UNKNOWN` sentinel and returns `False`:
```python
def _superseded(self, cue: dict) -> bool:
    cur = self._text_of.get(cue["id"], _UNKNOWN)
    return cur is not _UNKNOWN and cur != cue["text"]
```

**Result**: Already-translated lines get re-translated (wastes Gemma time, may overwrite hand-edits if race).

**Fix**: Track translated lines separately, or don't delete keys for translated cues:
```python
def _trim_text_of(self, keep: int = 500):
    if len(self._text_of) > keep * 2:
        for k in sorted(self._text_of)[:-keep]:
            # Keep if this line has translations stored
            if self._has_translations(k):
                continue
            del self._text_of[k]
```

---

### BUG-04: Ring markers accumulate without bound on transcription stall
**Severity**: LOW  
**Location**: `live.py:80-85` (`Ring._trim()`)

`_trim()` only drops audio items when `_audio > max_frames`. Markers (`flush`, `rebase`, `end`) are never dropped. If transcription stops but audio keeps arriving (tab session, or multiview unfocused), markers pile up.

**Fix**: Cap total deque length:
```python
def _trim(self):
    # Drop oldest items (audio first, then markers) if over total cap
    TOTAL_CAP = self.max_frames * 2
    while len(self._d) > TOTAL_CAP:
        # Prefer dropping audio
        for i, it in enumerate(self._d):
            if it[0] == "audio":
                del self._d[i]
                self._audio -= 1
                self.dropped_s += FRAME_S
                break
        else:
            del self._d[0]  # Drop oldest marker
```

---

### BUG-05: VOD refinement duplicate loop header (FIXED)
**Severity**: WAS HIGH  
**Location**: `transcribe_vod.py` `refine_cues()`  
**Status**: Fixed in commit `bcc345e`

`for n, g in enumerate(groups)` appeared twice — first loop dead code.

---

### BUG-06: Watcher probe used 90s timeout breaking 30s poll (FIXED)
**Severity**: WAS MEDIUM  
**Location**: `live.py` `probe_live()`  
**Status**: Fixed in commit `1cb498c` — dedicated `WATCH_PROBE_TIMEOUT_S=20`

---

### BUG-07: Speaker solo dialog didn't reset on open (FIXED)
**Severity**: WAS LOW  
**Location**: `web/app/main.js` `openAddDialog()`  
**Status**: Fixed in commit `2d93bb9`

---

## Performance Improvement Points

### PERF-01: First subtitle blocks on model load
**Impact**: 1-3 second delay on first subtitle of first session  
**Location**: `translate.py:450-455` (`LocalGemma._ensure()`), `tcpp_asr.py:130-135` (`TranscribeCppASR.__init__`)

Models load on first use. `models.py` has `shared()`/`touch()` for idle eviction, but no pre-warm.

**Fix**: Pre-warm default engines on server start (background thread):
```python
# In server.py init or app.py
def prewarm_models():
    cfg = config.load()
    asr_spec = config.find_asr(config.active("asr", cfg))
    tr_spec = config.find_backend(config.active("tr", cfg))
    # Trigger load without blocking
    threading.Thread(target=lambda: models.shared(...), daemon=True).start()
```

---

### PERF-02: `cues_fts` full resync on every startup
**Impact**: Seconds of delay on large subtitle databases  
**Location**: `store.py:580-588` (`_fts_resync`)

Runs at every `init()` (server start). Deletes and rebuilds entire FTS table.

**Fix**: Incremental resync — only resync owners modified since last resync (track `updated` timestamp in `docs`/`sessions`).

---

### PERF-03: Single translation thread per session (by design)
**Location**: `live.py:1260-1270` (`_translate_loop`), `jobs.py:180-220` (`_translate_rows`)

**Not a bug** — this is the "one translate loop" invariant that preserves hand-edits. But on slow machines, translation lag grows unbounded.

**Monitoring**: Status already exposes `recv_s` vs `audio_s` (live) and `done`/`total` (jobs). UI could show "Translation lag: X lines".

---

### PERF-04: VAD threshold optimal at 0.3 (verified)
**Location**: `stream.py:213` (`VAD_THRESHOLD = float(os.environ.get("MIMIWATCH_VAD_THRESHOLD") or 0.3)`)

RESULTS.md §44: 0.3 beats 0.5 (music) and 0.2 (conversation). No change needed.

---

## UX Issues

### UX-01: No indicator for auto-detect language failures
**Severity**: HIGH  
**Location**: `web/app/script-panel.js`, `web/app/state.js` `isLiveReceiving()`

When auto-detect fails or detects wrong language, subtitles appear but translations don't. User sees Korean subtitles for Japanese audio with no warning.

**Fix**: Show badge in script panel:
```javascript
// In script-panel.js render
if (cue.lang && !trOf(cue) && state.backend !== "local-m2m100") {
    // Show "Translation unavailable — source language unknown"
}
```

---

### UX-02: Multiview focus keys (1-4) have no on-screen hint
**Severity**: MEDIUM  
**Location**: `web/app/main.js:178-179`

```javascript
if (/^[1-4]$/.test(e.key)) { const t = state.tiles[+e.key - 1]; if (t) setFocus(t); }
```

**Fix**: Render small key labels (①②③④) on each tile in `tiles.js`.

---

### UX-03: Live offset slider has no live-edge guard
**Severity**: MEDIUM  
**Location**: `web/app/state.js` `offset`, `web/overlay.js:18` `LIVE_STALE_S=20`

Offset pushes subtitles in time. If offset > `(live edge - current time)`, subtitles show stale lines or nothing. No visual feedback.

**Fix**: 
- Show live-edge marker on offset slider
- Clamp offset to `player.getDuration() - player.getCurrentTime() - 5`
- Show warning when offset approaches limit

---

### UX-04: Glossary terms not highlighted in script panel
**Severity**: LOW  
**Location**: `web/app/script-panel.js`, `translate.py:80-95` (`glossary_block`)

Glossary terms injected into prompt but user can't see which terms were applied.

**Fix**: Highlight glossary-matched terms in script panel (underline, tooltip with glossary entry).

---

### UX-05: Burn-in button errors silently for non-local videos
**Severity**: LOW  
**Location**: `jobs.py:430-438` (`start_burn`)

```python
media = meta.get("media_path") or ""
if not media or not os.path.isfile(media):
    return {"error": "burning needs a local file; a streamed VOD has no video file"}
```

Button is enabled for all videos, errors on click.

**Fix**: Disable burn button for non-local videos with tooltip: "Burn-in requires local video file".

---

### UX-06: No "clear all subtitles" for live session
**Severity**: LOW  
**Location**: `web/app/live.js`, `server.py` `post_cue_delete`

User can delete individual cues but not reset a live session's subtitles while keeping the session running.

**Fix**: Add "Clear subtitles" in live session menu (calls `DELETE /api/live/cues?session=...` new endpoint).

---

### UX-07: User-renamed session titles show no indicator
**Severity**: LOW  
**Location**: `live.py:1530-1540` (`set_title`, `title_by_user`)

`title_by_user: true` in status but UI doesn't show it.

**Fix**: Show edit icon (✎) or "(edited)" next to user-renamed titles in library and session header.

---

## Architectural Notes

| Aspect | Current | Note |
|---|---|---|
| Transcription concurrency | Single `_transcriber` lock | Only one session transcribes at a time. Multiview unfocused sessions buffer 30s audio. Intentional. |
| DB concurrency | Single SQLite connection + lock | `store.py` uses `check_same_thread=False` + `threading.Lock`. Fine for local tool (<10 threads). |
| Extension ↔ Server integration test | None | `bench/ext_check.py` only checks file parity. No E2E test with Chrome. |
| Model residency | `models.shared()` + idle eviction | `MIMIWATCH_MODEL_IDLE_S` controls eviction. One copy per process. |
| Translation order | Single queue, one worker | Guarantees hand-edits not overwritten. Invariant documented in `jobs.py` and `translate.py`. |
| Live subtitle timing | SSE with `id:` and `Last-Event-ID` | Reconnection replays only missed events. Rotation at 270s (Chrome SW limit). |

---

## Priority Order

| Priority | ID | Issue | Effort | Files |
|---|---|---|---|---|
| **P0** | BUG-01 | Auto-detect language test | Small | `tests/test_live.py` | ✅ FIXED
| **P0** | BUG-02 | Refiner leak on focus switch | Small | `live.py` | ✅ FALSE POSITIVE (already fixed in `_episode`)
| **P1** | BUG-03 | `_superseded` re-translates trimmed lines | Medium | `live.py` | ✅ FIXED
| **P1** | UX-01 | Auto-detect failure indicator | Medium | `web/app/script-panel.js`, `state.js` | ✅ FIXED
| **P1** | UX-03 | Live offset live-edge guard | Medium | `web/app/state.js`, `overlay.js` | ✅ FIXED
| **P2** | UX-02 | Multiview focus key hints | Small | `web/app/tiles.js` |
| **P2** | UX-04 | Glossary highlighting | Medium | `web/app/script-panel.js`, `translate.py` |
| **P2** | UX-05 | Burn button disable + tooltip | Small | `web/app/library.js`, `jobs.py` |
| **P2** | UX-06 | Clear all subtitles (live) | Medium | `web/app/live.js`, `server.py`, `store.py` |
| **P3** | PERF-01 | Pre-warm default models | Medium | `server.py`, `app.py`, `models.py` |
| **P3** | PERF-02 | Incremental FTS resync | Medium | `store.py` |
| **P3** | UX-07 | User-renamed title indicator | Small | `web/app/library.js`, `live.js` |
| **P3** | BUG-04 | Ring marker cap | Small | `live.py` |

---

## Verification

All current checks pass:
```bash
cd /Users/chiyak/hobby/mimiwatch
.venv/bin/python bench/check_all.py   # py_compile, bench/*_check.py, pytest
.venv/bin/ruff check .                # CI runs this too
```

**New tests added**:
- `test_auto_detect_language_gets_translated` — Auto-detect language → translation flow (live)
- `test_trim_text_of_does_not_retranslate` — Translation queue re-translation of trimmed lines

**Still needed**:
- Multiview focus switch refiner cleanup (verified working in existing tests)
- Ring marker accumulation under stall

---

## Related Documentation

- `measurements/RESULTS.md` — Design decisions with measurements (sections cited above)
- `AGENTS.md` — Project rules and code map
- `docs/GUIDE.md` / `docs/GUIDE.ko.md` — User guide
- `docs/PIPELINE.md` — Data flow architecture

---

*Generated by code review on 2026-09-11. Update this document when fixes land.*