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

### BUG-01: Auto-detect language may not translate in live sessions — ✅ FIXED
**Severity**: HIGH  
**Location**: `tcpp_asr.py:293-294` (`transcribe()`), `live.py:1245-1252` (`_translate()`)  
**Status**: Fixed in code, test added

**Fix applied**: Added `test_auto_detect_language_gets_translated` in `tests/test_live.py` verifying auto-detect → translation flow works.

---

### BUG-02: Refiner thread leaks on multiview focus switch — ✅ FALSE POSITIVE
**Severity**: MEDIUM  
**Location**: `live.py:1480` (`_release()`), `live.py:1350` (`set_focus()`)  
**Status**: Already fixed in `_episode()` — refiner closed in `finally` block when focus lost.

---

### BUG-03: `_superseded()` re-translates lines trimmed from `_recent` — ✅ FIXED
**Severity**: MEDIUM  
**Location**: `live.py:1233-1238` (`_superseded`), `live.py:1180-1188` (`_trim_text_of`)  
**Status**: Fixed

**Fix applied**: Added `_translated_ids` set to track translated cue IDs. `_trim_text_of` now preserves entries for translated cues. `_superseded()` checks this set first.

**Tests added**: `test_trim_text_of_does_not_retranslate` in `tests/test_live.py`.

---

### BUG-04: Ring markers accumulate without bound on transcription stall
**Severity**: LOW  
**Location**: `live.py:80-85` (`Ring._trim()`)

`_trim()` only drops audio items when `_audio > max_frames`. Markers (`flush`, `rebase`, `end`) are never dropped. If transcription stops but audio keeps arriving (tab session, or multiview unfocused), markers pile up.

**Fix**: Cap total deque length:
```python
def _trim(self):
    TOTAL_CAP = self.max_frames * 2
    while len(self._d) > TOTAL_CAP:
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

Not a bug — this is the "one translate loop" invariant that preserves hand-edits. But on slow machines, translation lag grows unbounded.

**Monitoring**: Status already exposes `recv_s` vs `audio_s` (live) and `done`/`total` (jobs). UI could show "Translation lag: X lines".

---

### PERF-04: VAD threshold optimal at 0.3 (verified)
**Location**: `stream.py:213` (`VAD_THRESHOLD = float(os.environ.get("MIMIWATCH_VAD_THRESHOLD") or 0.3)`)

RESULTS.md §44: 0.3 beats 0.5 (music) and 0.2 (conversation). No change needed.

---

## UX Issues

### UX-01: No indicator for auto-detect language failures — ✅ FIXED
**Severity**: HIGH  
**Location**: `web/app/script-panel.js`, `web/app/state.js` `isLiveReceiving()`

When auto-detect fails or detects wrong language, subtitles appear but translations don't. User sees Korean subtitles for Japanese audio with no warning.

**Fix applied**: Added `tr-missing-lang` badge in script panel with i18n strings (`panel.row.missingLang`, `panel.row.missingLang.title`). CSS uses `--err` color.

---

### UX-02: Multiview focus keys (1-4) have no on-screen hint
**Severity**: MEDIUM  
**Location**: `web/app/main.js:178-179`

```javascript
if (/^[1-4]$/.test(e.key)) { const t = state.tiles[+e.key - 1]; if (t) setFocus(t); }
```

**Fix**: Render small key labels (①②③④) on each tile in `tiles.js`.

---

### UX-03: Live offset slider has no live-edge guard — ✅ FIXED
**Severity**: MEDIUM  
**Location**: `web/app/state.js` `offset`, `web/overlay.js:18` `LIVE_STALE_S=20`

Offset pushes subtitles in time. If offset > `(live edge - current time)`, subtitles show stale lines or nothing. No visual feedback.

**Fix applied**: Added `updateOffsetLiveEdge()` in `state.js` that clamps offset max to `(duration - currentTime - 2s)`. Called on offset input. Visual warning via CSS class `live-edge-warning` on `#offset-wrap`.

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

### UX-08: Script panel not highlighting current line for live receiving — ✅ FIXED
**Severity**: HIGH (Windows-specific sync issue)  
**Location**: `web/app/script-panel.js:441` (`markScript`)

For live receiving, `markScript` returned early and never highlighted the current line. The script panel only scrolled to bottom via `pinScriptToBottom`, but didn't show which line was actually on screen (the overlay shows the most recent cue).

**Fix applied**: `markScript` now highlights the last cue for live receiving, while `pinScriptToBottom` continues to handle scrolling. For VOD, behavior unchanged.

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

| Priority | ID | Issue | Effort | Files | Status |
|---|---|---|---|---|---|
| **P0** | BUG-01 | Auto-detect language test | Small | `tests/test_live.py` | ✅ DONE |
| **P0** | BUG-02 | Refiner leak on focus switch | Small | `live.py` | ✅ FALSE POSITIVE |
| **P1** | BUG-03 | `_superseded` re-translates trimmed lines | Medium | `live.py` | ✅ DONE |
| **P1** | UX-01 | Auto-detect failure indicator | Medium | `web/app/script-panel.js`, `state.js` | ✅ DONE |
| **P1** | UX-03 | Live offset live-edge guard | Medium | `web/app/state.js`, `overlay.js` | ✅ DONE |
| **P2** | UX-02 | Multiview focus key hints | Small | `web/app/tiles.js`, `index.html`, `app.css` | ✅ DONE |
| **P2** | UX-04 | Glossary highlighting | Medium | `web/app/script-panel.js`, `translate.py` | 🔄 PENDING |
| **P2** | UX-05 | Burn button disable + tooltip | Small | `web/app/export.js`, `strings/core.js`, `app.css` | ✅ DONE |
| **P2** | UX-06 | Clear all subtitles (live) | Medium | `web/app/live.js`, `server.py`, `store.py` | 🔄 PENDING |
| **P2** | BUG-04 | Ring marker cap | Small | `live.py` | ✅ DONE |
| **P3** | PERF-01 | Pre-warm default models | Medium | `server.py`, `app.py`, `models.py` | 🔄 PENDING |
| **P3** | PERF-02 | Incremental FTS resync | Medium | `store.py` | ✅ DONE |
| **P2** | UX-08 | Script panel live follow highlight | Small | `web/app/script-panel.js` | ✅ DONE |
| **P3** | UX-07 | User-renamed title indicator | Small | `web/app/library.js`, `bus.js`, `app.css` | ✅ DONE |

---

## Verification

All current checks pass:
```bash
cd /Users/chiyak/hobby/mimiwatch
.venv/bin/python bench/check_all.py   # py_compile, bench/*_check.py, pytest
.venv/bin/ruff check .                # CI runs this too
```

**New tests added** (merged to main):
- `test_auto_detect_language_gets_translated` — Auto-detect language → translation flow (live)
- `test_trim_text_of_does_not_retranslate` — Translation queue re-translation of trimmed lines

**Still needed**:
- Clear all subtitles for live session (UX-06)
- Glossary term highlighting (UX-04)
- Pre-warm default models (PERF-01)

---

## Related Documentation

- `measurements/RESULTS.md` — Design decisions with measurements (sections cited above)
- `AGENTS.md` — Project rules and code map
- `docs/GUIDE.md` / `docs/GUIDE.ko.md` — User guide
- `docs/PIPELINE.md` — Data flow architecture

---

*Generated by code review on 2026-09-11. Updated after P0/P1 fixes merged.*