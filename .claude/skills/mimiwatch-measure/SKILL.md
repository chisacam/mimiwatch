---
name: mimiwatch-measure
description: Measure one knob of the mimiwatch transcribe→refine→translate pipeline against a sample and write the result up as the next numbered section of measurements/RESULTS.md. Use when asked "재 보자", "측정해주세요", "이 값은 벤치마킹으로 나온 건가요", "표본으로 확인해주세요", "VAD 문턱을 바꿔보면", "정제가 나은지", "benchmark the pipeline", "which engine is better" — or whenever you are about to state that one setting is better than another. Not for adding a feature, and not for a change you cannot express as a single knob with a control.
---

# Measure one knob, leave one section

`measurements/RESULTS.md` is the project's memory. Every default in the code points at a
section number, and a commit that changes a default cites one. The unit of work here is
**one section**, not one experiment.

| Artifact | Path | Tracked? |
|---|---|---|
| Sample (audio + answer key) | `data/gold/` | no — `.gitignore` (copyright) |
| Raw run output | `measurements/*.json`, `bench/out_*.json` | no — contains broadcast speech |
| The write-up | `measurements/RESULTS.md` § next number | **yes** |
| The commit | message cites the section number | **yes** |

## Step 0 — read before measuring

1. `grep -E '^## ' measurements/RESULTS.md` — the last number is your number, and two or
   three of those titles probably already touch your question. §43 defines the samples
   and the scoring; read it first, always.
2. `ls bench/` — the script you need likely exists (`gold_fetch.py`, `gold.py`,
   `gold_e2e.py`, `sweep.py`, `compare.py`, `vad_ab.py`, `whisper_ab.py`,
   `refine_pass.py`). Read its docstring; each one states what it can and cannot score.

If an existing section already answers the question, say so and stop. That is a result.

## Step 0b — the decisions to confirm with the requester

Ask together, once:

1. **Which sample.** Japanese is the priority language; songs are the worst case and
   inflate every absolute number. A broadcast recording is more representative but
   usually has no answer key.
2. **What counts as better.** CER/WER, 대사 포착률, end-to-end chrF, and latency do not
   move together — §49 has a case where the content score held flat while the timing
   score fell. Name the number that decides it *before* running.
3. **One knob or a sweep.** A sweep costs hours of model time; confirm it is wanted.

## Step 0c — the facts to read now, not from this file

These move, and quoting them here is how this skill goes stale:

```sh
grep -E '^## ' measurements/RESULTS.md | tail -5      # last section number
grep -n 'seeded\|PROTECTED' config.py                 # which engines are default today
grep -n 'PROFILES\|threshold' stream.py               # current window/VAD values
```

## Step 1 — classify the sample by what answer key it has

This decides what you are allowed to conclude. Three classes:

| The sample has | Score with | You may conclude |
|---|---|---|
| Manual same-language subtitles (YouTube) | `bench/gold.py` → CER (ja) / WER (en) | transcription quality |
| Only a human *translation* (fansub, SAMI) | `bench/gold_e2e.py` → 대사 포착률 + chrF | VAD coverage, end-to-end translation |
| No answer key at all (a stream recording) | structural counters only: line count, characters, lines over 7 s, order inversions, overlaps | that the output is **not malformed** |

**With no answer key you do not touch a threshold.** §52 states the rule in the project's
own words: tuning on a sample with no answer key is not measuring, it is fitting. A
no-answer-key sample is for confirming a change already justified elsewhere behaves
sanely on real broadcast audio.

`bench/gold_fetch.py` is the provenance of the first class — `data/` is not in the repo,
so that script is the only record of where the samples came from.

## Step 2 — fix the control, move one knob

- Re-run the **baseline** in the same session, on the same machine. A baseline copied
  from an earlier section was measured on a different build.
- One knob. §50 exists because a two-knob conclusion ("4 s is 2 p better") turned out to
  be mostly a 60-second window boundary — the scoring window, not the setting.
- Reuse cached intermediates where the script offers it (`gold_e2e.py` stores
  `<wav>.<tag>.asr.json`) so a translation-only comparison does not re-transcribe.

## Step 3 — read the numbers as differences

Absolute values in this repo are high on purpose: the song samples are the hardest case
(baseline CER 47–80 %) and fansub paraphrase drags chrF down. **Differences** are the
signal.

Run-to-run spread is about ±1 p at the §43 sample size (1,490 characters; the same
setting twice gave 63.7 / 64.4). A 1-point win is noise. Say so rather than reporting it.

## Step 4 — write the section

Append, never rewrite an earlier section — they are cited from code comments and commit
messages.

- **Number** = last + 1.
- **Title is a claim, not a topic.** The existing titles read "정제는 정말로 되살립니다",
  "Whisper 손잡이는 전부 오차 안, VAD 문턱만 움직였습니다". A reader scanning the table of
  contents should get the answer without opening the section.
- Body: the setting under test, the table, and — required — **what the sample does not
  cover**. §52 names two limits it deliberately did not act on; that paragraph is the
  most reusable part of a section.
- Korean prose, explaining *why*, like the rest of the file.

## Step 5 — verify and commit

```sh
.venv/bin/python bench/check_all.py
.venv/bin/ruff check .
git status --short          # data/, measurements/*.json, bench/*.json must not appear
```

Commit message: one Korean sentence, ` -- `, then the reason with the section number in
it. If the measurement changed a default, the number that justifies it belongs in the
message, not only in the section.

## Traps that have bitten before

- **The light default engine pair cannot be refined.** SenseVoice Small and moonshine
  report `max_timestamp_kind = none`, so anything measuring the refine pass has to run
  the heavy pair — and a machine set to the defaults will silently skip refinement
  instead of measuring it.
- **Live and VOD are not the same refine.** Live tolerates one merged line because it
  scrolls past; a VOD cue is navigated to by timestamp, so §49's answer was to re-split
  by segment timestamps rather than to reuse the live path.
- **The scoring window can outweigh the setting** (§50). Before believing a win, check
  whether it survives a different window.
- **Speaker labels are unreliable on solo broadcasts** — CAM++ produced five speakers for
  one person. Noted in §52 and not acted on; do not treat it as a regression you caused.
- **Never commit a sample.** `data/`, `measurements/*.json|log|err|txt` and `bench/*.json`
  are gitignored because they contain broadcast speech verbatim.

## What this skill does not do

- Flip a default. The light CPU pair is the default on §33–34 evidence; changing it needs
  its own section *and* the owner's word.
- Publish anything containing broadcast speech.
- Tune a threshold on a sample with no answer key (Step 1).
- Decide priorities between quality and latency — measure both, hand over the trade.
