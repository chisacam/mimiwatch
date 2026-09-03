"""Translation layer: any source language -> the viewer's language.

hayamimi's own translator is hardwired to Japanese source (its
SOURCE_LANG_TOKEN is the module constant "__ja__"), because it was built for
transcribing Japanese broadcasts. mimiwatch's whole point is the opposite
direction -- whatever the speaker's language happens to be, render it in the
viewer's -- so the source token has to be a parameter, and the choice of
backend has to be swappable.

Three backends share one interface:

  LocalM2M   CTranslate2 M2M-100 418M, the model hayamimi already downloads.
             Measured at ~0.2s median on the 2026-08-27 live streams, so
             speed is not the reason to look elsewhere; quality is (it read
             社長 as "대통령" and ホロメン as "호르몬").
  OpenAICompatible  Any /v1/chat/completions endpoint: LM Studio, Ollama,
             a hosted API, a company gateway. One adapter covers all of them.
  LocalGemma Runs Gemma directly inside this process. The current default.

What actually decides translation quality was not the choice of backend but the
**prompt** (measurements/RESULTS.md sections 19-27). So this layer takes two
more things.

  genre    The character of the speech. It picks one set of prompts -- the
           speaker at a tech talk and the speaker on a game stream use
           different language. It is a property of the video being watched
           rather than of the backend, so it does not follow along when the
           engine changes.
  context  The few subtitle lines just before this one. One subtitle line on
           its own often does not fix the meaning (`束縛強め。` is "구속 강함"
           without context, "집착 강해" with it). A backend that does not use
           prompts ignores it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

import models
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


# ---------------------------------------------------------------------------
# Genre presets
#
# Swapping models in section 22 showed that the failures in subtitle
# translation came from **the character of the speech** rather than from model
# capacity. At a tech talk the transcriber mangles product names; on a game
# stream the speaker does not finish sentences. One set of prompts can hardly
# do both well.
#
# The presets were drawn backwards out of the failures observed.
#
#   tech    `Datadog` comes in as `Data Doc`, `Route 53` as `RAT fifty
#           three`. The translator is the only point that knows this line's
#           context, so restoring them is left to it.
#   gaming  Completing a fragment into a sentence was the worst failure. A
#           subject that is not there and an ending that is not there are
#           forbidden.
#   chat    Full of abbreviations and slang. What it does not know it
#           transliterates rather than invents.
#   music   In lyrics, imagery and word order come before grammar.

# The tail every preset shares. The output-format instruction has nothing to
# do with the genre, so it sits in one place, and `{context}` becomes an empty
# string when no context is attached.
_PROMPT_TAIL = ("Output only the translation, with no quotes, notes, or "
                "romanization.\n\n{context}{text}")

GENRE_PROMPTS = {
    "general": {
        "label_en": "General",
        "label_ko": "일반",
        "hint_en": ("When you do not know the genre, or it is mixed. This is "
                    "the prompt used so far."),
        "hint_ko": "장르를 모르거나 섞여 있을 때. 지금까지 쓰던 프롬프트입니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "Keep proper nouns as they are written. " + _PROMPT_TAIL),
    },
    "tech": {
        "label_en": "Tech talk · seminar",
        "label_ko": "기술 발표·세미나",
        "hint_en": ("Restores product and service names the transcriber mangled "
                    "to fit the context, and translates in written style."),
        "hint_ko": "전사기가 뭉갠 제품·서비스 이름을 문맥에 맞게 되살리고 문어체로 옮깁니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "This is a software or cloud engineering talk. "
                   "Product, service and API names are frequently garbled by "
                   "the speech recogniser; restore the spelling the context "
                   "calls for (for example a mangled cloud DNS service is "
                   "'Route 53'). Keep such names in Latin script. "
                   "Use written, declarative Korean. " + _PROMPT_TAIL),
    },
    "gaming": {
        "label_en": "Game stream",
        "label_ko": "게임 방송",
        "hint_en": ("Does not finish sentences that were left unfinished. "
                    "Leaves interjections as interjections."),
        "hint_ko": "끝나지 않은 말을 대신 끝내지 않습니다. 감탄사는 감탄사로 둡니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "This is a live game stream: the speaker is reacting in "
                   "real time, so lines are short, unfinished, and often just "
                   "an interjection. Translate exactly what is there. Do not "
                   "finish the sentence, do not add a subject the speaker did "
                   "not say, and do not turn a fragment into a full sentence. "
                   "Keep an interjection an interjection. Use casual spoken "
                   "Korean. Character and player names stay as names, but "
                   "ordinary words are always translated. " + _PROMPT_TAIL),
    },
    "chat": {
        "label_en": "Chat · variety",
        "label_ko": "잡담·버라이어티",
        "hint_en": "Transliterates slang and abbreviations instead of inventing a meaning.",
        "hint_ko": "은어와 줄임말은 뜻을 지어내지 않고 음차합니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "This is a streamer chatting with viewers: casual speech, "
                   "slang, in-jokes and clipped abbreviations. If a coined "
                   "word or abbreviation is not one you recognise, "
                   "transliterate it rather than guessing at a meaning. Do "
                   "not finish an unfinished sentence. Use casual spoken "
                   "Korean. " + _PROMPT_TAIL),
    },
    "music": {
        "label_en": "Song · lyrics",
        "label_ko": "노래·가사",
        "hint_en": ("Keeps the lyric's imagery and word order, and does not "
                    "insert a subject that is not there."),
        "hint_ko": "가사의 이미지와 어순을 지키고 없는 주어를 넣지 않습니다.",
        "prompt": ("Translate the {src} song lyric line below into {tgt}. "
                   "Lyrics are fragmentary by design: keep the line's imagery "
                   "and its word order where {tgt} allows, and do not supply "
                   "a subject or a conclusion the line does not have. "
                   + _PROMPT_TAIL),
    },
}

DEFAULT_GENRE = "general"

# Inserted before the source text when the preceding subtitles are passed
# along.
#
# The shape of this block decides a great deal. At first it said "for
# reference" once and simply left the target line after it, and on 4 lines out
# of 63 the model **translated the reference lines** instead of the target
# (`ここまで。` came out as "이번에 V스포 보컬 노래 방송으로요."). Putting the
# instruction once more immediately before the target line re-establishes the
# boundary.
CONTEXT_BLOCK = ("Context -- these {src} lines came before and are NOT to be "
                 "translated:\n{lines}\n\n"
                 "Now translate only this one {src} line:\n")
# How many lines to attach was decided by measuring 0, 1, 2, 3, 5 and 8
# (RESULTS.md section 28).
#
# Upwards, **from 5 lines the model stops translating.** `って。` comes back as
# `って`, untranslated. `looks_broken` does not catch this -- it is neither
# empty nor long, and there is no `⁇` either. Four runs out of four were the
# same, so it is not chance.
#
# Downwards, 1 line is not enough. The second sentence of
# `あなたはどうして生きてるの？百文字以内で答えよ。` disappeared wholesale.
#
# The difference between 2 and 3 is not large. A few lines come out right only
# at 3 (`うちに心奪われる` going from "집에서" to "우리에게"), which is why 3
# was chosen, but it is a thin difference out of a 63-line sample. **It is not
# grounds for calling it optimal.**
CONTEXT_LINES = 3


def genre_prompt(genre: str | None) -> str:
    return GENRE_PROMPTS.get(genre or DEFAULT_GENRE,
                             GENRE_PROMPTS[DEFAULT_GENRE])["prompt"]


def render_prompt(prompt: str, src: str, tgt: str, text: str,
                  context: list[str] | None = None) -> str:
    """A custom prompt without `{context}` still works as it is.

    str.format ignores leftover keywords, so a prompt of the old shape sitting
    in backends.json renders fine, just without the context.
    """
    block = ""
    if context:
        block = CONTEXT_BLOCK.format(
            src=src, lines="\n".join(f"- {c}" for c in context))
    return prompt.format(src=src, tgt=tgt, text=text, context=block)


class TranslationFailed(RuntimeError):
    """The translation result cannot be used.

    For a while this place returned the source text as it was. Then the caller
    cannot tell "the translation equals the source" from "the translation
    failed". In live the line was in fact neither stored nor published and
    disappeared wholesale, and the fallback backend never noticed a failure
    and so never once fired.
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

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        """`context` is the subtitles just before this line.

        A backend that does not use prompts ignores it -- M2M-100 has nowhere
        to take context.
        """
        raise NotImplementedError

    def should_translate(self, text: str, src: str, tgt: str) -> bool:
        """R3.8, plus the per-backend fragment threshold."""
        if src == tgt:
            return False
        stripped = (text or "").strip()
        if not stripped:
            return False
        return len(stripped) >= self.min_chars


