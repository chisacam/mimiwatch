"""정답 자막이 있는 표본으로 전사 설정을 채점합니다 (CER · 일본어/한국어, WER · 영어).

    .venv/bin/python bench/gold.py                       # data/gold/*.wav 전부, 기본 설정
    .venv/bin/python bench/gold.py --asr tcpp-lite      # 설정의 다른 엔진으로
    .venv/bin/python bench/gold.py --profile talk --pad 0.3 --whisper '{"no_speech_thold": 0.84}'
    .venv/bin/python bench/gold.py --model Fun-ASR-MLT-Nano-2512-Q8_0.gguf --device cpu

정답은 같은 이름의 `.ja.srt`(또는 `.ja-*.srt`, `.vtt`) 입니다 -- 유튜브의 **수동** 자막을
yt-dlp 로 받아 둔 것입니다. 구간 경계는 저마다 다르므로 자막 전체를 한 줄로 이어 붙여
비교합니다. 정규화: NFKC, 공백·구두점 제거. 일본어는 한자/가나 표기 차이(晴る/はる)를
글자 오류로 셉니다 -- 노래 가사 자막은 특히 그러니 절대값보다 **설정 사이의 차이**를 보십시오.

라이브와 같은 길로 자릅니다: VAD(프로필) → 앞 1초 선행 → 조각별 해독. `--pad` 는 VAD 가
끝이라 한 뒤 붙이는 뒤패딩(초)로, 지금 라이브 경로에는 없는 실험 손잡이입니다.
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys, time, unicodedata, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config, stream, tcpp_asr
import refine_pass
import transcribe_vod as vod
from live import PROFILES

PUNCT = re.compile(r"[\s、。，．,.!?！？「」『』（）()\[\]【】…・~〜\-—–\"'“”‘’:;：；♪]")


def norm(s: str) -> str:
    return PUNCT.sub("", unicodedata.normalize("NFKC", s or "")).lower()


def edit_distance(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hyp: str, ref: str) -> float:
    h, r = norm(hyp), norm(ref)
    return edit_distance(h, r) / max(1, len(r))


def wer(hyp: str, ref: str) -> float:
    h, r = norm_words(hyp), norm_words(ref)
    return edit_distance(h, r) / max(1, len(r))


def norm_words(s: str):
    return re.sub(r"[^\w\s]", "", unicodedata.normalize("NFKC", s or "")).lower().split()


def read_ref(wav: str) -> tuple[str, str]:
    base = wav[:-4]
    for pat in (".ja*.srt", ".ja*.vtt", ".ko*.srt", ".ko*.vtt", ".en*.srt", ".en*.vtt"):
        hits = sorted(glob.glob(glob.escape(base) + pat))
        if hits:
            lang = pat[1:3]
            lines = []
            for ln in open(hits[0], encoding="utf-8"):
                # 유튜브의 스타일 자막(가라오케식)은 제로폭 문자를 채우고, 한자마다 읽기를
                # 괄호로 달고(目(め)覚(ざ)め), 한 줄을 화면에 띄우는 동안 큐마다 되풀이합니다.
                # 노래한 가사 한 벌만 남기려고 셋을 걷어 냅니다. 크레딧(作詞：…)도 노래가 아닙니다.
                ln = re.sub(r"[\u200b\u200c\u2060\ufeff]", "", ln).strip()
                if not ln or ln.isdigit() or "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:")):
                    continue
                ln = re.sub(r"<[^>]+>", "", ln)
                ln = re.sub(r"[（(][ぁ-ゖァ-ヺー]+[）)]", "", ln)          # 후리가나
                if "：" in ln or "／" in ln:
                    continue                                       # 크레딧·제목 줄
                if ln in lines[-3:]:
                    continue                                       # 화면에 머무는 동안의 되풀이
                lines.append(ln)
            return lang, " ".join(lines)
    raise FileNotFoundError(f"{base}.<lang>.srt 가 없습니다")


def segments(pcm: np.ndarray, profile: str, pad_s: float, threshold: float | None = None):
    """(해독할 조각들, 그 조각의 표본 구간). 구간은 정제 패스가 무리를 묶는 데 씁니다."""
    ms, mx = PROFILES[profile]["min_silence"], PROFILES[profile]["max_speech"]
    vad = stream.build_vad(min_silence=ms, max_speech=mx, threshold=threshold)
    hist = stream.AudioHistory()
    out, spans, pending = [], [], []

    def drain(final=False):
        while not vad.empty():
            s = vad.front; a = np.asarray(s.samples, dtype=np.float32)
            start, end = s.start, s.start + len(a)
            pending.append((start, end)); vad.pop()
        # 뒤패딩: 끝난 구간 뒤로 pad 초가 더 들어온 뒤에 잘라 냅니다.
        have = hist.offset + len(hist.buf)
        while pending and (final or have >= pending[0][1] + int(pad_s * 16000)):
            start, end = pending.pop(0)
            end2 = min(end + int(pad_s * 16000), have)
            out.append(hist.slice(start, end2))          # slice 가 앞 1초 선행을 붙입니다
            spans.append((start, end2))

    for i in range(0, len(pcm), 1600):
        chunk = pcm[i:i + 1600]; vad.accept_waveform(chunk); hist.push(chunk); drain()
    vad.flush(); drain(final=True)
    return out, spans


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--asr", default="", help="설정의 전사 엔진 id (기본: 활성 엔진)")
    ap.add_argument("--model", default="", help="모델 파일 이름으로 직접 지정")
    ap.add_argument("--device", default="")
    ap.add_argument("--profile", default="talk", choices=sorted(PROFILES))
    ap.add_argument("--pad", type=float, default=0.0, help="VAD 뒤패딩(초)")
    ap.add_argument("--whisper", default="", help='WhisperRunOptions JSON, 예: {"no_speech_thold": 0.84}')
    ap.add_argument("--lang", default="", help="원본 언어 고정 (기본: 정답 자막의 언어)")
    ap.add_argument("--dump", action="store_true", help="가설 전문을 출력")
    ap.add_argument("--diversity", type=float, default=None,
                    help="환각 차단의 4-gram 다양도 바닥 (기본 0.35, 0 이면 끔)")
    ap.add_argument("--tag", default="", help="결과 줄 앞에 붙일 이름")
    ap.add_argument("--vad-threshold", type=float, default=stream.VAD_THRESHOLD,
                    help=f"Silero 말 판정 문턱 (기본 {stream.VAD_THRESHOLD})")
    ap.add_argument("--refine", action="store_true",
                    help="확정본 뒤에 정제 패스를 붙입니다 (라이브와 같은 무리 규칙)")
    ap.add_argument("--refine-merged", action="store_true",
                    help="49절 재현: 되쪼개지 않고 무리 하나를 자막 한 줄로 둡니다")
    a = ap.parse_args()
    if a.diversity is not None:
        tcpp_asr.DIVERSITY_FLOOR = a.diversity

    files = a.files or sorted(glob.glob("data/gold/*.wav"))
    spec = dict(config.find_asr(a.asr or config.active("asr")) or {"backend": "tcpp"})
    if a.model: spec["model"] = a.model
    if a.device: spec["device"] = a.device
    if a.whisper: spec["whisper"] = json.loads(a.whisper)
    label = spec.get("model") or "whisper-large-v3-turbo-Q8_0.gguf"
    print(f"[{a.tag}] " if a.tag else "", end="")
    print(f"엔진 {label} · {spec.get('device', 'auto')} · 프로필 {a.profile} · 뒤패딩 {a.pad}s"
          + (f" · whisper {spec['whisper']}" if spec.get("whisper") else "")
          + (f" · 다양도 {a.diversity}" if a.diversity is not None else "")
          + f" · VAD 문턱 {a.vad_threshold}")
    total_err = total_len = 0
    for f in files:
        lang, ref = read_ref(f)
        with wave.open(f) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        asr = tcpp_asr.build_live_asr(spec, a.lang or lang)
        segs, spans = segments(pcm, a.profile, a.pad, a.vad_threshold)
        speech_s = sum(len(s) for s in segs) / 16000
        t0 = time.time()
        parts = [asr.transcribe(s, 16000)["text"] for s in segs]
        # 정제 패스는 확정본을 낸 뒤에 붙습니다 -- 라이브와 같은 차례입니다.
        # 44절이 「짧게 끊어 잃은 것을 정제가 되살린다」고 미룬 판단을 여기서
        # 실제로 잽니다. 무리 수도 함께 적습니다(구간이 몇 개로 합쳐졌는지).
        groups = 0
        if a.refine:
            cues = [{"start": lo / 16000, "end": hi / 16000, "lang": "", "text": t}
                    for (lo, hi), t in zip(spans, parts)]
            lines = (refine_pass.refine_merged(pcm, spans, parts, asr)
                     if a.refine_merged else vod.refine_cues(pcm, cues, spans, asr))
            groups = len(lines)
            parts = [ln["text"] for ln in lines]
        hyp = " ".join(parts)
        el = time.time() - t0
        if lang == "en":
            score, unit = wer(hyp, ref), "WER"
            err, n = round(score * len(norm_words(ref))), len(norm_words(ref))
        else:
            score, unit = cer(hyp, ref), "CER"
            err, n = round(score * len(norm(ref))), len(norm(ref))
        total_err += err; total_len += n
        print(f"  {os.path.basename(f):<22} {lang} {unit} {score*100:5.1f}%  구간 {len(segs):>3} "
              + (f"→무리 {groups:>3} " if a.refine else "")
              + f"({speech_s:.0f}s/{len(pcm)/16000:.0f}s)  환각차단 {asr.hallucinations}  정답 {n}자  "
              f"{len(pcm)/16000/el:4.1f}x")
        if a.dump:
            print("    가설:", hyp[:400]); print("    정답:", ref[:400])
    if total_len:
        print(f"  전체 오류율 {total_err/total_len*100:.1f}%")


if __name__ == "__main__":
    main()
