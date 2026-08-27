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
import threading
import urllib.error
import urllib.request

import stream

M2M_DIR = os.path.join(stream.model_dir(), "mojicast-m2m100-ct2")

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


class TranslationFailed(RuntimeError):
    """번역 결과를 쓸 수 없습니다.

    한동안 이 자리에서 원문을 그대로 돌려주었습니다. 그러면 부르는 쪽이
    "번역이 원문과 같다"와 "번역이 실패했다"를 구분할 수 없습니다. 실제로
    라이브에서는 그 줄이 저장도 발행도 되지 않아 통째로 사라졌고, 대체
    백엔드도 실패를 알아채지 못해 한 번도 발동하지 않았습니다.
    """


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
            # 이 언어쌍을 모릅니다. 원문을 돌려주면 부르는 쪽이 번역된 줄로
            # 오해하므로 실패로 알립니다.
            raise TranslationFailed(f"M2M-100이 {src}→{tgt}를 지원하지 않습니다")
        try:
            pieces = self._sp.encode(stripped, out_type=str)
            if not pieces:
                raise TranslationFailed("토큰이 나오지 않았습니다")
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
                raise TranslationFailed(f"M2M-100: {decoded[:60]!r}")
            return decoded
        except TranslationFailed:
            raise
        except Exception as exc:
            # 실패를 원문으로 바꿔 돌려주면 부르는 쪽이 알 길이 없습니다.
            raise TranslationFailed(f"M2M-100: {exc}") from exc


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
                raise TranslationFailed(f"{self.model}: {out[:60]!r}")
            return out
        except Exception as exc:
            print(f"[translate] remote backend failed: {exc}", file=sys.stderr)
            raise


class LocalGemma(Translator):
    """Gemma를 이 프로세스 안에서 직접 돌립니다.

    전사 모델을 스스로 서빙하게 되면서 번역만 다른 앱에 맡길 이유가
    없어졌습니다. LM Studio 같은 별도 서버 없이 같은 GGUF를 그대로 씁니다.
    OpenAICompatible 경로는 그대로 두었으므로, 더 큰 모델을 다른 기계에서
    돌리고 싶을 때는 그쪽을 쓰면 됩니다.

    프롬프트는 원격 경로와 같은 것을 씁니다. 같은 모델을 두 경로로 부를 때
    결과가 달라지면 비교가 성립하지 않습니다.
    """

    name = "local-gemma"

    def __init__(self, model_path: str | None = None, n_ctx: int = 2048,
                 threads: int = 4, prompt: str | None = None,
                 max_tokens: int = 256):
        import stream

        self.model_path = model_path or os.path.join(
            stream.model_dir(), "gemma-4-E4B_q4_0-it.gguf")
        self.prompt = prompt or OpenAICompatible.DEFAULT_PROMPT
        self.max_tokens = max_tokens
        self._n_ctx = n_ctx
        self._threads = threads
        self._llm = None
        # llama.cpp의 컨텍스트는 동시 호출을 견디지 못합니다. 자막 한 줄마다
        # 번역 스레드가 뜨므로 직렬화합니다.
        self._lock = threading.Lock()

    def _ensure(self):
        if self._llm is not None:
            return
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"Gemma 모델이 없습니다: {self.model_path}\n"
                "./install.sh 를 실행하거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
        from llama_cpp import Llama
        # 4GB짜리를 세션 시작마다 올리면 첫 자막이 그만큼 늦습니다. 처음
        # 번역할 때 올리고 그 뒤로는 재사용합니다.
        self._llm = Llama(model_path=self.model_path, n_ctx=self._n_ctx,
                          n_threads=self._threads, n_gpu_layers=-1,
                          verbose=False)

    def translate(self, text: str, src: str, tgt: str) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        self._ensure()
        msg = self.prompt.format(src=src, tgt=tgt, text=stripped)
        with self._lock:
            out = self._llm.create_chat_completion(
                messages=[{"role": "user", "content": msg}],
                temperature=0.2, max_tokens=self.max_tokens)
        answer = (out["choices"][0]["message"].get("content") or "").strip()
        # 생각을 적고 나오는 모델이면 그 부분을 걷어냅니다. 자막 한 줄에
        # 추론을 붙이면 지연만 늘고 답은 나아지지 않는다는 것을 원격 경로에서
        # 이미 확인했습니다.
        if "</think>" in answer:
            answer = answer.rsplit("</think>", 1)[-1].strip()
        if looks_broken(answer, stripped):
            raise TranslationFailed(f"Gemma: {answer[:60]!r}")
        return answer


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
    if spec.get("backend") == "gemma":
        gemma = LocalGemma(spec.get("model_path"),
                           n_ctx=int(spec.get("n_ctx", 2048)),
                           threads=int(spec.get("threads", 4)),
                           prompt=spec.get("prompt"))
        gemma.min_chars = min_chars
        # 모델 파일이 없거나 적재가 실패해도 자막이 원문으로 남지는 않도록
        # M2M-100을 뒤에 둡니다. 어느 쪽이 실제로 답했는지는 기록됩니다.
        return WithFallback(gemma, LocalM2M())
    if spec.get("backend") == "openai":
        remote = OpenAICompatible(spec["base_url"], spec["model"],
                                  spec.get("api_key", ""), spec.get("prompt"),
                                  no_reasoning=spec.get("no_reasoning", True))
        remote.min_chars = min_chars
        return WithFallback(remote, LocalM2M())
    local = LocalM2M()
    local.min_chars = min_chars
    return local