class _M2MModel:
    """One copy of M2M-100. `models.shared` keeps just one per process."""

    def __init__(self, model_dir: str, device: str, compute_type: str):
        import ctranslate2
        import sentencepiece as spm

        self.sp = spm.SentencePieceProcessor(
            model_file=os.path.join(model_dir, "sentencepiece.model"))
        # CTranslate2's Translator is safe to use from several threads.
        self.tr = ctranslate2.Translator(model_dir, device=device,
                                         compute_type=compute_type)
        with open(os.path.join(model_dir, "shared_vocabulary.json"), encoding="utf-8") as f:
            self.vocab = set(json.load(f))


class LocalM2M(Translator):
    """M2M-100 with the source language as a parameter rather than a constant."""

    name = "local-m2m100"

    def __init__(self, model_dir: str = M2M_DIR, device: str = "cpu",
                 compute_type: str = "int8"):
        self._key = ("m2m100", model_dir, device, compute_type)
        m = models.shared(self._key, lambda: _M2MModel(model_dir, device, compute_type))
        self._sp, self._tr, self._vocab = m.sp, m.tr, m.vocab

    def supports(self, lang: str) -> bool:
        return f"__{lang}__" in self._vocab

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        # context is taken but not used. M2M-100 is a model that renders one
        # sentence as one sentence, so there is nowhere to attach the
        # preceding lines.
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        if not (self.supports(src) and self.supports(tgt)):
            # This language pair is unknown. Returning the source text
            # would have the caller mistake it for a translated line, so a
            # failure is reported instead.
            raise TranslationFailed(f"M2M-100이 {src}→{tgt}를 지원하지 않습니다")
        models.touch(self._key)
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
            # Turning a failure into the source text gives the caller no
            # way to know.
            raise TranslationFailed(f"M2M-100: {exc}") from exc


