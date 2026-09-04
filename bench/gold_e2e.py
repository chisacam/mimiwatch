"""Score the transcription-to-translation end with human-made **translated subtitles**
(a Korean SAMI file, say) as the ground truth.

    .venv/bin/python bench/gold_e2e.py data/gold/kagami.wav data/gold/kagami.ko.smi
    .venv/bin/python bench/gold_e2e.py ... --vad-threshold 0.5 --tag vad0.5
    .venv/bin/python bench/gold_e2e.py ... --translate local-gemma --genre general

Two things that can still be counted on a sample with no transcription ground truth
(animation, drama).

  dialogue capture rate   The fraction of the ground-truth cues whose time a transcription
                          span overlaps. It falls when VAD misses dialogue. Used to measure
                          the VAD threshold over 116 minutes mixed with sound effects and BGM.
  chrF                    Character n-gram (1-6) F2 between the translation and the human one,
                          grouped into 60 s windows. The absolute value is low because fan
                          subtitles paraphrase, so look at the **difference** between
                          translation engines, genre prompts and transcription settings.

The transcription is saved to `<wav>.<tag>.asr.json` and reused when only the translation
setting changes on a rerun.
"""
from __future__ import annotations
import argparse, html, json, os, re, sys, time, unicodedata, wave
from collections import Counter
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config, stream, tcpp_asr
import refine_pass
import transcribe_vod as vod
from live import PROFILES

SR = 16000


# ---- ground-truth subtitles ------------------------------------------------------

def read_smi(path: str) -> list[dict]:
    """SAMI -> [{start, end, text}]. An empty cue (&nbsp;) sets the end of the cue before it."""
    raw = open(path, encoding="utf-8-sig", errors="replace").read()
    cues = []
    for m in re.finditer(r"<Sync\s+Start=(\d+)[^>]*>(.*?)(?=<Sync\s|</BODY>)", raw, re.S | re.I):
        start = int(m.group(1)) / 1000
        body = re.sub(r"<br\s*/?>", " ", m.group(2), flags=re.I)
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("​", "").strip()
        if cues:
            cues[-1]["end"] = start
        if not body:
            continue
        # On-screen text ([마음의 교실]) and production credits are not dialogue.
        if re.fullmatch(r"\[.*\]", body) or "@" in body:
            continue
        cues.append({"start": start, "end": start + 4.0, "text": body})
    return cues


def read_ref(path: str) -> list[dict]:
    if path.lower().endswith(".smi"):
        return read_smi(path)
    cues, cur = [], None
    for ln in open(path, encoding="utf-8-sig"):
        ln = ln.strip()
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s+-->\s+(\d+):(\d+):(\d+)[,.](\d+)", ln)
        if m:
            h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
            cur = {"start": h1 * 3600 + m1 * 60 + s1 + ms1 / 1000,
                   "end": h2 * 3600 + m2 * 60 + s2 + ms2 / 1000, "text": ""}
            cues.append(cur)
        elif cur is not None and ln and not ln.isdigit():
            cur["text"] = (cur["text"] + " " + re.sub(r"<[^>]+>", "", ln)).strip()
    return [c for c in cues if c["text"]]


# ---- transcription ---------------------------------------------------------------

def transcribe(wav: str, spec: dict, lang: str, profile: str, threshold: float,
               do_refine: bool = False, merged: bool = False) -> list[dict]:
    with wave.open(wav) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    prof = PROFILES[profile]
    vad = stream.build_vad(min_silence=prof["min_silence"], max_speech=prof["max_speech"],
                           threshold=threshold)
    hist = stream.AudioHistory()
    asr = tcpp_asr.build_live_asr(spec, lang)
    out, spans = [], []
    t0 = time.time()

    def drain():
        while not vad.empty():
            s = vad.front; a = np.asarray(s.samples, dtype=np.float32)
            start, end = s.start / SR, (s.start + len(a)) / SR
            got = asr.transcribe(hist.with_preroll(s.start, a), SR)
            vad.pop()
            if got["text"].strip():
                out.append({"start": round(start, 2), "end": round(end, 2),
                            "text": got["text"].strip(), "lang": got.get("lang") or ""})
                spans.append((s.start, s.start + len(a)))

    for i in range(0, len(pcm), 1600):
        c = pcm[i:i + 1600]; vad.accept_waveform(c); hist.push(c); drain()
    vad.flush(); drain()
    fast_n = len(out)
    # Refinement comes after every final is out. A VOD has the whole audio, so the
    # 30 s ring limit of live does not apply, but the rule that groups utterances is
    # kept identical -- make it different and the comparison against what was
    # measured on live no longer holds.
    if do_refine:
        print(f"    정제 {fast_n}구간...", file=sys.stderr, flush=True)
        out = (refine_pass.refine_merged(pcm, spans, [o["text"] for o in out], asr)
               if merged else vod.refine_cues(pcm, out, spans, asr))
    return {"segments": out, "hallucinations": asr.hallucinations,
            "fast_segments": fast_n,
            "audio_s": len(pcm) / SR, "elapsed_s": round(time.time() - t0, 1)}


# ---- metrics ---------------------------------------------------------------------

def coverage(ref: list[dict], segs: list[dict], slack: float = 0.5) -> tuple[int, int]:
    """How many ground-truth cues have a transcription span overlapping their time (+-slack)."""
    starts = np.array([s["start"] for s in segs]); ends = np.array([s["end"] for s in segs])
    hit = 0
    for c in ref:
        if np.any((starts <= c["end"] + slack) & (ends >= c["start"] - slack)):
            hit += 1
    return hit, len(ref)


