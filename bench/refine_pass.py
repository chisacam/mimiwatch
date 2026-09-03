"""확정본 무리를 합쳐 다시 해독하는 정제 패스를, 벤치에서 오프라인으로 돌립니다.

라이브에는 `stream.Refiner`가 있지만 그것은 스레드와 30초 오디오 링 위에서
돌고 결과를 `sink.refine(text, ...)`로만 내놓습니다 -- 시각이 붙지 않아
채점할 수가 없습니다. 여기서는 오디오 전체가 이미 배열로 있으므로(녹화본이
바로 그 조건입니다) 무리의 시작·끝을 그대로 들고 동기로 돕니다.

무리를 닫는 규칙과 되돌림 규칙은 `stream.Refiner.maybe_refine`·`work`에서
그대로 가져왔습니다(`GROUP_GAP_S`, `GROUP_MAX_S`, `PREROLL_S`,
`REFINE_MIN_KEEP`). 상수를 베끼지 않고 stream 에서 읽어 오는 이유입니다 --
그쪽이 움직이면 이 측정도 함께 움직여야 합니다.
"""
from __future__ import annotations

import numpy as np

import stream

SR = stream.SAMPLE_RATE


def group_spans(spans: list[tuple[int, int]]) -> list[list[int]]:
    """확정 구간(표본 단위)을 정제 단위로 묶습니다. 값은 각 무리의 인덱스 목록.

    라이브는 「마지막 발화 뒤로 2초가 조용하면 무리가 끝났다」를 흘러가는
    시계로 판정합니다. 다 받아 둔 오디오에서는 같은 말이 「다음 발화가 2초
    뒤에 온다」가 됩니다. 쉬지 않고 말하면 25초에서 끊는 것은 같습니다.
    """
    gap = int(stream.GROUP_GAP_S * SR)
    mx = int(stream.GROUP_MAX_S * SR)
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, (start, end) in enumerate(spans):
        if cur:
            first_start = spans[cur[0]][0]
            prev_end = spans[cur[-1]][1]
            if start - prev_end >= gap or end - first_start >= mx:
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def refine(pcm: np.ndarray, spans: list[tuple[int, int]], texts: list[str], asr,
           on_group=None, split: bool = False) -> list[dict]:
    """무리마다 원본 오디오를 통째로 다시 해독해 자막 줄을 새로 냅니다.

    돌려주는 줄은 무리 하나에 하나입니다 -- 라이브에서도 정제본은 무리 하나가
    한 줄로 나옵니다. 되돌림(`REFINE_MIN_KEEP`)도 라이브와 같습니다: 다시
    해독한 것이 확정본을 이어 붙인 것보다 눈에 띄게 짧으면 무리째 버립니다.
    말을 삼킨 해독을 좋은 자막으로 바꿔치기하는 것이 가장 나쁩니다.

    `split`이면 런타임이 준 구간 시각으로 무리를 도로 여러 줄로 쪼갭니다.
    25초짜리 한 줄은 글자가 아무리 좋아도 자막으로 쓸 수 없습니다 --
    녹화본 자막은 시각 구간을 들고 플레이어가 그것으로 찾아갑니다.
    """
    out: list[dict] = []
    pre = int(stream.PREROLL_S * SR)
    for g in group_spans(spans):
        first_start, last_end = spans[g[0]][0], spans[g[-1]][1]
        buf = pcm[max(0, first_start - pre):last_end]
        fast_joined = " ".join(texts[i].strip() for i in g if texts[i].strip())
        if len(buf) < SR // 2:
            continue
        base = max(0, first_start - pre)
        got = asr.transcribe(buf, SR, speech_s=len(buf) / SR, live=False, segments=split)
        text = got["text"].strip()
        kept_fast = len(text) < stream.REFINE_MIN_KEEP * len(fast_joined)
        if kept_fast:
            text = fast_joined
        if not text.strip():
            continue
        lang = got.get("lang") or ""
        if split and not kept_fast and got.get("segments"):
            for sg in got["segments"]:
                start = base / SR + sg["start"]
                end = base / SR + sg["end"]
                # 선행 1초 안에서만 나온 줄은 앞 무리의 꼬리입니다. 그대로
                # 두면 같은 말이 두 무리에 겹쳐 실립니다.
                if end <= first_start / SR:
                    continue
                out.append({"start": round(max(start, base / SR), 2),
                            "end": round(end, 2), "text": sg["text"],
                            "lang": lang, "fast": ""})
        elif split and not kept_fast:
            # 시각을 달라고 했는데 오지 않았습니다(원격 전사기). 무리 한 줄로 둡니다.
            out.append({"start": round(first_start / SR, 2), "end": round(last_end / SR, 2),
                        "text": text, "lang": lang, "fast": fast_joined})
        elif split:
            # 되돌린 무리는 확정본 그대로가 낫습니다 -- 시각도 그때 것이 정확합니다.
            for i in g:
                if texts[i].strip():
                    out.append({"start": round(spans[i][0] / SR, 2),
                                "end": round(spans[i][1] / SR, 2),
                                "text": texts[i].strip(), "lang": lang, "fast": ""})
        else:
            out.append({"start": round(first_start / SR, 2),
                        "end": round(last_end / SR, 2),
                        "text": text,
                        "lang": lang,
                        "fast": fast_joined})
        if on_group:
            on_group(len(out), len(spans))
    return out