class OpenAICompatible(Translator):
    """Any OpenAI-shaped /v1/chat/completions endpoint."""

    name = "openai-compatible"

    # Moved into the genre table. The name stays -- it is better to be able
    # to read here what a backend that did not specify a prompt itself uses.
    DEFAULT_PROMPT = GENRE_PROMPTS[DEFAULT_GENRE]["prompt"]

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

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": render_prompt(
                self.prompt, src, tgt, stripped, context)}],
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


class _LlamaHolder:
    """One copy of Gemma and its lock. Loaded on the first request."""

    def __init__(self, model_path: str, device: str, n_ctx: int, threads: int,
                 n_gpu_layers: int):
        self.model_path, self.device = model_path, device
        self.n_ctx, self.threads, self.n_gpu_layers = n_ctx, threads, n_gpu_layers
        self.lock = threading.Lock()
        self.llm = None

    def get(self):
        with self.lock:
            if self.llm is not None:
                return self.llm
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(
                    f"Gemma 모델이 없습니다: {self.model_path}\n"
                    "「엔진 관리 › 모델·도구」에서 받거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
            from llama_cpp import Llama
            print(f"[translate] Gemma · {self.device} · {self.threads}스레드",
                  file=sys.stderr, flush=True)
            self.llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx,
                             n_threads=self.threads,
                             n_gpu_layers=self.n_gpu_layers, verbose=False)
            return self.llm


