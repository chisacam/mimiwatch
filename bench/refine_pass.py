"""Reproduce section 49's "one line per whole group" refinement. It is a variant
the shipping path does not have.

The refinement that ships is `transcribe_vod.refine_cues`, and it **re-splits** the
re-decoded result along the runtime's span times. Section 49 is where measuring what
happens when a group goes out as a single line without the re-split led to choosing the
re-split, so that comparison has to stay runnable -- which is why the losing side is kept
here.

The rule that groups utterances is not copied; it is taken from the shipping code
(`vod.refine_groups`). If the two copies move independently, the comparison does not hold.
"""
from __future__ import annotations

import numpy as np

import stream
import transcribe_vod as vod

SR = stream.SAMPLE_RATE


def refine_merged(pcm: np.ndarray, spans: list[tuple[int, int]], texts: list[str],
                  asr) -> list[dict]:
    """One utterance group into one subtitle line. The fall-back rule is the same
    as in `refine_cues`."""
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
