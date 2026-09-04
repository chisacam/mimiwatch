"""Run the same model over the same sample, changing only the prompt.

`run.py` is the record of measurements across models, so it stays untouched.
The only thing that changes here is the prompt -- model, quantisation,
temperature and sample are all the same. The presets and the context block are
taken straight from `translate.py`, so whatever comes out well here is what
comes out of the app.

Four things are measured.

  generic      the prompt used so far, no context (baseline)
  preset       genre preset, no context
  generic-ctx  the prompt used so far + the previous 3 subtitle lines
  preset-ctx   genre preset + the previous 3 subtitle lines

Preset and context are measured separately because if both are turned on at
once and the result improves, there is no telling which of them did the work.
"""
from __future__ import annotations

import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stream, translate                                   # noqa: E402

TGT = "ko"
MODEL = "gemma-4-E4B_q4_0-it.gguf"

# Which genre fits each source of the sample. In the app this is what a person picks.
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

# `ctxN` (ctx0, ctx1, ctx5 and so on) turns the preset on and varies only the
# number of context lines. The number 3 was first set by judgement, so this
# measures how many lines is actually right.


def parse_mode(mode):
    """(whether to use the preset, number of context lines)"""
    if mode.startswith("ctx"):
        return True, int(mode[3:])
    use_preset, use_ctx = MODES[mode]
    return use_preset, (translate.CONTEXT_LINES if use_ctx else 0)


def main(mode, tag=""):
    """temperature is not 0, so one result is only one sample.
    `tag` runs the same condition several times and keeps each one apart."""
    use_preset, n_ctx = parse_mode(mode)
    with open(os.path.join(HERE, "sample.json"), encoding="utf-8") as f:
        items = json.load(f)

    # Loading 5GB again every time the prompt changes only makes the comparison
    # slower. One is loaded and only self.prompt is swapped per line -- LocalGemma
    # holds the prompt as an instance attribute, so this just works.
    tr = translate.LocalGemma(model_path=os.path.join(stream.model_dir(), MODEL))
    tr._ensure()

    rows = []
    for i, item in enumerate(items):
        genre = GENRE_BY_SOURCE[item["source"]] if use_preset else "general"
        tr.prompt = translate.genre_prompt(genre)
        # The sample carries up to 8 lines. Only as many as needed are taken from
        # the end -- a nearby line is worth more as context than a distant one.
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
