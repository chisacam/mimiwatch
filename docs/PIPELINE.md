# Transcription–Translation–Display Pipeline Review (2026-08-29)

*[한국어](PIPELINE.ko.md)*

Starting from the question "quality is decided by the model, but what
additionally contributes to speed and quality is, in the end, the processing
pipeline", I looked again at the path from sound coming in to a subtitle
reaching the screen, holding the code up against the measurement record
(`measurements/RESULTS.md`).

The conclusion first: **the structure stays as it is.** The shape of each stage
was already settled by measurement, and no new fact turned up that would
overturn that basis. Instead, in the code I found and fixed **one race in the
translation path and one thread structure**, and I write down here, with the
reasons, the things I reviewed but did not touch.

## 1. The path as it is now

```
sound (ffmpeg pipe or tab PCM) ─ 0.1 s chunks ─▶ Silero VAD ─▶ utterance chunk (3~12 s per profile, 1 s of lead-in)
   ─▶ Whisper decode (final) ─▶ publish_line("final") ─▶ SQLite + SSE ─▶ screen (average 0.38 s)
                                                         └▶ translation queue ─▶ Gemma (3 lines of context) ─▶ SSE
   after 2 s of silence, re-decode the utterance group (refinement) ─▶ publish_line("refine") ─▶ replaces the finals it absorbed ─▶ translation
```

| Stage | Value | Basis |
|---|---|---|
| VAD → final subtitle reaching the screen | average 384 ms, max 795 ms (collab) | section 14 |
| One decode | average 381 ms (Metal, 3~4 s chunks) | section 14 |
| One translated line | Gemma 0.15 s, M2M-100 0.2 s | sections 5·23 |
| Refinement wait | 2 s of silence (`GROUP_GAP_S`) → send the final out first, replace it afterwards | sections 2·7 |
| Context | the previous 3 lines (having measured 0·1·2·3·5·8 lines) | sections 28~31 |
| Quantization | Q8_0 stays (going lower does not get faster on Metal) | sections 15~18 |
| Batched decode | `run_batch` is slower instead | section 16 |

The latency is made up like this. From a person finishing a sentence to the
subtitle appearing = **silence detection (0.25~0.35 s) + decoding (~0.38 s) +
delivery (a few ms)**. Silence detection is the time the VAD needs to be
confident the utterance has ended, so shortening it cuts sentences off.
Decoding is decided by the model and the device. That is, **there is almost no
latency left that the pipeline could remove.** The rest is all time spent
waiting on purpose for accuracy, and what makes you not wait out that time is
"the final first, replaced by the refined line".

## 2. What was fixed

### 2.1 The race where the translation of a final line absorbed by a refined line overwrote the refined line's translation

A refined line **inherits the id** of the first final line it absorbed (so that
the screen swaps out that slot). Previously a new translation thread was
started for every subtitle line, so if that final line's translation thread
finished **later** than the refined line's translation thread, the old partial
translation was published and stored under the same id. A half-finished
translation was left under the refined line on the screen and in the DB, and it
stayed there through a reload. Since the order in which the two threads
finished was not fixed, it was intermittent.

Now **one translation worker thread** per session drains the queue in the order
things were put into it. The publish order is the translation order, and each
line checks **before and after** translating whether the source text of
this id is still this text (`_text_of`). If a refined line arrived in between,
the result is discarded. Test:
`tests/test_live.py::test_superseded_final_is_not_translated_after_refine`.

### 2.2 As a bonus, translation calls drop

A final line whose refined line has already arrived **does not even call the
translator.** In the sample in section 2 there were 15 finals to 8 refined
lines, and 26 to 9, so on a stream with refinement on, a good part of the
final-line translations is absorbed into refined lines. Gemma idles that much
more, and the refined line's translation arrives in that slot sooner. (How much
it dropped has not been measured yet -- it is the next measurement item.)

### 2.3 Thousands of threads → one

A two-hour stream created thousands of threads. With a single model lock they
could not run side by side either, so there was cost and nothing gained. When a
session ends it translates the lines left in the queue before leaving
(`_close_translator`, up to 10 s) -- so that the goodbye at the end of a stream
is not left as source text only.

## 3. Reviewed but not changed

