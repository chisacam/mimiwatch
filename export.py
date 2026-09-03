"""Export the accumulated subtitles to a file.

VOD and live sit in the same table, so there is one path for reading them.
What they hold differs, though -- a VOD's ranges were actually measured and it
has `end`, while live has only the start time.

Live having no end time is structural -- what knows the moment an utterance
ends is the VAD, and the subtitle is finalised later than that and comes out
carrying only a start time. SRT/VTT demand an end time, so it is synthesized
here. The rules are in END_* below.
"""
from __future__ import annotations

import json

import store

# The rules for synthesizing a live subtitle's end time.
#
# It is held up until the next line starts. That leaves no gaps, and where
# speech is sparse the previous subtitle stays up long enough to read. Held
# without limit, though, the same line would sit there for minutes, so the top
# is capped -- even a solo broadcast always has stretches with no speech.
END_MAX_S = 6.0
# When the next line follows immediately, the result is a 0.2-second subtitle.
# It cannot be read, and some tools discard it outright. Even overlapping, it
# is held at least this long.
END_MIN_S = 0.8

FORMATS = ("srt", "vtt", "txt", "json")
VIEWS = ("both", "tr", "src")


def _ts(seconds: float, comma: bool = False) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clock(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _pick(translations: dict, backend: str) -> str:
    if not translations:
        return ""
    return translations.get(backend) or next(iter(translations.values()), "") or ""


def collect(value: str, backend: str = "") -> tuple[dict, list[dict]]:
    """`value` is the same thing the on-screen list uses: `live:<session>` for
    live, a video id for a VOD.

    `backend` is whose translation to put in. Left empty, live takes the engine
    that session used and a VOD takes one of the engines that has a
    translation -- for a VOD this choice used to be the **alphabetically last**
    entry in `backends_done`, so with both Gemma and M2M-100 present
    `local-m2m100` was picked. A translation other than the one being looked at
    on screen went out to the file.

    The rows returned are `{start, end, text, tr, speaker, kind}`.
    """
    meta, rows = (_collect_live(value[5:], backend) if value.startswith("live:")
                  else _collect_video(value, backend))
    _fill_ends(rows)
    return meta, rows


def _fill_ends(rows: list[dict]):
    """Fix up the lines with no end time, or with an end before the start.

    A live subtitle has no end time to begin with, and even in a VOD, pulling
    the start time earlier by hand leaves the old end time behind it and makes
    the range run backwards. With such a line in it, a tool either discards
    that subtitle or rejects the whole SRT/VTT file.

    There is one rule -- until the next line starts, capped top and bottom.
    """
    for i, r in enumerate(rows):
        if r["end"] > r["start"]:
            continue                      # an actually measured range. Left alone.
        nxt = rows[i + 1]["start"] if i + 1 < len(rows) else None
        span = (nxt - r["start"]) if nxt is not None else END_MAX_S
        r["end"] = r["start"] + max(END_MIN_S, min(END_MAX_S, span))


def _collect_live(session_id: str, backend: str = "") -> tuple[dict, list[dict]]:
    st = store.session(session_id)
    if not st:
        raise KeyError("no such session")
    backend = backend or st.get("backend") or ""
    cues = store.cues(session_id)
    # _fill_ends fills the end times. Live has no measured ranges, so every
    # line goes through that rule, but there must be only one place that
    # synthesizes them.
    rows = [{
        "start": float(c.get("t") or 0.0), "end": float(c.get("end") or 0.0),
        "text": (c.get("text") or "").strip(),
        "tr": _pick(c.get("translations") or {}, backend).strip(),
        "speaker": c.get("speaker") or "",
        "kind": c.get("kind") or "final",
    } for c in cues]
    meta = {"title": st.get("title") or session_id, "value": "live:" + session_id,
            "source_lang": st.get("source_lang") or "",
            "viewer_lang": st.get("viewer_lang") or "",
            "live": True, "url": st.get("url") or ""}
    return meta, rows


def _collect_video(video_id: str, backend: str = "") -> tuple[dict, list[dict]]:
    meta_doc = store.doc(video_id)
    if meta_doc is None:
        raise KeyError("no such video")
    cues = store.cues(video_id)
    # A VOD's ranges were actually measured, so they are used as they are.
    have = sorted({b for c in cues for b in c["translations"]})
    if backend not in have:
        # No translation from the chosen engine (or none chosen) -- one of
        # the ones there are.
        backend = (have or [""])[-1]
    rows = [{
        "start": float(c["t"] or 0.0),
        "end": float(c["end"] or 0.0),
        "text": (c["text"] or "").strip(),
        "tr": _pick(c["translations"], backend).strip(),
        "speaker": c["speaker"],
        "kind": c["kind"] or "final",
    } for c in cues]
    meta = {"title": meta_doc.get("title") or video_id, "value": video_id,
            "source_lang": meta_doc.get("source_lang") or "",
            "viewer_lang": meta_doc.get("viewer_lang") or "",
            "live": False, "url": meta_doc.get("url") or ""}
    return meta, rows


def _lines(row: dict, view: str) -> list[str]:
    """What to write on this line. The same axis as "Both / Translation only /
    Source only" on screen."""
    src, tr = row["text"], row["tr"]
    if view == "src":
        return [src] if src else []
    if view == "tr":
        # A line with no translation emits the source. Leaving it out would
        # make it as if that utterance had never happened, and the screen does
        # not do that either.
        return [tr or src] if (tr or src) else []
    out = [x for x in (src, tr) if x]
    return out


def render(meta: dict, rows: list[dict], fmt: str, view: str) -> tuple[bytes, str]:
    """Returns (body, MIME)."""
    if fmt not in FORMATS:
        raise ValueError(f"모르는 형식입니다: {fmt}")
    if view not in VIEWS:
        view = "both"

    if fmt == "json":
        body = json.dumps({"meta": meta, "view": view, "cues": rows},
                          ensure_ascii=False, indent=1)
        return body.encode("utf-8"), "application/json; charset=utf-8"

    if fmt == "txt":
        out = [f"# {meta['title']}"]
        if meta.get("url"):
            out.append(f"# {meta['url']}")
        out.append("")
        for r in rows:
            body = _lines(r, view)
            if not body:
                continue
            head = f"[{_clock(r['start'])}]"
            who = f" {r['speaker']}:" if r["speaker"] else ""
            out.append(f"{head}{who} {body[0]}")
            pad = " " * (len(head) + 1)
            out.extend(pad + b for b in body[1:])
        return ("\n".join(out) + "\n").encode("utf-8"), "text/plain; charset=utf-8"

    # SRT/VTT are subtitle tracks. A note we wrote ourselves
    # (「⋯ 못 받았습니다 ⋯」) is not an utterance, so it is left out here. It
    # stays in txt/json -- those are a record to read, and what was missed is
    # information there.
    speech = [r for r in rows if r["kind"] != "note"]
    comma = fmt == "srt"
    out = [] if comma else ["WEBVTT", ""]
    n = 0
    for r in speech:
        body = _lines(r, view)
        if not body:
            continue
        n += 1
        if comma:
            out.append(str(n))
        out.append(f"{_ts(r['start'], comma)} --> {_ts(r['end'], comma)}")
        out.extend(body)
        out.append("")
    mime = ("application/x-subrip; charset=utf-8" if comma
            else "text/vtt; charset=utf-8")
    return ("\n".join(out) + "\n").encode("utf-8"), mime


def filename(meta: dict, fmt: str) -> str:
    """The file name. Uses the title, filtering out the characters a file name
    cannot hold."""
    base = "".join(c for c in (meta.get("title") or "mimiwatch")
                   if c not in '\\/:*?"<>|\n\r\t').strip()
    base = " ".join(base.split())[:80] or "mimiwatch"
    return f"{base}.{fmt}"