def _chars(s: str) -> str:
    return re.sub(r"[\s、。，．,.!?！？「」『』…・~〜\-—–\"'“”‘’:;：；♪\[\]()]", "",
                  unicodedata.normalize("NFKC", s or ""))


def chrf(hyp: str, ref: str, n_max: int = 6, beta: float = 2.0) -> float:
    h, r = _chars(hyp), _chars(ref)
    if not h or not r:
        return 0.0
    ps, rs = [], []
    for n in range(1, n_max + 1):
        hg = Counter(h[i:i + n] for i in range(len(h) - n + 1))
        rg = Counter(r[i:i + n] for i in range(len(r) - n + 1))
        if not hg or not rg:
            continue
        ov = sum((hg & rg).values())
        ps.append(ov / max(1, sum(hg.values()))); rs.append(ov / max(1, sum(rg.values())))
    p, rc = sum(ps) / len(ps), sum(rs) / len(rs)
    return 0.0 if p + rc == 0 else (1 + beta ** 2) * p * rc / (beta ** 2 * p + rc)


def windowed_chrf(ref: list[dict], hyp: list[dict], win: float) -> float:
    end = max([c["end"] for c in ref] + [h["end"] for h in hyp])
    scores = []
    for t in np.arange(0, end, win):
        r = " ".join(c["text"] for c in ref if t <= c["start"] < t + win)
        h = " ".join(c["tr"] for c in hyp if t <= c["start"] < t + win and c.get("tr"))
        if r:
            scores.append(chrf(h, r))
    return sum(scores) / max(1, len(scores))


# ---- translation -----------------------------------------------------------------

def translate_all(segs: list[dict], backend_id: str, genre: str, src: str, tgt: str) -> None:
    import translate as mw_translate
    tr = mw_translate.build(config.find("tr", backend_id) or {"backend": "local"}, genre)
    t0 = time.time()
    for i, s in enumerate(segs):
        ctx = [x["text"] for x in segs[max(0, i - mw_translate.CONTEXT_LINES):i]]
        try:
            s["tr"] = tr.translate(s["text"], src, tgt, ctx)
        except Exception as exc:
            s["tr"] = s["text"]; s["tr_error"] = str(exc)[:80]
        if i % 100 == 0:
            print(f"    번역 {i}/{len(segs)}", file=sys.stderr, flush=True)
    return round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wav"); ap.add_argument("ref")
    ap.add_argument("--lang", default="ja"); ap.add_argument("--tgt", default="ko")
    ap.add_argument("--asr", default=""); ap.add_argument("--model", default=""); ap.add_argument("--device", default="")
    ap.add_argument("--profile", default="talk", choices=sorted(PROFILES))
    ap.add_argument("--vad-threshold", type=float, default=stream.VAD_THRESHOLD)
    ap.add_argument("--tag", default="")
    ap.add_argument("--translate", default="", help="번역 엔진 id. 비우면 전사만")
    ap.add_argument("--genre", default="general")
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--refine", action="store_true",
                    help="확정본 뒤에 정제 패스를 붙입니다 (라이브와 같은 무리 규칙)")
    ap.add_argument("--refine-merged", action="store_true",
                    help="49절 재현: 되쪼개지 않고 무리 하나를 자막 한 줄로 둡니다")
    a = ap.parse_args()

    ref = read_ref(a.ref)
    spec = dict(config.find_asr(a.asr or config.active("asr")) or {"backend": "tcpp"})
    if a.model: spec["model"] = a.model
    if a.device: spec["device"] = a.device
    tag = a.tag or (f"{(spec.get('model') or 'whisper')[:12]}-{a.profile}-vad{a.vad_threshold}"
                    + ("-refine" if a.refine else "")
                    + ("-merged" if a.refine_merged else ""))
    cache = f"{a.wav}.{tag}.asr.json"
    if os.path.exists(cache):
        got = json.load(open(cache, encoding="utf-8"))
        print(f"[{tag}] 전사 재사용 {cache}")
    else:
        got = transcribe(a.wav, spec, a.lang, a.profile, a.vad_threshold,
                         a.refine, a.refine_merged)
        json.dump(got, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    segs = got["segments"]
    hit, n = coverage(ref, segs)
    speech = sum(s["end"] - s["start"] for s in segs)
    print(f"[{tag}] 정답 큐 {n}  대사 포착률 {hit / n * 100:.1f}%  전사 구간 {len(segs)}"
          + (f"(정제 전 {got['fast_segments']})  " if got.get("fast_segments") else "  ")
          + f"말 {speech:.0f}s/{got['audio_s']:.0f}s  환각차단 {got['hallucinations']}  "
          + f"전사 {got['audio_s'] / max(1, got['elapsed_s']):.0f}x")
    if a.translate:
        el = translate_all(segs, a.translate, a.genre, a.lang, a.tgt)
        json.dump(got, open(f"{a.wav}.{tag}.{a.translate}.{a.genre}.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        score = windowed_chrf(ref, segs, a.window)
        errs = sum(1 for s in segs if s.get("tr_error"))
        print(f"[{tag} → {a.translate}/{a.genre}] chrF({a.window:.0f}s 창) {score * 100:.1f}  "
              f"번역 {len(segs)}줄 {el}s  실패 {errs}")


if __name__ == "__main__":
    main()
