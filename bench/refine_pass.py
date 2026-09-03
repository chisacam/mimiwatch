"""49절의 「무리째 한 줄」 정제를 재현합니다. 출하 경로에는 없는 변형입니다.

출하되는 정제는 `transcribe_vod.refine_cues` 이고, 그것은 다시 해독한 결과를
런타임의 구간 시각으로 **되쪼갭니다**. 49절은 되쪼개기 없이 무리 하나를 한 줄로
내보내면 어떻게 되는지를 재고 그 답으로 되쪼개기를 고른 자리이므로, 그 비교를
다시 돌릴 수 있어야 합니다 -- 그래서 진 쪽만 여기 남깁니다.

무리를 묶는 규칙은 베끼지 않고 출하 코드에서 가져다 씁니다(`vod.refine_groups`).
두 벌이 따로 움직이면 비교가 성립하지 않습니다.
"""
from __future__ import annotations

import numpy as np

import stream
import transcribe_vod as vod

SR = stream.SAMPLE_RATE


def refine_merged(pcm: np.ndarray, spans: list[tuple[int, int]], texts: list[str],
                  asr) -> list[dict]:
    """무리 하나를 자막 한 줄로. 되돌림 규칙은 `refine_cues` 와 같습니다."""
    out: list[dict] = []
    pre = int(stream.PREROLL_S * SR)
    for g in vod.refine_groups(spans):
        first_start, last_end = spans[g[0]][0], spans[g[-1]][1]
        buf = pcm[max(0, first_start - pre):last_end]
        fast_joined = " ".join(texts[i].strip() for i in g if texts[i].strip())
        if len(buf) < SR // 2:
            continue
        got = asr.transcribe(buf, SR, speech_s=len(buf) / SR, live=False)
        text = got["text"].strip()
        if len(text) < stream.REFINE_MIN_KEEP * len(fast_joined):
            text = fast_joined
        if not text.strip():
            continue
        out.append({"start": round(first_start / SR, 2),
                    "end": round(last_end / SR, 2), "text": text,
                    "lang": got.get("lang") or ""})
    return out
