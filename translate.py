"""Translation layer: any source language -> the viewer's language.

hayamimi's own translator is hardwired to Japanese source (its
SOURCE_LANG_TOKEN is the module constant "__ja__"), because it was built for
transcribing Japanese broadcasts. mimiwatch's whole point is the opposite
direction -- whatever the speaker's language happens to be, render it in the
viewer's -- so the source token has to be a parameter, and the choice of
backend has to be swappable.

Two backends share one interface:

  LocalM2M   CTranslate2 M2M-100 418M, the model hayamimi already downloads.
             Measured at ~0.2s median on the 2026-08-27 live streams, so
             speed is not the reason to look elsewhere; quality is (it read
             社長 as "대통령" and ホロメン as "호르몬").
  OpenAICompatible  Any /v1/chat/completions endpoint: LM Studio, Ollama,
             a hosted API, a company gateway. One adapter covers all of them.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HAYAMIMI = os.environ.get("HAYAMIMI_DIR", "/Users/chiyak/hobby/hayamimi")
M2M_DIR = os.path.join(HAYAMIMI, "models", "mojicast-m2m100-ct2")

EOS_TOKEN = "</s>"
# Beam sizes hayamimi measured per target; anything else falls back to 2.
BEAM_SIZE_BY_TARGET = {"zh": 1, "ko": 2, "en": 2}
NO_REPEAT_NGRAM_SIZE = 3


# Broadcasts are 38-55% utterances of six characters or fewer ("先頭。",
# "ハハハハ", "네") against 0-3% for conference talks, so a per-line charge
# adds up fast on chatty content. Skipping them is therefore a COST control,
# not a quality one: `looks_broken` below already discards a mangled
# translation whatever its length, and a capable model handles fragments
# fine (Gemma 4 renders "やろっかー。" as "할까" where M2M-100 returns "⁇").
# Default 0 -- translate everything -- and let a metered endpoint opt in.
DEFAULT_MIN_CHARS = 0
BROKEN_MARKERS = ("⁇", "\ufffd")


def looks_broken(out: str, src_text: str) -> bool:
    """True when a translation should be discarded in favour of the source."""
    if not out or not out.strip():
        return True
    if any(m in out for m in BROKEN_MARKERS):
        return True
    # A hypothesis many times longer than its source is the classic M2M-100
    # repetition blow-up; showing it is worse than showing nothing new.
    return len(out) > max(40, len(src_text) * 6)


class Translator:
    """Interface both backends implement."""

    name = "base"
    min_chars = DEFAULT_MIN_CHARS

    def translate(self, text: str, src: str, tgt: str) -> str:
        raise NotImplementedError

    def should_translate(self, text: str, src: str, tgt: str) -> bool:
        """R3.8, plus the per-backend fragment threshold."""
        if src == tgt:
            return False
        stripped = (text or "").strip()
        if not stripped:
            return False
        return len(stripped) >= self.min_chars


class LocalM2M(Translator):
    """M2M-100 with the source language as a parameter rather than a constant."""

    name = "local-m2m100"

    def __init__(self, model_dir: str = M2M_DIR, device: str = "cpu",
                 compute_type: str = "int8"):
        import ctranslate2
        import sentencepiece as spm

        self._sp = spm.SentencePieceProcessor(
            model_file=os.path.join(model_dir, "sentencepiece.model"))
        self._tr = ctranslate2.Translator(model_dir, device=device,
                                          compute_type=compute_type)
        with open(os.path.join(model_dir, "shared_vocabulary.json"), encoding="utf-8") as f:
            self._vocab = set(json.load(f))

    def supports(self, lang: str) -> bool:
        return f"__{lang}__" in self._vocab

    def translate(self, text: str, src: str, tgt: str) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        if not (self.supports(src) and self.supports(tgt)):
            return text
        try:
            pieces = self._sp.encode(stripped, out_type=str)
            if not pieces:
                return text
            # Cap the decode length against the source: a degenerate
            # hypothesis otherwise runs to the model's maximum and stalls the
            # whole queue on one bad line.
            max_len = min(256, max(32, len(pieces) * 2 + 16))
            res = self._tr.translate_batch(
                [[f"__{src}__"] + pieces + [EOS_TOKEN]],
                target_prefix=[[f"__{tgt}__"]],
                beam_size=BEAM_SIZE_BY_TARGET.get(tgt, 2),
                no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                max_decoding_length=max_len,
            )
            out = res[0].hypotheses[0]
            if out and out[0] == f"__{tgt}__":
                out = out[1:]
            decoded = self._sp.decode(out).strip()
            if looks_broken(decoded, stripped):
                return text
            return decoded
        except Exception as exc:
            print(f"[translate] local backend failed: {exc}", file=sys.stderr)
            return text


class OpenAICompatible(Translator):
    """Any OpenAI-shaped /v1/chat/completions endpoint."""

    name = "openai-compatible"

    DEFAULT_PROMPT = (
        "Translate the {src} subtitle line below into {tgt}. "
        "Output only the translation, with no quotes, notes, or romanization. "
        "Keep proper nouns as they are written.\n\n{text}"
    )

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 prompt: str | None = None, timeout: float = 30.0,
                 no_reasoning: bool = True):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.prompt = prompt or self.DEFAULT_PROMPT
        self.timeout = timeout
        # Reasoning models spend their budget deliberating over a one-line
        # subtitle: Gemma 4 E4B burned 484 completion tokens and 9s on a
        # twelve-character line, against 8 tokens and 0.25s with thinking
        # off -- and the shorter answer was the better translation. Both
        # spellings are sent because servers disagree on which they honour;
        # one that knows neither ignores them.
        self.no_reasoning = no_reasoning

    def translate(self, text: str, src: str, tgt: str) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt.format(
                src=src, tgt=tgt, text=stripped)}],
            "temperature": 0.2,
        }
        if self.no_reasoning:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
            msg = data["choices"][0]["message"]
            out = (msg.get("content") or "").strip()
            if not out and msg.get("reasoning_content"):
                # The model spent its whole budget thinking and never
                # answered; treat it as a failure so the fallback runs.
                raise RuntimeError("model returned reasoning but no answer")
            if looks_broken(out, stripped):
                return text
            return out
        except Exception as exc:
            print(f"[translate] remote backend failed: {exc}", file=sys.stderr)
            raise


class WithFallback(Translator):
    """Try the remote backend, fall back to local when it errors out.

    R3.5: a translation endpoint going down must degrade the subtitles, not
    stop them. It must also not slow them to a crawl -- a broadcast can hold
    880 cues, and an endpoint that is simply not running would otherwise cost
    880 doomed requests and their timeouts. After a short run of consecutive
    failures the remote is considered down and skipped for the rest of the
    pass; one probe every `RETRY_AFTER` lines lets it come back.
    """

    name = "fallback"
    TRIP_AFTER = 3
    RETRY_AFTER = 50

    def __init__(self, primary: Translator, backup: Translator):
        self.primary, self.backup = primary, backup
        self.min_chars = primary.min_chars
        self.failures = 0
        self.consecutive = 0
        self.tripped = False
        self._since_trip = 0
        # Which backend produced the most recent line. A caller storing
        # results per backend must not file a fallback result under the
        # remote's name -- that would mark the remote "already translated"
        # with text it never produced, and the real comparison would never
        # happen.
        self.last_used = "primary"

    def translate(self, text: str, src: str, tgt: str) -> str:
        if self.tripped:
            self._since_trip += 1
            if self._since_trip < self.RETRY_AFTER:
                self.last_used = "backup"
                return self.backup.translate(text, src, tgt)
            self._since_trip = 0  # let one request through to test the water
        try:
            out = self.primary.translate(text, src, tgt)
            self.consecutive = 0
            self.tripped = False
            self.last_used = "primary"
            return out
        except Exception:
            self.failures += 1
            self.consecutive += 1
            if self.consecutive >= self.TRIP_AFTER and not self.tripped:
                self.tripped = True
                self._since_trip = 0
                print(f"[translate] remote backend unreachable after "
                      f"{self.consecutive} tries; using the local model",
                      file=sys.stderr)
            self.last_used = "backup"
            return self.backup.translate(text, src, tgt)


def build(spec: dict | None) -> Translator:
    """spec = {"backend": "local"} or
       {"backend": "openai", "base_url": ..., "model": ..., "api_key": ...}"""
    spec = spec or {}
    min_chars = int(spec.get("min_chars", DEFAULT_MIN_CHARS) or 0)
    if spec.get("backend") == "openai":
        remote = OpenAICompatible(spec["base_url"], spec["model"],
                                  spec.get("api_key", ""), spec.get("prompt"),
                                  no_reasoning=spec.get("no_reasoning", True))
        remote.min_chars = min_chars
        return WithFallback(remote, LocalM2M())
    local = LocalM2M()
    local.min_chars = min_chars
    return local
