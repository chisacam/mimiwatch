"""같은 모델·같은 표본에 프롬프트만 바꿔 가며 돌립니다.

`run.py`는 모델을 바꿔 재는 기록이므로 건드리지 않습니다. 여기서 바뀌는
것은 프롬프트뿐이고 모델·양자화·temperature·표본은 모두 같습니다. 프리셋과
문맥 블록은 `translate.py`의 것을 그대로 쓰므로, 여기서 좋게 나온 것이
그대로 앱에서 나오는 것입니다.

네 가지를 잽니다.

  generic      지금까지의 프롬프트, 문맥 없음 (기준선)
  preset       장르 프리셋, 문맥 없음
  generic-ctx  지금까지의 프롬프트 + 직전 자막 3줄
  preset-ctx   장르 프리셋 + 직전 자막 3줄

프리셋과 문맥을 따로 재는 이유는, 둘을 한꺼번에 켜서 좋아지면 어느 쪽이
일했는지 알 수 없기 때문입니다.
"""
from __future__ import annotations

import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stream, translate                                   # noqa: E402

TGT = "ko"
MODEL = "gemma-4-E4B_q4_0-it.gguf"

# 표본의 출처마다 어느 장르가 맞는지. 앱에서는 사람이 고르는 값입니다.
GENRE_BY_SOURCE = {
    "MPSTWzF2ZKU.json": "gaming",
    "71SnC4H-G1Q.json": "music",
    "jrLVa1Md4GU.json": "tech",
    "RESULTS.md": "gaming",
}

MODES = {
    "generic":     (False, False),
    "preset":      (True,  False),
    "generic-ctx": (False, True),
    "preset-ctx":  (True,  True),
}

# `ctxN` (예: ctx0, ctx1, ctx5)은 프리셋을 켜고 문맥 줄 수만 바꿉니다.
# 3이라는 수는 처음에 판단으로 정한 것이라, 실제로 몇 줄이 맞는지 잽니다.


def parse_mode(mode):
    """(프리셋 사용 여부, 문맥 줄 수)"""
    if mode.startswith("ctx"):
        return True, int(mode[3:])
    use_preset, use_ctx = MODES[mode]
    return use_preset, (translate.CONTEXT_LINES if use_ctx else 0)


def main(mode, tag=""):
    """temperature가 0이 아니므로 한 번의 결과는 표본 하나일 뿐입니다.
    `tag`로 같은 조건을 여러 번 돌려 따로 남깁니다."""
    use_preset, n_ctx = parse_mode(mode)
    with open(os.path.join(HERE, "sample.json"), encoding="utf-8") as f:
        items = json.load(f)

    # 프롬프트가 바뀔 때마다 5GB를 다시 올리면 비교가 느려지기만 합니다.
    # 하나를 올려 두고 줄마다 self.prompt만 갈아 끼웁니다 -- LocalGemma가
    # 프롬프트를 인스턴스 속성으로 들고 있어 그대로 됩니다.
    tr = translate.LocalGemma(model_path=os.path.join(stream.model_dir(), MODEL))
    tr._ensure()

    rows = []
    for i, item in enumerate(items):
        genre = GENRE_BY_SOURCE[item["source"]] if use_preset else "general"
        tr.prompt = translate.genre_prompt(genre)
        # 표본은 8줄까지 담고 있습니다. 뒤에서부터 필요한 만큼만 씁니다 --
        # 가까운 줄이 먼 줄보다 문맥으로서 값어치가 큽니다.
        ctx = (item.get("context") or [])[-n_ctx:] if n_ctx else None
        t = time.time()
        try:
            out, err = tr.translate(item["text"], item["src_lang"], TGT, ctx), None
        except Exception as exc:
            out, err = "", f"{type(exc).__name__}: {exc}"
        rows.append({**item, "genre": genre, "n_context": len(ctx or []),
                     "out": out, "error": err,
                     "seconds": round(time.time() - t, 3)})
        print(f"  {i+1:>2} [{genre:<7}] {rows[-1]['seconds']:>5.2f}s  "
              f"{item['text'][:22]:<24} -> {(out or err)[:46]}", file=sys.stderr)

    dst = os.path.join(HERE, f"out_prompt_{mode}{tag}.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "model": MODEL, "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    print(f"-> {dst}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
