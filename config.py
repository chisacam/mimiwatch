"""The one place that reads and writes the engine settings (`backends.json`).

`jobs.load_config()` existed, and yet `live.py` used to open and read the same
file directly in three places -- because `jobs` imports `live`, so it could not
be imported the other way round. That meant the work of bringing new engines in
from the example config (`_seed`) did not run on the live path, and a change to
the file's shape had four places to fix. This module imports no other module in
the project, so anyone can import it.

Writing is **atomic**. This file holds real endpoints and API keys, and a
half-written file left behind by dying mid-write blocks the whole next startup.
It is written to a temporary file and swapped in with `os.replace`.

Read-modify-write happens inside one lock. Saving two engines one after another
from the screen has two request threads changing the same file at once, and
without the lock the one that writes second erases what the first wrote.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading

import paths

BASE = paths.BASE
# `MIMIWATCH_CONFIG` can point at a different file (the tests use a temporary
# one). Running as a bundle (PyInstaller) it goes out into the user area,
# because the inside of a bundle is read-only.
CONFIG = paths.config_path()
# The example config travels with the program -- inside the repo, or inside the bundle.
EXAMPLE_CONFIG = os.path.join(BASE, "backends.example.json")

# The key names in the config. Translators are `backends`/`active`, transcribers
# are `asr_backends`/`asr_active`.
KINDS = {"tr": ("backends", "active"), "asr": ("asr_backends", "asr_active")}

# The default engines that cannot be deleted. Translation needs that entry
# because the fallback path (WithFallback) always keeps M2M-100 behind it, and
# for transcription, if it disappears from the list there is nothing left to
# pick. This must be the same value as the screen's (LOCKED in app.js) -- the
# server used to be protecting `local-hayamimi`, which no longer existed, so the
# default transcriber could be deleted through the API.
PROTECTED = {"tr": "local-m2m100", "asr": "tcpp-best"}

# UI languages the string table ships. The web front end and the extension both
# read this through /api/backends, so adding a language means adding it here and
# in web/app/i18n.js -- a code the table does not know renders as its key.
UI_LANGS = ("en", "ko")
DEFAULT_UI_LANG = "en"

_lock = threading.RLock()


def load() -> dict:
    """Reads the settings. On the first run it copies the example, and it brings in
    engines that have newly appeared in the example."""
    with _lock:
        if not os.path.exists(CONFIG) and os.path.exists(EXAMPLE_CONFIG):
            os.makedirs(os.path.dirname(CONFIG) or ".", exist_ok=True)
            shutil.copy(EXAMPLE_CONFIG, CONFIG)
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return _seed(cfg)


def save(cfg: dict):
    with _lock:
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG)


def _example() -> dict:
    try:
        with open(EXAMPLE_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# Labels we shipped before engine names were made language-neutral. A config
# copied from the example carries one of these verbatim, and _seed() only ever
# adds an engine -- it never renames one -- so an existing install would keep
# showing Korean names beside an English UI. Renaming only a label that still
# matches what we shipped leaves a label the user edited themselves alone.
_SHIPPED_LABELS_BEFORE_EN = {
    "local-m2m100": "M2M-100 (가벼움 · CPU · 기본)",
    "local-gemma": "Gemma 4 E4B (품질 · GPU 권장 · 4.9GB)",
    "tcpp-best": "Whisper large-v3-turbo (품질 · GPU 권장)",
    "tcpp-lite": "SenseVoice Small (가벼움 · CPU · 기본)",
    "tcpp-lite-en": "Moonshine base (가벼움 · 영어 전용)",
}


def _relabel(cfg: dict, example: dict) -> list[str]:
    """Give a shipped engine the name the example now uses. Returns the ids fixed."""
    fixed = []
    for key, _active in KINDS.values():
        want = {e["id"]: e.get("label") or "" for e in example.get(key, [])}
        for b in cfg.get(key, []):
            was = _SHIPPED_LABELS_BEFORE_EN.get(b.get("id"))
            if was and b.get("label") == was and want.get(b["id"]):
                b["label"] = want[b["id"]]
                fixed.append(b["id"])
    return fixed


def _seed(cfg: dict) -> dict:
    """Brings engines that have newly appeared in the example into the user's settings.

    backends.json is copied once on the first run and left alone after that --
    it holds real endpoints and API keys, so it cannot be overwritten. But then
    a default engine added later never reaches an existing user. A light
    transcriber was actually added once and nobody could see it.

    An id brought in once is written down in `seeded`. So an engine the user
    deleted does not come back to life, and **only what is genuinely new**
    comes in.

    A config meeting this code for the first time has no `seeded`. In that case
    whatever it holds right now counts as already seen, and what exists only in
    the example is brought in.
    """
    example = _example()
    if not example:
        return cfg
    seen = set(cfg.get("seeded") or [])
    added = []
    for key, _active in KINDS.values():
        have = {b["id"] for b in cfg.get(key, [])}
        seen |= have                      # What it holds now counts as already seen
        for entry in example.get(key, []):
            if entry["id"] in have or entry["id"] in seen:
                continue
            cfg.setdefault(key, []).append(dict(entry))
            seen.add(entry["id"])
            added.append(entry["id"])
    renamed = _relabel(cfg, example)
    if added or renamed or set(cfg.get("seeded") or []) != seen:
        cfg["seeded"] = sorted(seen)
        save(cfg)
    if added:
        print(f"[설정] 새 엔진을 들여왔습니다: {', '.join(added)}",
              file=sys.stderr, flush=True)
    return cfg


def entries(kind: str, cfg: dict | None = None) -> list[dict]:
    key, _ = KINDS[kind]
    return (cfg or load()).get(key, [])


def find(kind: str, engine_id: str, cfg: dict | None = None) -> dict | None:
    for b in entries(kind, cfg):
        if b["id"] == engine_id:
            return b
    return None


def find_backend(engine_id: str) -> dict | None:
    return find("tr", engine_id)


def find_asr(engine_id: str) -> dict | None:
    return find("asr", engine_id)


def active(kind: str, cfg: dict | None = None) -> str:
    """The id of the engine used as the default right now. If there is none, the
    default that cannot be deleted."""
    _, active_key = KINDS[kind]
    return (cfg or load()).get(active_key) or PROTECTED[kind]


def set_active(kind: str, engine_id: str) -> dict:
    """Changes which engine is used as the default. The first-time setup screen calls it."""
    key, active_key = KINDS[kind]
    with _lock:
        cfg = load()
        if not any(b["id"] == engine_id for b in cfg.get(key, [])):
            return {"error": f"'{engine_id}' 엔진이 없습니다"}
        cfg[active_key] = engine_id
        save(cfg)
        return cfg


def setup_done(cfg: dict | None = None) -> bool:
    """Has the first-time setup been finished? Config files written before this mark
    existed do not have it -- for those, having every model that is needed counts as
    finished (the screen decides)."""
    return bool((cfg or load()).get("setup_done"))


def mark_setup_done() -> dict:
    with _lock:
        cfg = load()
        cfg["setup_done"] = True
        save(cfg)
        return cfg


def ui_lang(cfg: dict | None = None) -> str:
    """Which language the UI draws itself in.

    Config files written before this setting existed have no such key, and the
    browser is the better guess for those -- the front end asks navigator.language
    when this comes back empty, so returning "" is meaningful and not an error.
    """
    code = str((cfg or load()).get("ui_lang") or "")
    return code if code in UI_LANGS else ""


def set_ui_lang(code: str) -> dict:
    with _lock:
        if code not in UI_LANGS:
            return {"error": f"'{code}' is not a UI language ({', '.join(UI_LANGS)})"}
        cfg = load()
        cfg["ui_lang"] = code
        save(cfg)
        return {"ui_lang": code}


def example_default(kind: str) -> str:
    """The example config's default active engine. Where to fall back to when the
    active engine has been deleted."""
    _, active_key = KINDS[kind]
    return _example().get(active_key) or PROTECTED[kind]


def upsert(kind: str, entry: dict) -> dict:
    """Puts in or swaps out one engine. The screen's "Engine management" calls it."""
    key, _ = KINDS[kind]
    with _lock:
        cfg = load()
        cfg[key] = [b for b in cfg.get(key, []) if b["id"] != entry["id"]]
        cfg[key].append(entry)
        save(cfg)
        return cfg


def delete(kind: str, engine_id: str) -> dict:
    """Deletes one engine from the settings. Translation and transcription follow the
    same rule.

    If what was deleted was the active engine, it falls back to the example
    config's default (when that one is still there). Translation used to fall
    back to M2M-100 unconditionally, but the example's default is Gemma -- the
    first session after a deletion suddenly dropped in quality.
    """
    key, active_key = KINDS[kind]
    protected = PROTECTED[kind]
    if engine_id == protected:
        return {"error": "기본 로컬 엔진은 삭제할 수 없습니다"}
    with _lock:
        cfg = load()
        before = cfg.get(key, [])
        kept = [b for b in before if b["id"] != engine_id]
        if len(kept) == len(before):
            return {"error": f"'{engine_id}' 엔진이 없습니다"}
        cfg[key] = kept
        if cfg.get(active_key) == engine_id:
            want = example_default(kind)
            cfg[active_key] = want if any(b["id"] == want for b in kept) else protected
        save(cfg)
        return cfg