**Giving Whisper the previous sentence as a prompt (conditioning).** A common
technique for raising proper-noun consistency. (When I first wrote this I said
"the bindings have no slot for it", which was wrong -- 0.2.2's
`WhisperRunOptions(initial_prompt, condition_on_prev_tokens, no_speech_thold,
logprob_thold, compression_ratio_thold, …)` can be passed through
`Session.run(family=…)`.) Even so it does not go into the fast pass: in live
use this technique is known to increase repetition hallucination (whisper.cpp
#3744·#2286, exactly the phenomenon the diversity check in section 13 blocks),
and a defect where the tail is cut off on ≤30 s chunks has been reported as
well (transcribe.cpp #89). An experiment that passes one or two of the previous
refined sentences **to the refinement pass only** is worth doing -- with the
hallucination counter as the metric. Per-chunk
`no_speech_prob`/`avg_logprob` (chunk trace) exist only in the C API, so from
Python the only route is adjusting the thresholds
(`no_speech_thold`·`logprob_thold`).

**Speculative decoding (`spec_k_drafts`).** The bindings expose it, but on
whisper-large-v3-turbo -1, 0 and 4 all took the same decode time (5 s of
silence, 256~264 ms). This model does not advertise the feature, so it appears
to be silently ignored.

**Re-evaluating the Gemma prompt.** I looked at whether re-evaluating some 200
tokens of instructions on every line is wasteful, but llama-cpp-python
**automatically reuses the KV cache for prefix tokens identical to the previous
call** (`longest_prefix` in `Llama.generate`). Our prompt puts the genre
instructions in front and the changing context and source text behind, so the
instructions are not evaluated. This structure is already close to optimal.

**Translating several lines in one prompt (batching).** It could cut the 880
lines × 0.15 s = 2 minutes on a VOD, but the "one line in, one line out"
contract breaks, so lines risk being merged or dropped, and for live it means
nothing. Held off.

**Transcription and translation side by side on the GPU.** The two hold their
own locks so they do not block each other, but they share one Metal device, so
they slow each other down. Right now translation is 0.15 s, so it does not
show. On a VOD, running translation alongside transcription could make the
total time shorter or longer, so it is not changed before it is measured.

**0.1 s VAD chunks, 1 s of lead-in, keeping 30 s of audio.** Every 0.1 s the
30 s buffer is rebuilt with `np.concatenate`, but 1.9 MB × 10 times/s is not a
cost.

## 4. What was measured and what to measure next

On the evening of 2026-08-29 two things were measured
(`measurements/RESULTS.md` sections 41~42): **Silero VAD v5 throws away 90% of
the speech in VTuber streams, so v4 stays**, and **hardening the Whisper
thresholds (0.84/-1.3) makes no difference on 4 s chunks**. Only the knobs
(`whisper: {...}`, `refine_prompt`, `MIMIWATCH_VAD_MODEL`) were left.

That same night, with a sample that has reference subtitles (four music videos,
`bench/gold.py`), twelve knobs and four alternative models were measured
(sections 43~45): **the Whisper thresholds, the prompt and the fallback are all
within the error margin**, the shorter the cut the worse it gets, and **only the
VAD threshold 0.5 → 0.3 gains (64.4 → 59.0%)**; it is neutral on the
conversational sample, so it was promoted to the default. Fun-ASR, Qwen3-ASR and
Cohere Transcribe fell 6~14p behind whisper-turbo on Japanese songs.

With 116 minutes of animation (Korean fansubs) the tail end was measured
(sections 46~47): VAD 0.3 halves the lines it missed, Gemma chrF 29~31 vs
M2M-100 13, splitting at 4 s is 2p better for translation than 12 s, Gemma
misses proper nouns like `心/ココロ` → a glossary experiment is the next
priority.

What is left:

- How much translation calls dropped on a stream with refinement on
  (section 2.2).
- The rate at which the 2 s refinement wait actually fires on the collab
  profile (3 s cuts) -- in section 2, 40~50% of lines had refinement pushed
  back by more than 5 s, but that was with 12 s cuts.
- That the basis for 3 lines of context is thin (section 31) still stands. More
  samples have to be collected.


## 5. Multiview (2026-08-30)

Several streams sit on one screen, but **only one session is transcribed at a
time.** For that, the path in section 1 had to split "sound coming in" from
"transcribing it" -- previously reading ffmpeg and the VAD/decoding were one
for loop in one thread, so when transcribing stopped, nobody read the pipe and
ffmpeg stalled.

```
ffmpeg (or feed()) ─ reader thread ─▶ Ring (last 30 s, each chunk carries the read clock recv_s) ─▶ _consume() ─▶ run_stream
                                   │                                                                              ▲
                                   │  with no focus, the oldest are dropped                                       │ only while it holds the focus (_episode)
                                   └  with the focus, it waits for room (= the old pipe backpressure)             └ one transcription token per process
```

| Decision | Reason |
|---|---|
| A chunk enters the ring carrying the read-clock value | the subtitle time is `media_base + audio_s` at the moment of publishing, but decoding 30 s that pooled in the ring all at once stamps it 30 s late. The consumer **assigns** the chunk's value to `audio_s`, so the time is where that chunk sits |
| A change of the time base on reconnect also goes through a mark in the ring (`rebase`) | changing it outside the ring attaches the new base to old chunks still sitting in the ring |
| When the focus is lost, `run_stream` ends and the VAD, the history and the refiner are made anew at the next focus | all three are based on sample positions that put the start of run_stream at 0, so carrying them across a gap makes the lead-in region point at the wrong sound. The transcriber and translator shells are kept (the weights are shared) |
| The new focus takes the token only after the old focus has finished refining and let it go | two sessions running the same `tc.Model` at once is not guaranteed by the bindings. In the meantime the sound is in the new session's ring |
| A Group lives in memory only | after a restart the members are "interrupted" anyway, and regrouping is for the user to ask for |

Cost: one ffmpeg per session and 30 s of float32 (≈1.9 MB). Transcription and
translation are one focus only, so they are as before. From moving the focus to
the first subtitle is the old session's last refinement (≤1 s or so) plus the
time the new session takes to drain the ring.