class LocalGemma(Translator):
    """Runs Gemma directly inside this process.

    Once the transcription model was served here, there was no reason to leave
    translation alone to another app. The same GGUF is used as it is, without a
    separate server like LM Studio. The OpenAICompatible path was left in
    place, so anyone who wants to run a larger model on another machine can use
    that.

    The prompt is the same one the remote path uses. If calling the same model
    down two paths gave different results, there would be no comparison to
    make.
    """

    name = "local-gemma"

    def __init__(self, model_path: str | None = None, n_ctx: int = 2048,
                 threads: int = 4, prompt: str | None = None,
                 max_tokens: int = 256, genre: str | None = None,
                 device: str = "auto"):
        import stream

        self.model_path = model_path or os.path.join(
            stream.model_dir(), "gemma-4-E4B_q4_0-it.gguf")
        # The settings may name only the file (the same rule as
        # `resolve_asr` on the transcription side). A model downloaded from
        # Hugging Face (modelhub) is registered that way.
        if not os.path.isabs(self.model_path) and not os.path.exists(self.model_path):
            self.model_path = os.path.join(stream.model_dir(), self.model_path)
        # With `cpu`, not one layer goes on the GPU. On a machine where
        # sharing with transcription is tight, such as integrated graphics,
        # running at least the translation on the CPU keeps the subtitles from
        # waiting on each other. Translation is 0.2s per line, so the CPU is
        # enough.
        self.device = (device or "auto").strip().lower()
        self.n_gpu_layers = 0 if self.device == "cpu" else -1
        # A prompt written into the backend itself takes precedence. The
        # genre applies only to a backend that left that place empty.
        self.prompt = prompt or genre_prompt(genre)
        self.max_tokens = max_tokens
        self._n_ctx = n_ctx
        self._threads = threads
        # There is one copy of the model per process (models.py). The prompt
        # (the genre) belongs to this object while the model is shared, so
        # sessions and jobs with different genres use the same Gemma. It is
        # loaded on the first translation -- loading it ahead of time would
        # delay the session start by that much.
        self._key = ("gemma", self.model_path, self.device, n_ctx, threads, self.n_gpu_layers)
        self._holder = models.shared(
            self._key,
            lambda: _LlamaHolder(self.model_path, self.device, n_ctx, threads,
                                 self.n_gpu_layers))
        # llama.cpp's context does not survive concurrent calls. A
        # translation thread comes up for every subtitle line, so they are
        # serialised through a lock that lives with the model.
        self._lock = self._holder.lock

    def _ensure(self):
        return self._holder.get()

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        llm = self._ensure()
        models.touch(self._key)
        msg = render_prompt(self.prompt, src, tgt, stripped, context)
        with self._lock:
            out = llm.create_chat_completion(
                messages=[{"role": "user", "content": msg}],
                temperature=0.2, max_tokens=self.max_tokens)
        answer = (out["choices"][0]["message"].get("content") or "").strip()
        # For a model that writes out its thinking, that part is stripped.
        # The remote path already confirmed that attaching reasoning to a
        # one-line subtitle only adds latency without improving the answer.
        if "</think>" in answer:
            answer = answer.rsplit("</think>", 1)[-1].strip()
        if looks_broken(answer, stripped):
            raise TranslationFailed(f"Gemma: {answer[:60]!r}")
        return answer


class Lazy(Translator):
    """Built only on the first call.

    The M2M-100 used as a fallback is needed only when the default engine
    fails, but it used to be built ahead of time in every `build()` -- which
    amounted to reading 473MB (once) every time a session was opened, and on a
    machine without the model file it fell over here even with Gemma perfectly
    fine.
    """

    name = "lazy"

    def __init__(self, factory):
        self._factory = factory
        self._inst: Translator | None = None
        self._lock = threading.Lock()

    def _get(self) -> Translator:
        with self._lock:
            if self._inst is None:
                self._inst = self._factory()
                self.name = self._inst.name
            return self._inst

    def translate(self, text, src, tgt, context=None):
        return self._get().translate(text, src, tgt, context)

    def should_translate(self, text, src, tgt):
        return self._get().should_translate(text, src, tgt)


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

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        if self.tripped:
            self._since_trip += 1
            if self._since_trip < self.RETRY_AFTER:
                self.last_used = "backup"
                return self.backup.translate(text, src, tgt, context)
            self._since_trip = 0  # let one request through to test the water
        try:
            out = self.primary.translate(text, src, tgt, context)
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
            return self.backup.translate(text, src, tgt, context)


def build(spec: dict | None, genre: str | None = None) -> Translator:
    """spec = {"backend": "local"} or
       {"backend": "openai", "base_url": ..., "model": ..., "api_key": ...}

    `genre` is a property of the video being watched, not of the backend. The
    same Gemma translates both a tech talk and a game stream, with only the
    prompt swapped.
    """
    spec = spec or {}
    min_chars = int(spec.get("min_chars", DEFAULT_MIN_CHARS) or 0)
    if spec.get("backend") == "gemma":
        device = (spec.get("device") or "auto").strip().lower()
        # Four threads is not enough on the CPU. The same rule as for
        # transcription.
        gemma = LocalGemma(spec.get("model_path"),
                           n_ctx=int(spec.get("n_ctx", 2048)),
                           threads=int(spec.get("threads")
                                       or stream.default_threads(device)),
                           prompt=spec.get("prompt"), genre=genre,
                           device=device)
        gemma.min_chars = min_chars
        # M2M-100 sits behind it so that a missing model file or a failed
        # load does not leave the subtitles as the source text. Which of the
        # two actually answered is recorded.
        return WithFallback(gemma, Lazy(LocalM2M))
    if spec.get("backend") == "openai":
        remote = OpenAICompatible(spec["base_url"], spec["model"],
                                  spec.get("api_key", ""),
                                  spec.get("prompt") or genre_prompt(genre),
                                  no_reasoning=spec.get("no_reasoning", True))
        remote.min_chars = min_chars
        return WithFallback(remote, Lazy(LocalM2M))
    local = LocalM2M()
    local.min_chars = min_chars
    return local
