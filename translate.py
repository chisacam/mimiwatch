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
  LocalGemma Gemma를 이 프로세스 안에서 직접 돌립니다. 지금의 기본값입니다.

번역 품질을 실제로 가르는 것은 백엔드 선택이 아니라 **프롬프트**였습니다
(measurements/RESULTS.md 19~27절). 그래서 이 계층은 두 가지를 더 받습니다.

  genre    발화의 성격. 프롬프트 한 벌을 고릅니다 -- 기술 발표의 화자와
           게임 방송의 화자는 쓰는 말이 다릅니다. 백엔드가 아니라 보고 있는
           영상의 속성이므로, 엔진을 바꿔도 따라오지 않습니다.
  context  이 줄 직전의 자막 몇 줄. 자막 한 줄만으로는 뜻이 정해지지 않는
           경우가 많습니다(`束縛強め。`가 문맥 없이는 "구속 강함",
           있으면 "집착 강해"). 프롬프트를 쓰지 않는 백엔드는 무시합니다.
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
# 장르 프리셋
#
# 22절에서 모델을 바꿔 본 결과, 자막 번역의 실패는 모델 용량보다 **발화의
# 성격**에서 왔습니다. 기술 발표에서는 전사기가 제품명을 뭉개고, 게임
# 방송에서는 화자가 문장을 끝내지 않습니다. 한 벌의 프롬프트로 둘 다
# 잘하기는 어렵습니다.
#
# 프리셋은 관찰한 실패에서 거꾸로 끌어냈습니다.
#
#   tech    `Datadog`이 `Data Doc`으로, `Route 53`이 `RAT fifty three`로
#           들어옵니다. 번역기는 이 줄의 문맥을 아는 유일한 지점이므로
#           복원을 맡깁니다.
#   gaming  파편을 문장으로 완성하는 것이 가장 나쁜 실패였습니다. 없는
#           주어와 없는 결말을 금지합니다.
#   chat    줄임말과 은어가 많습니다. 모르면 지어내지 말고 음차합니다.
#   music   가사는 문법보다 이미지와 어순이 우선입니다.

# 모든 프리셋이 공유하는 끝부분. 출력 형식 지시는 장르와 무관하므로 한 군데
# 두고, `{context}`는 문맥을 붙이지 않을 때 빈 문자열이 됩니다.
_PROMPT_TAIL = ("Output only the translation, with no quotes, notes, or "
                "romanization.\n\n{context}{text}")

GENRE_PROMPTS = {
    "general": {
        "label": "일반",
        "hint": "장르를 모르거나 섞여 있을 때. 지금까지 쓰던 프롬프트입니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "Keep proper nouns as they are written. " + _PROMPT_TAIL),
    },
    "tech": {
        "label": "기술 발표·세미나",
        "hint": "전사기가 뭉갠 제품·서비스 이름을 문맥에 맞게 되살리고 문어체로 옮깁니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "This is a software or cloud engineering talk. "
                   "Product, service and API names are frequently garbled by "
                   "the speech recogniser; restore the spelling the context "
                   "calls for (for example a mangled cloud DNS service is "
                   "'Route 53'). Keep such names in Latin script. "
                   "Use written, declarative Korean. " + _PROMPT_TAIL),
    },
    "gaming": {
        "label": "게임 방송",
        "hint": "끝나지 않은 말을 대신 끝내지 않습니다. 감탄사는 감탄사로 둡니다.",
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
        "label": "잡담·버라이어티",
        "hint": "은어와 줄임말은 뜻을 지어내지 않고 음차합니다.",
        "prompt": ("Translate the {src} subtitle line below into {tgt}. "
                   "This is a streamer chatting with viewers: casual speech, "
                   "slang, in-jokes and clipped abbreviations. If a coined "
                   "word or abbreviation is not one you recognise, "
                   "transliterate it rather than guessing at a meaning. Do "
                   "not finish an unfinished sentence. Use casual spoken "
                   "Korean. " + _PROMPT_TAIL),
    },
    "music": {
        "label": "노래·가사",
        "hint": "가사의 이미지와 어순을 지키고 없는 주어를 넣지 않습니다.",
        "prompt": ("Translate the {src} song lyric line below into {tgt}. "
                   "Lyrics are fragmentary by design: keep the line's imagery "
                   "and its word order where {tgt} allows, and do not supply "
                   "a subject or a conclusion the line does not have. "
                   + _PROMPT_TAIL),
    },
}

