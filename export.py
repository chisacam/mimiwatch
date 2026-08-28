"""쌓인 자막을 파일로 내보냅니다.

녹화본과 라이브가 같은 표에 있으므로 읽는 길은 하나입니다. 다만 담고 있는
것이 다릅니다 -- 녹화본은 구간을 실제로 재어 `end` 가 있고, 라이브는 시작
시각뿐입니다.

라이브에 끝 시각이 없는 것은 구조상 그렇습니다 -- 발화가 끝나는 순간을
아는 것은 VAD 이고, 자막은 그보다 늦게 확정되어 시작 시각만 달고 나옵니다.
SRT/VTT 는 끝 시각을 요구하므로 여기서 지어 줍니다. 규칙은 아래 END_* 에
있습니다.
"""
from __future__ import annotations

import json

import store

# 라이브 자막의 끝 시각을 짓는 규칙.
#
# 다음 줄이 시작할 때까지 띄워 둡니다. 그러면 빈틈이 없고, 말이 뜸한 구간에서
# 직전 자막이 남아 있어 읽을 시간이 생깁니다. 다만 한없이 남기면 몇 분째 같은
# 줄이 붙어 있게 되므로 위를 막습니다 -- 혼자 하는 방송에도 말이 비는 구간은
# 늘 있습니다.
END_MAX_S = 6.0
# 다음 줄이 바로 뒤따라오면 0.2초짜리 자막이 나옵니다. 읽을 수 없고 도구에
# 따라서는 아예 버립니다. 겹치더라도 이만큼은 띄웁니다.
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


def collect(value: str) -> tuple[dict, list[dict]]:
    """`value` 는 화면의 목록이 쓰는 것과 같습니다: 라이브는 `live:<세션>`,
    녹화본은 영상 id.

    돌려주는 줄은 `{start, end, text, tr, speaker, kind}` 입니다.
    """
    meta, rows = (_collect_live(value[5:]) if value.startswith("live:")
                  else _collect_video(value))
    _fill_ends(rows)
    return meta, rows


def _fill_ends(rows: list[dict]):
    """끝 시각이 없거나 시작보다 앞선 줄을 손봅니다.

    라이브 자막에는 애초에 끝 시각이 없고, 녹화본이라도 사람이 시작 시각을
    앞으로 당기면 옛 끝 시각이 뒤에 남아 거꾸로 된 구간이 됩니다. SRT/VTT 에
    그런 줄이 들어가면 도구가 그 자막을 버리거나 파일 전체를 거부합니다.

    규칙은 하나입니다 -- 다음 줄이 시작할 때까지, 위아래를 막아서.
    """
    for i, r in enumerate(rows):
        if r["end"] > r["start"]:
            continue                      # 실제로 잰 구간입니다. 그대로 둡니다.
        nxt = rows[i + 1]["start"] if i + 1 < len(rows) else None
        span = (nxt - r["start"]) if nxt is not None else END_MAX_S
        r["end"] = r["start"] + max(END_MIN_S, min(END_MAX_S, span))


def _collect_live(session_id: str) -> tuple[dict, list[dict]]:
    st = store.session(session_id)
    if not st:
        raise KeyError("no such session")
    backend = st.get("backend") or ""
    cues = store.cues(session_id)
    # 끝 시각은 _fill_ends 가 채웁니다. 라이브에는 잰 구간이 없으므로 전부
    # 그 규칙을 타지만, 짓는 자리는 한 군데여야 합니다.
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


def _collect_video(video_id: str) -> tuple[dict, list[dict]]:
    meta_doc = store.doc(video_id)
    if meta_doc is None:
        raise KeyError("no such video")
    cues = store.cues(video_id)
    # 녹화본은 구간을 실제로 재어 두었으므로 그대로 씁니다.
    backend = (meta_doc.get("backends_done")
               or sorted({b for c in cues for b in c["translations"]}) or [""])[-1]
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
    """이 줄에 무엇을 적을지. 화면의 「둘 다 / 번역만 / 원문만」과 같은 축입니다."""
    src, tr = row["text"], row["tr"]
    if view == "src":
        return [src] if src else []
    if view == "tr":
        # 번역이 없는 줄은 원문을 냅니다. 빼 버리면 그 발화가 아예 없었던
        # 것이 되는데, 화면에서도 그렇게 하지 않습니다.
        return [tr or src] if (tr or src) else []
    out = [x for x in (src, tr) if x]
    return out


def render(meta: dict, rows: list[dict], fmt: str, view: str) -> tuple[bytes, str]:
    """(내용, MIME) 을 돌려줍니다."""
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

    # SRT/VTT 는 자막 트랙입니다. 우리가 적은 안내(「⋯ 못 받았습니다 ⋯」)는
    # 발화가 아니므로 여기서는 뺍니다. txt/json 에는 남습니다 -- 그쪽은
    # 읽는 기록이라 무엇을 놓쳤는지가 정보입니다.
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
    """파일 이름. 제목을 쓰되 파일 이름이 될 수 없는 글자는 걸러 냅니다."""
    base = "".join(c for c in (meta.get("title") or "mimiwatch")
                   if c not in '\\/:*?"<>|\n\r\t').strip()
    base = " ".join(base.split())[:80] or "mimiwatch"
    return f"{base}.{fmt}"
