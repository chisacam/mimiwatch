"""Check that exported subtitles keep to their format.

A live cue has no end time. What knows the moment an utterance ends is the VAD,
and the cue is finalised later than that carrying only a start time. SRT/VTT
demand an end time, so export.py invents one, and when that rule goes wrong a
tool either drops the subtitles wholesale or leaves one line on screen for
minutes at a time.

    .venv/bin/python bench/export_check.py

It does not touch the store -- it only reads.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import export                                                # noqa: E402
import store                                                 # noqa: E402

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def secs(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = re.split("[,.]", rest)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def fake_rows():
    """Lines picked to hit exactly the places where the rules bite."""
    return [
        {"t": 0.0, "text": "첫 줄", "kind": "final", "translations": {"b": "first"}},
        # The next line 0.3 s later: the minimum length bites
        {"t": 0.3, "text": "바로 다음", "kind": "final", "translations": {"b": "next"}},
        # 60 s of silence: the maximum length bites
        {"t": 60.3, "text": "한참 뒤", "kind": "final", "translations": {}},
        {"t": 61.0, "kind": "note", "text": "⋯ 못 받았습니다 ⋯", "translations": {}},
        {"t": 62.0, "text": "마지막", "kind": "final", "translations": {"b": "last"}},
    ]


def main():
    print("\n[1] 끝 시각 규칙 (라이브)")
    rows = []
    cues = fake_rows()
    for i, c in enumerate(cues):
        start = c["t"]
        nxt = cues[i + 1]["t"] if i + 1 < len(cues) else None
        span = (nxt - start) if nxt is not None else export.END_MAX_S
        span = max(export.END_MIN_S, min(export.END_MAX_S, span))
        rows.append({"start": start, "end": start + span, "text": c["text"],
                     "tr": next(iter(c["translations"].values()), ""),
                     "speaker": "", "kind": c["kind"]})
    d = [r["end"] - r["start"] for r in rows]
    check(abs(d[0] - export.END_MIN_S) < 1e-6,
          f"바짝 붙은 줄은 최소 {export.END_MIN_S}초 ({d[0]:.2f})")
    # The 60 s jump is between the second line (0.3 s) and the third (60.3 s).
    check(abs(d[1] - export.END_MAX_S) < 1e-6,
          f"오래 비면 최대 {export.END_MAX_S}초 ({d[1]:.2f})")
    # A note follows 0.7 s after the third line, so the minimum length bites.
    check(abs(d[2] - export.END_MIN_S) < 1e-6,
          f"안내가 바로 뒤따라도 최소 길이 ({d[2]:.2f})")
    check(abs(d[4] - export.END_MAX_S) < 1e-6, f"마지막 줄도 상한 ({d[4]:.2f})")
    check(all(x > 0 for x in d), "길이가 모두 양수다")

    meta = {"title": "시험 / 제목: 살아있나?", "url": "", "value": "x",
            "source_lang": "ja", "viewer_lang": "ko", "live": True}

    print("\n[2] SRT")
    body, mime = export.render(meta, rows, "srt", "both")
    txt = body.decode()
    check("subrip" in mime, f"MIME ({mime})")
    blocks = [b for b in txt.strip().split("\n\n") if b.strip()]
    check(len(blocks) == 4, f"안내(note)는 빠지고 4덩어리 ({len(blocks)})")
    check("못 받았습니다" not in txt, "안내가 자막 트랙에 없다")
    nums = [int(b.split("\n")[0]) for b in blocks]
    check(nums == [1, 2, 3, 4], f"번호가 1부터 이어진다 ({nums})")
    arrows = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", txt)
    check(len(arrows) == 4, f"시각 줄 {len(arrows)}개")
    check(all(secs(b) > secs(a) for a, b in arrows), "끝이 늘 시작보다 뒤")
    check(all(secs(arrows[i][0]) <= secs(arrows[i + 1][0]) for i in range(3)),
          "시작 시각이 오름차순")
    check(txt.startswith("1\n00:00:00,000 --> 00:00:00,800"),
          "첫 덩어리 모양: " + txt.split("\n\n")[0].replace("\n", " | "))
    check("첫 줄\nfirst" in txt, "둘 다 보기는 원문 다음 번역")

    print("\n[3] VTT")
    body, mime = export.render(meta, rows, "vtt", "both")
    txt = body.decode()
    check(txt.startswith("WEBVTT\n"), "WEBVTT 로 시작")
    check("text/vtt" in mime, f"MIME ({mime})")
    check(re.search(r"\d\d:\d\d:\d\d\.\d\d\d --> ", txt) is not None,
          "밀리초 구분자가 점")
    check("," not in re.findall(r"[\d:,.]+ --> [\d:,.]+", txt)[0],
          "시각에 쉼표가 없다")

    print("\n[4] 보기 (both / tr / src)")
    src = export.render(meta, rows, "srt", "src")[0].decode()
    tr = export.render(meta, rows, "srt", "tr")[0].decode()
    check("first" not in src and "첫 줄" in src, "원문만: 번역이 없다")
    check("첫 줄" not in tr and "first" in tr, "번역만: 원문이 없다")
    check("한참 뒤" in tr, "번역이 없는 줄은 번역만 보기에서도 원문으로 남는다")

    print("\n[5] TXT / JSON")
    txt = export.render(meta, rows, "txt", "both")[0].decode()
    check(txt.startswith("# 시험 / 제목: 살아있나?"), "제목 머리말")
    check("[00:00] 첫 줄" in txt, "타임스탬프가 붙는다")
    check("못 받았습니다" in txt, "안내는 읽는 기록에 남는다")
    js = export.render(meta, rows, "json", "both")[0].decode()
    import json as _j
    d = _j.loads(js)
    check(len(d["cues"]) == 5, f"json 은 안내까지 {len(d['cues'])}줄")
    check(d["meta"]["title"] == meta["title"], "json 에 메타가 있다")

    print("\n[6] 파일 이름")
    n = export.filename(meta, "srt")
    check(not set(n) & set('\\/:*?"<>|'), f"금지 글자가 없다 ({n})")
    check(n.endswith(".srt"), "확장자")
    check(export.filename({"title": ""}, "vtt") == "mimiwatch.vtt", "제목이 비면 기본값")
    long = export.filename({"title": "가" * 300}, "txt")
    check(len(long) <= 84, f"너무 길지 않다 ({len(long)}자)")

    print("\n[7] 진짜 자막으로")
    sid = None
    for row in store.sessions(50):
        if row.get("cues"):
            sid = row["id"]; break
    if not sid:
        print("  ----  저장된 라이브 세션이 없어 건너뜁니다")
    else:
        meta2, rows2 = export.collect("live:" + sid)
        check(len(rows2) > 0, f"{meta2['title'][:24]!r} 에서 {len(rows2)}줄")
        out = export.render(meta2, rows2, "srt", "both")[0].decode()
        pairs = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", out)
        check(all(secs(b) > secs(a) for a, b in pairs), "끝이 늘 시작보다 뒤")
        over = sum(1 for i in range(len(pairs) - 1)
                   if secs(pairs[i][1]) > secs(pairs[i + 1][0]) + 1e-6)
        check(over <= len(pairs) * 0.2,
              f"겹치는 자막이 드물다 ({over}/{len(pairs)})")
        longest = max((secs(b) - secs(a)) for a, b in pairs)
        check(longest <= export.END_MAX_S + 1e-6,
              f"가장 긴 자막이 상한 안 ({longest:.1f}초)")

    print("\n[8] 없는 것")
    for bad in ("live:nope", "not-a-video"):
        try:
            export.collect(bad); check(False, f"{bad} 는 거절해야 한다")
        except KeyError:
            check(True, f"{bad} 를 거절한다")
    try:
        export.render(meta, rows, "docx", "both"); check(False, "모르는 형식 거절")
    except ValueError:
        check(True, "모르는 형식을 거절한다")

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