DEFAULT_GENRE = "general"

# 직전 자막을 함께 넘길 때 원문 앞에 끼웁니다.
#
# 이 블록의 모양이 결과를 크게 가릅니다. 처음에는 "참고용"이라고 한 번만
# 적고 대상 줄을 그 뒤에 그냥 두었는데, 63줄 중 4줄에서 모델이 대상 대신
# **참고 줄을 번역했습니다**(`ここまで。`가 "이번에 V스포 보컬 노래
# 방송으로요."로 나왔습니다). 지시를 대상 줄 바로 앞에 한 번 더 두어
# 경계를 다시 세웁니다.
CONTEXT_BLOCK = ("Context -- these {src} lines came before and are NOT to be "
                 "translated:\n{lines}\n\n"
                 "Now translate only this one {src} line:\n")
# 몇 줄을 붙일지는 0·1·2·3·5·8을 재서 정했습니다(RESULTS.md 28절).
#
# 위로는 **5줄부터 모델이 옮기기를 그만둡니다.** `って。`가 `って`로,
# 번역되지 않은 채 돌아옵니다. `looks_broken`은 이것을 잡지 못합니다 --
# 비어 있지도, 길지도 않고, `⁇`도 없으니까요. 네 번 돌려 네 번 같았으므로
# 우연이 아닙니다.
#
# 아래로는 1줄이 모자랍니다. `あなたはどうして生きてるの？百文字以内で
# 答えよ。`의 뒷문장이 통째로 사라졌습니다.
#
# 2와 3의 차이는 크지 않습니다. 3에서만 바로잡히는 줄이 몇 개 있어
# (`うちに心奪われる`가 "집에서"에서 "우리에게"로) 3을 골랐지만, 표본
# 63줄에서 나온 얇은 차이입니다. **최적이라고 부를 근거는 아닙니다.**
CONTEXT_LINES = 3


def genre_prompt(genre: str | None) -> str:
    return GENRE_PROMPTS.get(genre or DEFAULT_GENRE,
                             GENRE_PROMPTS[DEFAULT_GENRE])["prompt"]


