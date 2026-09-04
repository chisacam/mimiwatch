"""Run one translator over the sample and record what it said, and how long.

Two models are compared:

  gemma4      google/gemma-4-E4B-it-qat-q4_0 (5.15GB) -- what mimiwatch ships
              today, driven through translate.LocalGemma so the measurement
              is of the production path, prompt and all.
  translategemma  google/translategemma-4b-it Q4_K_M (2.5GB) -- a Gemma 3 4B
              fine-tuned by Google Translate for 55 languages. It refuses the
              general instruction prompt: its chat template takes the source
              and target language as fields, not as English prose, so it is
              driven through that template instead.

One model per process. Both are multi-gigabyte and holding them at once on
a laptop turns the latency numbers into a memory-pressure measurement.
"""
from __future__ import annotations

import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stream  # noqa: E402

TGT = "ko"
MODELS = {
    "gemma4": "gemma-4-E4B_q4_0-it.gguf",
    "translategemma": "translategemma-4b-it-Q4_K_M.gguf",
    # The same weights driven by mimiwatch's existing English instruction
    # prompt instead of the official template. If this arm holds up, adopting
    # the model costs one filename; if it does not, the template has to be
    # plumbed through translate.py, and that is worth knowing before deciding.
    "translategemma-plain": "translategemma-4b-it-Q4_K_M.gguf",
}


def load_sample():
    with open(os.path.join(HERE, "sample.json"), encoding="utf-8") as f:
        return json.load(f)


class Gemma4Runner:
    """The shipping path, unmodified."""

    def __init__(self, path):
        import translate
        self.t = translate.LocalGemma(model_path=path)
        self.t._ensure()

    def __call__(self, text, src):
        return self.t.translate(text, src, TGT)


class TranslateGemmaRunner:
    """The official chat template, rendered from the GGUF's own copy.

    Writing the template out by hand would risk measuring a prompt Google
    never intended; the GGUF carries the real one, so it is applied with
    jinja2 exactly as transformers would.
    """

    def __init__(self, path):
        from llama_cpp import Llama
        self.llm = Llama(model_path=path, n_ctx=2048, n_threads=4,
                         n_gpu_layers=-1, verbose=False)
        tpl = self.llm.metadata.get("tokenizer.chat_template")
        if not tpl:
            raise RuntimeError("this GGUF has no chat_template")
        import jinja2

        def raise_exception(msg):            # transformers' template helper
            raise ValueError(msg)

        env = jinja2.Environment()
        env.globals["raise_exception"] = raise_exception
        self.tpl = env.from_string(tpl)

    def render(self, text, src):
        return self.tpl.render(
            messages=[{"role": "user", "content": [{
                "type": "text", "source_lang_code": src,
                "target_lang_code": TGT, "text": text}]}],
            # llama.cpp prepends BOS itself; letting the template add a
            # second one measurably degrades the answer.
            add_generation_prompt=True, bos_token="")

    def __call__(self, text, src):
        out = self.llm.create_completion(
            self.render(text, src), max_tokens=256, temperature=0.0,
            stop=["<end_of_turn>", "<eos>"])
        return (out["choices"][0]["text"] or "").strip()


class PlainPromptRunner:
    """Any GGUF driven through the prompt translate.py sends today."""

    def __init__(self, path):
        import translate
        self.t = translate.LocalGemma(model_path=path)
        self.t._ensure()

    def __call__(self, text, src):
        return self.t.translate(text, src, TGT)


def main(which):
    path = os.path.join(stream.model_dir(), MODELS[which])
    if not os.path.exists(path):
        sys.exit(f"no model: {path}")
    t0 = time.time()
    runner = {"gemma4": Gemma4Runner,
              "translategemma": TranslateGemmaRunner,
              "translategemma-plain": PlainPromptRunner}[which](path)
    load_s = time.time() - t0
    print(f"[{which}] loaded in {load_s:.1f}s", file=sys.stderr)

    rows = []
    for i, item in enumerate(load_sample()):
        t = time.time()
        try:
            out, err = runner(item["text"], item["src_lang"]), None
        except Exception as exc:            # a refusal is a result, not a crash
            out, err = "", f"{type(exc).__name__}: {exc}"
        rows.append({**item, "out": out, "error": err,
                     "seconds": round(time.time() - t, 3)})
        print(f"  {i+1:>2} {rows[-1]['seconds']:>5.2f}s  "
              f"{item['text'][:28]:<30} -> {(out or err)[:40]}", file=sys.stderr)

    dst = os.path.join(HERE, f"out_{which}.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"model": which, "file": MODELS[which],
                   "load_seconds": round(load_s, 1), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print(f"-> {dst}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