def render_prompt(prompt: str, src: str, tgt: str, text: str,
                  context: list[str] | None = None) -> str:
    """`{context}`가 없는 사용자 정의 프롬프트도 그대로 동작합니다.

    str.format은 남는 키워드를 무시하므로, backends.json에 예전 모양의
    프롬프트가 들어 있어도 문맥만 빠진 채 정상적으로 렌더됩니다.
    """
    block = ""
    if context:
        block = CONTEXT_BLOCK.format(
            src=src, lines="\n".join(f"- {c}" for c in context))
    return prompt.format(src=src, tgt=tgt, text=text, context=block)


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

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        """`context`는 이 줄 직전의 자막들입니다.

        프롬프트를 쓰지 않는 백엔드는 무시합니다 -- M2M-100에는 문맥을
        받을 자리가 없습니다.
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
    """M2M-100 한 벌. `models.shared`가 프로세스에 하나만 둡니다."""

    def __init__(self, model_dir: str, device: str, compute_type: str):
        import ctranslate2
        import sentencepiece as spm

        self.sp = spm.SentencePieceProcessor(
            model_file=os.path.join(model_dir, "sentencepiece.model"))
        # CTranslate2의 Translator는 여러 스레드에서 함께 써도 됩니다.
        self.tr = ctranslate2.Translator(model_dir, device=device,
                                         compute_type=compute_type)
        with open(os.path.join(model_dir, "shared_vocabulary.json"), encoding="utf-8") as f:
            self.vocab = set(json.load(f))


class LocalM2M(Translator):
    """M2M-100 with the source language as a parameter rather than a constant."""

    name = "local-m2m100"

    def __init__(self, model_dir: str = M2M_DIR, device: str = "cpu",
                 compute_type: str = "int8"):
        m = models.shared(("m2m100", model_dir, device, compute_type),
                          lambda: _M2MModel(model_dir, device, compute_type))
        self._sp, self._tr, self._vocab = m.sp, m.tr, m.vocab

    def supports(self, lang: str) -> bool:
        return f"__{lang}__" in self._vocab

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        # context는 받지만 쓰지 않습니다. M2M-100은 한 문장을 한 문장으로
        # 옮기는 모델이라 앞 줄을 붙일 자리가 없습니다.
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

    # 장르 표에 옮겨 두었습니다. 이름은 남겨 둡니다 -- 프롬프트를 직접
    # 지정하지 않은 백엔드가 무엇을 쓰는지 여기서 읽히는 편이 낫습니다.
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
    """Gemma 한 벌과 그 자물쇠. 처음 청할 때 올립니다."""

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
                    "./install.sh 를 실행하거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
            from llama_cpp import Llama
            print(f"[translate] Gemma · {self.device} · {self.threads}스레드",
                  file=sys.stderr, flush=True)
            self.llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx,
                             n_threads=self.threads,
                             n_gpu_layers=self.n_gpu_layers, verbose=False)
            return self.llm


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
                 max_tokens: int = 256, genre: str | None = None,
                 device: str = "auto"):
        import stream

        self.model_path = model_path or os.path.join(
            stream.model_dir(), "gemma-4-E4B_q4_0-it.gguf")
        # `cpu`면 한 층도 GPU에 올리지 않습니다. 내장 그래픽처럼 전사와
        # 나눠 쓰기 빠듯한 기계에서, 번역만이라도 CPU로 돌려 두면 자막이
        # 서로를 기다리지 않습니다. 번역은 줄당 0.2초라 CPU로도 충분합니다.
        self.device = (device or "auto").strip().lower()
        self.n_gpu_layers = 0 if self.device == "cpu" else -1
        # 백엔드에 프롬프트를 직접 적어 두었다면 그것이 우선입니다. 장르는
        # 그 자리를 비워 둔 백엔드에만 적용됩니다.
        self.prompt = prompt or genre_prompt(genre)
        self.max_tokens = max_tokens
        self._n_ctx = n_ctx
        self._threads = threads
        # 모델은 프로세스에 한 벌입니다(models.py). 프롬프트(장르)는 이 객체의
        # 것이고 모델은 공유하므로, 장르가 다른 세션과 작업이 같은 Gemma를
        # 씁니다. 첫 번역 때 올립니다 -- 미리 올리면 세션 시작이 그만큼 늦습니다.
        self._holder = models.shared(
            ("gemma", self.model_path, self.device, n_ctx, threads, self.n_gpu_layers),
            lambda: _LlamaHolder(self.model_path, self.device, n_ctx, threads,
                                 self.n_gpu_layers))
        # llama.cpp의 컨텍스트는 동시 호출을 견디지 못합니다. 자막 한 줄마다
        # 번역 스레드가 뜨므로 모델과 함께 사는 자물쇠로 직렬화합니다.
        self._lock = self._holder.lock

    def _ensure(self):
        return self._holder.get()

    def translate(self, text: str, src: str, tgt: str,
                  context: list[str] | None = None) -> str:
        stripped = (text or "").strip()
        if not stripped or src == tgt:
            return text
        llm = self._ensure()
        msg = render_prompt(self.prompt, src, tgt, stripped, context)
        with self._lock:
            out = llm.create_chat_completion(
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


class Lazy(Translator):
    """첫 호출에서야 만듭니다.

    대체용 M2M-100은 기본 엔진이 실패할 때만 필요한데, 예전에는 `build()`마다
    미리 만들었습니다 -- 세션을 열 때마다 473MB를 (한 번은) 읽는 셈이었고,
    모델 파일이 없는 기계에서는 Gemma가 멀쩡해도 여기서 넘어졌습니다.
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

    `genre`는 백엔드가 아니라 보고 있는 영상의 성질입니다. 같은 Gemma로
    기술 발표와 게임 방송을 모두 번역하되 프롬프트만 갈아 끼웁니다.
    """
    spec = spec or {}
    min_chars = int(spec.get("min_chars", DEFAULT_MIN_CHARS) or 0)
    if spec.get("backend") == "gemma":
        device = (spec.get("device") or "auto").strip().lower()
        # CPU로 돌리면 스레드 4는 모자랍니다. 전사와 같은 규칙을 씁니다.
        gemma = LocalGemma(spec.get("model_path"),
                           n_ctx=int(spec.get("n_ctx", 2048)),
                           threads=int(spec.get("threads")
                                       or stream.default_threads(device)),
                           prompt=spec.get("prompt"), genre=genre,
                           device=device)
        gemma.min_chars = min_chars
        # 모델 파일이 없거나 적재가 실패해도 자막이 원문으로 남지는 않도록
        # M2M-100을 뒤에 둡니다. 어느 쪽이 실제로 답했는지는 기록됩니다.
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
