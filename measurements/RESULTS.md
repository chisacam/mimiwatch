# Measured results (2026-08-27)

*[한국어](RESULTS.ko.md)*

A measurement to find out whether hayamimi can be connected to a live stream, and how late the subtitles arrive. It took as its targets the items the requirements draft could not settle on paper.

## Targets

| | Stream A | Stream B |
|---|---|---|
| Channel | 桃鈴ねね (hololive) | 雪花ラミィ (hololive) |
| Character | Japanese chat live | Japanese chat live |
| Measured window | 130 s | 136 s |

Both streams were measured with the same settings. hayamimi was run with `--input ws --serve --mode single --lang ja --translate ko`, the audio-only HLS that yt-dlp resolved was converted to 16 kHz mono PCM with ffmpeg and sent to the WebSocket ingest, and the subtitles were received over SSE.

## 1. Verifying the path

| Item | Result |
|---|---|
| Does yt-dlp resolve the live audio | **It does.** It offers format 234 (audio-only HLS) |
| Is `EXT-X-PROGRAM-DATE-TIME` provided | **It is.** An absolute time is attached to every 2 s segment |
| From ffmpeg to the hayamimi ingest | **It works.** It connected without modifying `ws_ingest.py` |
| Audio supply rate | Slightly faster than realtime. 156 s worth was supplied in 150.8 s |

## 2. Measuring the delay

| Stream | Final subtitles | Refined lines | Final → translation | Final → refined (median) | Refined max | Over 5 s |
|---|---|---|---|---|---|---|
| A | 15 | 8 | 0.16 s | 2.28 s | 20.03 s | 6/15 (40%) |
| B | 26 | 9 | 0.27 s | 5.31 s | 20.07 s | 13/26 (50%) |

**Translation is not the bottleneck.** The translation delay of the local M2M-100 has a median of about 0.2 s.

**The bottleneck is the triggering of the refinement.** hayamimi's two-pass refinement waits for 2 s or more of silence before it runs, and for a speaker who talks without pausing, that silence rarely comes. Both streams were pushed out to as much as 20 s, and the share that went over 5 s was 40 percent and 50 percent.

**This is not a trait of an individual speaker but a structural problem.** The same pattern was reproduced with two different speakers.

## 3. The difference in recognition quality

Even with the same engine and the same settings, the recognition quality of the two streams differed noticeably.

**Stream A**
```
ちいかはね。映画見たからね。
プロフィール帳のちょうってどれだこれか。
```

**Stream B**
```
ゲストで出る曲聴いて覚えてるかいみたいな。
そうじゃないときは大体うん。何してるかな？
TIKTOKを見てるか。YOUTUBE見てるか。
```

In stream B the particles and endings are intact and the proper nouns are accurate too. In stream A the character name `ちいかわ` was smeared into `ちいかは`. The average length of a single final subtitle also differs: 15 characters for A against 21 for B.

**Implication**: when a user is unhappy with subtitle quality, it has to be possible to tell whether the cause is the settings or the source audio. The UI needs an indicator that exposes the state of the audio.

## 4. A defect in the loopback path

`ws_ingest.py` also has the ability to send subtitle events back over the ingest connection. This path loses events.

| Measurement | Result |
|---|---|
| 1st | Could not measure, because of a client bug |
| 2nd | Received 22 events, then stopped at 57 s |
| 3rd (engine started fresh) | 0 |
| Compared against SSE at the same time | **92 over SSE against 15 over WebSocket** |

The engine transcribed correctly all three times, so publishing itself is not the problem. **So the audio goes over WebSocket and the subtitles come back over SSE.**

## 5. Translation quality

The limits of the local M2M-100 418M showed themselves.

| Source | Translation | Problem |
|---|---|---|
| 社長とお料理企画 | 대통령과 요리 프로젝트 | `社長` (a company president) mistranslated as 대통령 (head of state) |
| まずホロメンいこうよ | 우선 호르몬이 되어야 한다 | The proper noun `ホロメン` mistranslated as 호르몬 (hormone) |

The translation speed is sufficient, so wiring up an external endpoint is **an option for quality, not for speed**.

## 6. Confirming the value of the refined line

```
final:    ちいかはね。映画見たからね。
refined:  ちいかわが今はやってます。ちいかわね。映画見たからね。
```

The refinement stage restored the character name. The decision to make the refined line the unit of translation is right on quality grounds. It only has to be solved together with the delay problem above.

## 7. Conclusion

1. **The path holds.** It runs from yt-dlp to hayamimi, and the existing ingest does not have to be modified.
2. **Subtitles are received over SSE.** The WebSocket loopback is not used.
3. **The refinement delay has to be solved.** Showing the final subtitle first and replacing it when the refined line arrives is the right approach. It requires no change to hayamimi, and it keeps something on the screen at all times.
4. **There is a basis for automatic alignment in live streams too.** `EXT-X-PROGRAM-DATE-TIME` can be used, with the manual offset kept as a secondary means.
5. **Improving translation quality is solved with an external endpoint.** The speed is already sufficient.

---

# Choosing the transcription model (2026-08-28)

Live transcription quality remained the biggest weakness, so transcribe.cpp was
taken as the runtime and the candidate models were measured. The sample is
8 pieces of 20 s each -- 4 from Japanese game streams
(`s_ja_600/1800/3000/4200`) and 4 from Korean seminars (`s_ko_60/400/900/1400`).

## 8. Speed

Apple Silicon Metal backend, against 20 s of audio.

| Model | Size | Speed |
|---|---|---|
| SenseVoice Small Q8_0 | 241 MB | 205x realtime |
| whisper-large-v3-turbo Q8_0 | 845 MB | 29~40x realtime |
| Fun-ASR MLT Nano Q8_0 | 850 MB | 23~39x realtime |
| Voxtral Mini Realtime Q8_0 (offline) | 4513 MB | 5x realtime |
| Voxtral Mini Realtime Q8_0 (streaming) | 4513 MB | 2.9x realtime |

Live only has to clear 1x realtime, so SenseVoice's 205x is headroom to spare.
Voxtral's 2.9x, on the other hand, carries one session but has almost no margin.

## 9. Voxtral Realtime: not adopted

It is a model that advertises low-latency streaming, so it was checked
separately. The same piece was run with the streaming latency varied.

| Latency setting | Output |
|---|---|
| 80 ms | 88 chars -- `これはお父さん` inserted (hallucination) |
| 480 ms | 81 chars -- stable |
| 2400 ms | 81 chars -- **exactly identical** to 480 ms |

**Raising the latency does not improve the quality.** It has already converged
at 480 ms, and below that hallucinations creep in. The quality does not surpass
Fun-ASR while the speed is more than 10 times slower, and speaker separation
turned out to be an API-only feature rather than something the model does.
**No grounds for adopting it were found.**

## 10. Does raising the SenseVoice quantisation make it better

The documentation's own table records F32, F16 and Q8_0 as all having the same
WER of 3.13%. Whether that also holds for Korean and Japanese was checked
directly against the 8 pieces.

- 2 of the 8 had **not a single character of difference in the output.**
- The remaining 6 differ by **only 1~2 characters**, and the direction is that
  F32 is slightly better.

| Piece | Q8_0 | F32 |
|---|---|---|
| s_ja_1800 | `やろうとてら` | `やろうとしてら` |
| s_ja_4200 | `カスタカスタマイズ` | `カスタムカスタマイズ` |
| s_ko_60 | `엔지니를` | `엔지니어를` |

F32 brings back the odd dropped syllable. But that is all it does, and it costs
3.7 times the size. And the real problem in Korean -- shortening `데이터독` to
`데이터` and `프리세일즈` to `프리세일` -- is not fixed by F32 either.
**This is a limit of the model rather than of the quantisation, so raising the
quantisation is not the answer.** We keep Q8_0.

## 11. Checking again against 4 live streams

The provisional conclusion from the 8 pieces of 20 s (Japanese = Fun-ASR) was
verified against real streams. 4 live streams were recorded for 180 s each, and
**the same audio** was fed to several models. Attaching them to a live stream
one after another would make each engine hear a different stretch, so the
comparison would not hold. The chunking used the same values as live
(max 4 s / silence 0.30 s), and all three engines produced the same number of
lines, so the chunks are identical.

| Stream | hayamimi | Fun-ASR | Whisper v3 turbo |
|---|---|---|---|
| Japanese solo (SMAvGS1YwCo) | 374 chars | **575 chars** | 533 chars |
| Japanese solo, talkative (AurxqUYrBuk) | 492 chars | **657 chars** | 604 chars |
| Japanese collab (lfBZHXZWqqg) | 370 chars | 614 chars † | **619 chars** |
| Korean solo (mxCUAACJ790) | 556 chars | — | 522 chars |

† The volume is similar but the content collapsed. See below.

### On solo streams Fun-ASR is ahead

Where hayamimi dropped whole clauses, Fun-ASR wrote them down, and the proper
nouns were accurate too.

| hayamimi | Fun-ASR |
|---|---|
| `ここ毎回な毎回やって` | `でもお任せも経験値強かったりしないかなでここ毎回な毎回やって` |
| `暢ちゃんとは` | `スバルちゃんとは` (the actual name) |
| `試合。` | `試合速度が応援を a ボタンでなんかあると` |

### But on collabs the language lock comes undone

Even though it was pinned with `-l ja`, Fun-ASR spat out other languages.

| hayamimi | Fun-ASR | Whisper |
|---|---|---|
| `クローゼット付き` | `開ける黒鉄付き` | `開ける?クローゼット付け` |
| `ちゃんとプライバシードアつけない` | `テンション高い!silプライバシーね…` | `高いプライバシーねちゃんとプライバシーどうつけない` |
| — | `tahu` / `truk` / `hơi vui` | — |

That is Indonesian and Vietnamese, and the internal token `!sil` leaked straight
through as well. Whisper's language never leaked on any of the three streams,
and on the collab it in fact wrote down the most.

### Korean

| hayamimi | Whisper |
|---|---|
| `말랑 통 아저씨가` | `그 말 안 통한 아저씨들` |
| `0 포 티랑 일하면` | `영포티랑 일하면` |
| `허러비스에서는` | `컬어비스에서는` |

hayamimi has more characters (556 against 522) but the sense does not come
through. That the judgement has to be made on content and not on volume was
already learned from the hallucination in `s_ja_1800`.

## 12. The per-language assignment (settled)

| Language | Choice | Speed |
|---|---|---|
| Japanese | **whisper-large-v3-turbo Q8_0** | 11~15x realtime |
| Korean | **whisper-large-v3-turbo Q8_0** | 18x realtime |
| Everything else | hayamimi RoutedASR | — |

In Japanese it concedes 7% to Fun-ASR on solo streams, but the side that does
not collapse on collabs was chosen. A model that survives everything is better
than one that only handles a single kind of stream well, and since both
languages use the same model only one 845 MB copy has to be resident.

Against Fun-ASR's 35~46x, Whisper is slower at 11~18x, but live only has to
clear 1x realtime.

## 13. Defending against hallucination

On `s_ja_1800` Fun-ASR repeated `おらおら` 117 times and manufactured 262
characters. SenseVoice handled the same stretch in 8 characters, so that stretch
is in fact nearly silent and the 262 characters are all hallucination. At first
this was wrongly rated as "the one that catches the most".

The 4-gram diversity separates the two cases cleanly.

| Stretch | Diversity |
|---|---|
| Normal speech | 0.94 |
| Repetition runaway | 0.02 |

The defence is two layers.

1. **The diversity check** -- a result of 40 characters or more whose 4-gram
   diversity is below 0.35 is thrown away. Verified against the 2176 real
   subtitles already in storage, with 0 false positives.
2. **The generation-cap exception** -- when a piece a few seconds long uses up
   all 256 tokens, `OutputTruncated` is raised. Human speech cannot do that, so
   it is treated as the same runaway and thrown away with it. This exception
   actually aborted a run in the middle of the collab comparison, and in a live
   session that is where the session would have died.

## 14. Verification in real live use

It was attached to the hardest stream, the collab, with the collab profile.

| Item | Value |
|---|---|
| Subtitles | 25 lines, all Japanese |
| Decoding | average 381 ms / max 793 ms |
| Delay to the screen | average 384 ms / max 795 ms |
| Hallucinations blocked | 0 |
| Errors | none |

Proper nouns and game terms such as `スバル`, `セブンデイズ・トゥーダイ` and
`寝袋` came out accurately. One of the 25 lines had a broken output like
`風料雨 Turnew add`, but the frequency is low and it recovered on the next line.

---

# Reviewing the Whisper quantisation levels (2026-08-28)

Because VOD transcription multiplies with the length, whether lowering the
quantisation shortens the time was checked. The measurement applied the same
chunking settings as VOD (max 12 s / silence 0.35 s) to 2 recordings of live
streams (180 s each).

## 15. Speed: it does not go down

| Quantisation | Size | Japanese | Korean |
|---|---|---|---|
| Q8_0 | 845 MB | 13.2x realtime | 14.4x realtime |
| Q6_K | 660 MB | 15.9x realtime | 13.9x realtime |
| Q5_K_M | 591 MB | 13.2x realtime | 14.8x realtime |
| Q4_K_M | 511 MB | 11.0x realtime | 14.3x realtime |

It merely wobbles between 13 and 16x realtime with no trend. Shrinking the model
by 40% does not shorten the time, and Q4_K_M in Japanese was in fact the
slowest.

The same result as the M4 Max measurement in the transcribe.cpp documentation --
38.4x against 38.1x on Metal, and only on CPU 1.4x against 1.9x. **On Metal the
weight size is not the bottleneck.**

## 16. run_batch: it is slower, in fact

The binding's `run_batch` is described as "about 2x the throughput on families
that have a batched-operation path", so it looked like a fit for VOD and was
measured alongside.

| | Sequential | Batched |
|---|---|---|
| Japanese (30 chunks / 117 s of speech) | 13.6 s | **21.1 s** |
| Korean (25 chunks / 86 s of speech) | 12.5 s | 13.5 s |

Whisper appears to fall into what the documentation calls "falling back to
running them one at a time", and on top of that the waste of padding to the
longest piece when the pieces differ in length seems to be added. That Japanese,
which has more pieces and a wider spread of lengths, got far worse points in
that direction.

## 17. Quality: the lower you go, the further it drifts

The comparison was made per chunk, against Q8_0 as the baseline. Comparing the
concatenated characters by position means that a single character shifted near
the front makes everything after it register as different, so it has to be
looked at per chunk.

| Quantisation | Japanese (30 chunks) | Korean (25 chunks) |
|---|---|---|
| Q6_K | 27 exact matches / similarity 0.955 | 21 / 0.981 |
| Q5_K_M | 24 / 0.958 | 19 / 0.958 |
| Q4_K_M | **16 / 0.932** | **11 / 0.872** |

The difference between Q6_K and Q5_K_M is mostly harmless -- `ねぇ` against
`ねぇー`, `아저씨는` against `아저씨가`, whether the `요` at the end of a
sentence is there.

Q4_K_M drops words.

| Q8_0 | Q4_K_M |
|---|---|
| `無理がいい機嫌なんだ。` | `いい機嫌なんだ。` |
| `でもそれでもずっとそのなんかバリオカートとか` | `でもそれでもずっとバリオカートとか` |

The direction is not one-way, though. Q4_K_M produced `最近回線の調子` instead
of `最近回戦の調子`, and since the context is a conversation about the
connection, **Q4_K_M is the one that is right.** It is more accurate to see it
not as the lower level always being worse, but as the spread of the output's
wobble getting wider.

## 18. Conclusion: we keep Q8_0

There is no speed gain, so there is no reason to give up even a little quality.

**Q6_K** is an option only when memory has to be saved. It uses 185 MB less
while 27 of the 30 Japanese chunks and 21 of the 25 Korean chunks match Q8_0
down to the character, with no speed penalty. Memory is not short right now, so
we are not changing it.

The remaining road to actually shortening VOD time is parallelising sessions.
Since the VAD already filters out the silence (117 s of speech out of 180 s of
Japanese), the processing time is proportional not to the length of the video
but to the time spent speaking, and at the current 13x realtime an hour-long one
takes 4~5 minutes.

---

# Comparing translation models: TranslateGemma 4B (2026-08-28)

**TranslateGemma 4B** (`google/translategemma-4b-it`, based on Gemma 3 4B,
55 languages), which Google released as a translation-only model, was attached
to the same subtitle lines as the **Gemma 4 E4B** in use today and compared.

## 19. Method

The sample is 63 lines. To make the distribution the same as what the subtitle
translator actually receives, they were drawn evenly along the time axis from
VODs.

| Source | Lines | Character |
|---|---|---|
| `MPSTWzF2ZKU` (ぶいすぽ game stream, ja) | 34 | fragments, interjections, repeated words |
| `71SnC4H-G1Q` (ぶいすぽ singing stream, ja) | 14 | song titles, calling out audience members |
| `jrLVa1Md4GU` (Datadog talk, en) | 12 | complete sentences, technical terms |
| The M2M-100 failure lines from section 5 above | 3 | fixed reference points |

Both models are Q4-class quantisation, `n_ctx=2048`, Metal, the same process
structure. **The prompts were not held the same** -- they cannot be held the
same (see section 21). Gemma 4 used exactly the prompt that
`translate.LocalGemma` actually sends (temperature 0.2), and TranslateGemma used
the official chat template embedded in the GGUF, rendered with jinja2
(temperature 0.0).

The code to reproduce it is in `bench/`.

```sh
.venv/bin/python bench/sample.py
.venv/bin/python bench/run.py gemma4
.venv/bin/python bench/run.py translategemma
.venv/bin/python bench/compare.py
```

## 20. Speed and size: TranslateGemma is better

| | Gemma 4 E4B q4_0 | TranslateGemma 4B Q4_K_M |
|---|---|---|
| File size | 5.15 GB | **2.49 GB** |
| Median | 0.18 s | **0.17 s** |
| p90 | 0.44 s | **0.34 s** |
| Max | 1.06 s | **0.79 s** |
| Total for 63 lines | 14.7 s | **12.9 s** |
| Failures | 0 | 0 |

At half the size it is a little faster. But as section 5 already confirmed,
**translation is not the bottleneck.** Whether it is 0.18 s or 0.17 s, the
arrival time of the subtitle does not change. The meaningful number here is not
the speed but **the 2.7 GB saved**, and nothing else.

## 21. You cannot swap it in as-is

TranslateGemma's chat template takes the source text, the source language and
the target language **as fields**. Put an English instruction in as a string and
the template raises an exception on its own.

```
ValueError: User role must provide `content` as a list with a single entry ...
```

`llama-cpp-python` applies the template baked into the GGUF as it is, so a
replacement that only changes the model path in `translate.py` **fails on all
63 lines** and quietly falls back to M2M-100. Adopting it would require running
a separate translation-only render path out of `LocalGemma`.

The rendered prompt looks like this.

```
<start_of_turn>user
You are a professional Japanese (ja) to Korean (ko) translator. ...
Please translate the following Japanese text into Korean:

社長とお料理企画<end_of_turn>
<start_of_turn>model
```

## 22. Quality: on complete sentences they are equals, on fragments they part ways

**On complete sentences it is hard to pick a winner.** Both were readable on the
12 lines of the English talk, and there were lines where TranslateGemma was
better.

| Source | Gemma 4 | TranslateGemma |
|---|---|---|
| And this famous conveys that message. | 그리고 이 유명한 **것이** 그 메시지를 전달합니다. | 그리고 이 유명한 **표현은** 그 메시지를 전달합니다 |
| リサイクルマークで選べない。 | 재활용 마크로 선택할 수 없습니다 | 재활용 **표시만으로는** 선택할 수 없습니다 |

**On fragments TranslateGemma makes things up.** It finishes, on the speaker's
behalf, a sentence the speaker did not finish. Writing out every one of the
63 lines where they visibly parted ways gives this.

| Source | Gemma 4 | TranslateGemma | What was added |
|---|---|---|---|
| やろっかー。 | 할까 | 어쩌라고? | The sense is reversed |
| いいだけ。 | 그냥 | 네, 좋습니다. | An agreement that was not there |
| って。 | 라고. | 네. | An agreement that was not there |
| 危ないだろ。 | 위험하잖아. | 위험한가요? | An assertion turned into a question |
| あっずっと角にいた。 | 아, 계속 구석에 있었어. | **그는** 계속해서 구석에 서 있었다. | A third-person subject that was not there |
| ずっとラブにくっついてくる。 | 계속 사랑에 매달려 와. | **'러브'라는 단어에** 얽매여 있다. | Turned into a comment about the word |
| なんも反応がないな。ゲームあっ。 | 아무런 반응이 없네. 게임 아. | 아무런 반응이 없네. **게임은 끝났어.** | An ending that was not there |
| この帽子ケイトルーツだろ。絶対帽子が。 | 이 모자 케이트의 것이잖아. 분명 모자가. | 이 모자는 케이트 루트 모델이 맞아요. **꼭 이 모자를 입어야 해요.** | An exhortation that was not there |
| そばにいてほしくて。 | 곁에 있어 **줬으면 해서.** | 제 곁에 **있어주세요.** | A wish turned into a command |
| おじいちゃんおばあちゃんたちよ。ろこぶんじゃないか。 | 할아버지, 할머니들. 즐거운 일이네요. | 할아버지, 할머니, **혹시 괜찮으신가요?** | An enquiry after their health that was not there |
| えっ。 / あっ。 / あ。 | 네? / 아. / 아. | 음. / 음. / 음. | Flattens all three into one |
| 矢印押すのWSDって…方向器を押すだけだったら私。結構得意で楽しかった。 | 화살표 누르는 게 WSD라면 어렵겠지만, 방향키만 누르는 거라면 저. 꽤 잘했고 즐거웠어요. | (repeats the same clause twice, growing to twice the length of the source) | Duplicate generation |

The same tendency showed up twice in English as well.

| Source | TranslateGemma | Problem |
|---|---|---|
| It'll **at best** reduce the performance … and **at worst** | **최악의 경우**, 이는 앱 및 인프라의 성능을 저하시킬 뿐입니다. | at best and at worst were flipped |
| DNS sniffers or dig into VPC logs for managed cloud services … | … VPC 로그를 분석하여 **악성 행위를 탐지할 수 있습니다.** | Attached a purpose that is not in the source |

The attitude also differs when it meets a proper noun it does not know.
`メンゲン` (a streamer's abbreviation for a members-only stream) was left as
`멘겐` by Gemma 4, while TranslateGemma made up `보컬 그룹` (a vocal group).
Left as it is, a viewer can go and look it up; made up, they never know it is
wrong.

**This is not the temperature's fault.** TranslateGemma was run at 0.0 (greedy)
and Gemma 4 at 0.2. The side that was sampled more deterministically made more
up.

The same direction shows in the length too. These are the median output/source
character counts.

| Source length | Gemma 4 | TranslateGemma |
|---|---|---|
| 7~20 chars | 1.13x | 1.28x |
| 21 chars or more | 0.70x | 0.89x |

## 23. Conclusion: we are not changing it

TranslateGemma 4B is a well-made translator. At half the size it is a little
faster, and on complete sentences it is the equal of Gemma 4 E4B or better. If
what you are translating is a document, it is a good choice.

**But what mimiwatch translates is not documents.** 38~55% of live speech is
fragments of six characters or fewer (section 5), and in that range
TranslateGemma fills in what it does not know plausibly instead of leaving it
empty. In subtitles this is the worst possible mode of failure -- a viewer
notices that M2M-100's `⁇` or `호르몬` is a mistranslation, but "게임은 끝났어"
they cannot notice. `looks_broken` cannot filter a sentence like that out
either. Because it makes sense.

Three things catch together.

1. **It makes things up on fragments.** 13 of the 15 lines that visibly parted
   ways were TranslateGemma's invention.
2. **The gain does not arrive as subtitle delay.** Translation is already not
   the bottleneck, so what is left is 2.7 GB of disk and nothing else.
3. **It is not a free swap.** A translation-only prompt path has to be newly run
   out of `translate.py` (section 21).

**The conditions for looking again** are these. When 2.7 GB of disk actually
becomes a problem, or when the fragment problem is solved outside the translator
-- that is, changing it so that refined lines are merged into sentences before
being translated makes the very range TranslateGemma is weak in disappear. At
that point it is worth measuring again. **12B/27B were not measured.** What 4B
revealed is not a shortage of capacity but a learned tendency towards fragments,
so there is no guarantee that scaling up makes it better.

---

# Genre presets and context (2026-08-28)

After deciding in section 23 not to change the model, the remaining question was
**how to use the same model better**. The failures of subtitle translation came
from the character of the speech rather than from model capacity, so what
happens when the prompt is matched to that character was measured.

## 24. Method

The **same 63 lines, the same Gemma 4 E4B q4_0, the same temperature 0.2** as
section 19. The only thing that changes is the prompt. Four variants were each run.

| | Prompt | Preceding subtitles |
|---|---|---|
| `generic` | the one set used so far | none |
| `preset` | per genre | none |
| `generic-ctx` | the one set used so far | 3 lines |
| `preset-ctx` | per genre | 3 lines |

The preset and the context were measured separately because if both are turned
on at once and the result gets better, there is no telling which one did the
work. The genre was attached according to where the sample came from -- `gaming`
for the game stream, `music` for the singing stream, `tech` for the talk.

```sh
.venv/bin/python bench/run_prompt.py {generic|preset|generic-ctx|preset-ctx}
.venv/bin/python bench/compare4.py
```

## 25. The preset: it brings back the names the transcriber mangled

The clearest gain came from the tech talk. **The translator restores from
context** a product name that Whisper mangled. The translator is the only point
that knows this line comes from a cloud talk.

| Source (transcription) | generic | preset |
|---|---|---|
| …managed cloud services like **RAT fifty three**. | …**RAT fifty three**와 같은 관리형 클라우드 서비스의… | …**Route 53**과 같은 관리형 클라우드 서비스의… |

Some were not brought back. `Cordian S`(→ Consul) and `Data Doc`(→ Datadog)
were left exactly as they were by all four combinations. The preset is not a
cure-all; it works **when the context is narrow enough**.

The second gain is register. The game stream subtitles read like a stream.

| Source | generic | preset |
|---|---|---|
| コメント見なければこんなもんですか？ | 코멘트 안 보면 이런 건가요? | 댓글 안 보면 이렇냐? |
| ねっめっちゃ怖い。 | 와, 진짜 무서워. | 헐 진짜 무서워 |
| 私グリンピースでした。バギ。 | 저는 그린피스였습니다. 바기. | 나 그린피스였어. 바기. |
| リサイクルマークで選べない。 | 재활용 마크로 선택할 수 없다. | 리사이클 마크로 못 고르겠네. |

**The preset has regressions of its own.** The first version of the game preset
over-applied "leave proper nouns alone" and turned `玉を打って` into
**`玉을 쳐서`** -- it left the Japanese kanji standing. Adding "leave names as
names, but always carry common nouns over" fixed it to `공을 쳐서`. A genre
prompt does not come out right the first time, and it needs a procedure that
runs it against this sample again.

## 26. Context: it was a loss until the boundary was tightened

Once the 3 preceding subtitle lines were attached, **the model translated a
reference line instead of the target line.** 4 lines out of 63.

| Source | Preceding subtitles | First version's output |
|---|---|---|
| ここまで。 | …ノアちゃんが…オススメしてくれた曲の中の一つ。 / はい。 / 今回Vスポの… | **이번에 V스포 보컬 노래 방송으로요.** |
| ふだんの歌ってさ。… | …この歌枠はなんと残ります。 | **평소 노래 방송은 멘겐으로 가지만, 이번 노래 방송은 남아요.** |
| It'll **at best** … and **at worst** | There is no way it's DNS. / But somehow it was DNS. | **최악의 경우** (the whole sentence is gone) |
| とっとさんなんですけど。二十五分から… | ちょっと。 / いやでも雑談する。 | **그냥.** |

The first version's context block wrote "for reference" once and then simply
left the target line after it. The cause is that three reference lines sat
between the instruction and the target, so the instruction drifted away. **Once
a boundary was raised one more time right in front of the target line,** all
four disappeared.

```
Context -- these ja lines came before and are NOT to be translated:
- 狙われ。
- 笑われました。
- 心棒が。

Now translate only this one ja line:
って。
```

After the boundary was tightened, the context works only in the direction of
**narrowing the meaning**.

| Source | Preceding subtitles | Without context | With context |
|---|---|---|---|
| 束縛強め。 | ププッ。/ やめ。/ 仲よく仲よ。 | 구속 강함. | **집착 강해.** |
| うちに心奪われるなんてことあるはずないでしょ。 | あ。/ 聴いてください。/ リック。 | **집에서** 마음을 빼앗기다니… | **우리에게** 마음을 빼앗긴다는 일은… |
| And this famous conveys that message. | Maybe my applications aren't able to communicate… | 이 유명한 **것이** | 이 유명한 **문구가** |

**It costs almost nothing.**

| | generic | preset | generic-ctx | preset-ctx |
|---|---|---|---|---|
| Median | 0.19 s | 0.19 s | 0.19 s | 0.20 s |
| Total for 63 lines | 14.1 s | 13.6 s | 15.4 s | 15.2 s |

3 context lines cost 8% more. Since section 5 confirmed that translation is
not the bottleneck, the arrival time of a subtitle does not change.

## 27. Conclusion: we turn both on

Of the 63 lines, only **16** are identical down to the character between
`generic` and `preset-ctx`. The other 47 changed, and the direction of the
change was mostly right. Compared with changing the model (a 2.5GB download, the
plumbing work of section 21), fitting the prompt is **far cheaper and has a
bigger effect.**

The implementation goes like this.

- The five genres (`General` `Tech talk · seminar` `Game stream`
  `Chat · variety` `Song · lyrics`) are picked in 「＋ Add video」. They apply to
  both live and VOD.
- The chosen genre stays with the transcription. It is not asked again when
  translating with another backend.
- The 3 preceding subtitle lines are passed along. In live, only lines
  **earlier** than this one are picked (a refined line has already dropped the
  lines it absorbed, and later lines that came in while waiting are still
  there). In VOD the later side is not passed either -- using information live
  does not have would make the two paths' translations diverge.
- M2M-100 receives the context but does not use it. It is a model that carries
  one sentence into one sentence, so there is nowhere to attach it.

**Remaining limits.** There is one genre per video. Chat in the middle of a talk,
or a song in the middle of a game stream, does not get that stretch translated
differently. And the preset wording is itself something to be measured -- as
with `玉을 쳐서` in section 25, fixing one line spoils another. When you change
a preset, run `bench/run_prompt.py` again.

---

# Number of context lines (2026-08-28)

Section 27 decided to pass 3 preceding subtitle lines, but **3 was not a number
arrived at by measuring.** Only "3 lines versus none" was measured; 1·2·5·8 were
never compared against each other. They are measured here.

## 28. Method

The same 63 lines as section 24, the same model, the genre preset on, and only
the number of context lines changed to 0·1·2·3·5·8. The sample holds up to the
8 preceding lines and is cut from the back -- a near line is worth more than a
far one.

**Each condition was run four times.** At temperature 0.2 a single result was
taken to be no more than one sample -- and **all four were identical down to the
character.** That means it is effectively deterministic at this setting, so the
results below are a reproducible tendency and not sampling noise. It was not
measured across changing seeds, though, so this is not the same as saying
"statistically robust".

```sh
for n in 0 1 2 3 5 8; do .venv/bin/python bench/run_prompt.py ctx$n; done
.venv/bin/python bench/sweep.py        # metrics
.venv/bin/python bench/sweep_agg.py    # aggregates the repeats too
```

## 29. From 5 lines on, the model stops carrying it over

There is no way to score quality automatically, so proxy metrics were looked at,
and one of them separated the conditions -- **lines with Japanese kana left in
the output.**

| Context lines | Source leaked (of 252 lines over 4 runs) | Leaked line | Failures | Median |
|---|---|---|---|---|
| 0 | 0 | — | 0 | 0.18 s |
| 1 | 4 | `ラムネ先輩見つけちゃったー。` | 0 | 0.19 s |
| 2 | 4 | 〃 | 0 | 0.20 s |
| **3** | **4** | 〃 | 0 | 0.20 s |
| 5 | 8 | 〃 + **`って。`** | 0 | 0.20 s |
| 8 | 8 | 〃 + **`って。`** | 0 | 0.20 s |

The leak at 1~3 lines is a single long-vowel mark in `찾았다ー`. It carries a
drawled delivery over, so it is hard to call it a flaw.

**From 5 lines on, `って。` comes back as `って`.** It hands the source back
untranslated. All four runs were the same.

This is the worst way to fail. `looks_broken` does not catch it -- it is not
empty, not longer than the source, and there is no `⁇`. On screen, Japanese
appears where the Korean subtitle should be.

Speed does not separate them. The difference in median between 0 lines and 8
lines is 0.02 s.

## 30. 1 line is not enough, and 2 and 3 are a sheet of paper apart

Between adjacent steps 17~24 lines changed, but **most of them are synonym swaps
rather than improvements** (`노력할게요` ↔ `노력하겠습니다`, a full stop present
or not). Picking out only the ones where the meaning changed gives this.

**1 → 2**: with 1 line the back half of a long sentence is lost.

| Source | ctx1 | ctx2 |
|---|---|---|
| あなたはどうして生きてるの？**百文字以内で答えよ。** | 당신은 왜 살고 있나요? | 당신은 왜 살고 있나요? **백자로 답하세요.** |
| 玉を打って倒してください。 | **구슬** 쳐서 쓰러뜨려 줘. | **공을** 쳐서 쓰러뜨려 주세요. |

**2 → 3**: three lines are put right and one is spoiled.

| Source | ctx2 | ctx3 | |
|---|---|---|---|
| うちに心奪われるなんてことあるはずないでしょ。 | **집에서** 마음을… | **우리에게** 마음을… | better |
| まくります。 | **박아버릴게요.** | 막을게요. | better |
| づける。 | 달다 | 붙인다. | better |
| いいだけ。 | 좋기만 해. | 좋은 만큼 | worse |

**3 → 5**: apart from the `って。` leak it is noise.

## 31. Conclusion: we keep 3, but the evidence is thin

| Lines | Verdict |
|---|---|
| 0 | No gain from context |
| 1 | Loses the back half of a long sentence |
| 2 | Usable |
| **3** | **The default.** Three lines 2 got wrong are put right and one is spoiled |
| 5·8 | Lines appear that hand the source straight back |

**The grounds for ruling out 5 and above are clear** -- it is a reproducible
source leak, and the current `looks_broken` does not filter it. **Between 2 and
3 they are not.** It is a net gain of two lines out of 63, a margin that another
sample could overturn.

Reasons to change it, should they arise, are these. Switching to another model
(the leak threshold will differ from model to model), the source language
changing (this sample is 51 Japanese lines and 12 English), or using a paid
endpoint (3 context lines are money). Run `bench/run_prompt.py ctxN` again then.

**The remaining hole.** `looks_broken` does not catch a translation that hands
the source straight back. It does not show at the current default, but it is the
place it will show if the context is lengthened or the model changed.

---

# Low-spec machines: what to run where (2026-08-28)

A question that came from the reporter on issue #1 (Ryzen 7 7840HS · Radeon 780M
integrated graphics). The model loaded, but decoding took 3 seconds and a little
later the runtime died.

## 32. One chunk's failure was ending the whole stream

`_drain` called `asr.transcribe()` wrapped in nothing. So when the GPU driver
fell over on a single chunk, that exception went straight through `run_stream`
up to `_run` and the session ended. The refinement path was already confining
the failure of one utterance group (`stream.py`'s Refiner); only the fast path
was empty.

Now a single failure drops that chunk and moves on. But if the device really has
died there is no use in going on trying, so after **five failures in a row** it
does let go then. A success returns the count to 0, so the occasional failure
does not cut the stream off. The same thinking as `translate.py`'s breaker.

## 33. We add a light transcriber

SenseVoice Small, already measured in section 8, goes in as an option. The same
20-second chunk was measured again on this machine (M5 Pro).

| Model | Size | GPU (Metal) | **CPU (8 threads)** |
|---|---|---|---|
| whisper-large-v3-turbo Q8_0 | 845 MB | 56.8× realtime | **7.7× realtime** |
| SenseVoice Small Q8_0 | 241 MB | 272.8× realtime | **62.4× realtime** |

**It is 8× faster on the CPU.** And the light one running on the CPU is faster
than the heavy one running on the GPU. On a machine where integrated graphics
struggles, that is a large difference.

Quality is not a one-sided loss. On the same chunk there are places SenseVoice
put right.

| | whisper-large-v3-turbo | SenseVoice Small |
|---|---|---|
| | 相手も広う**同場内**で | あえてもいれ**工場内**で |
| | **できた中で**他のプレイヤー | **できた銃で**他のプレーヤー |

`工場内` (inside the factory) and `できた銃で` (with the gun that was made) are
the correct ones. In exchange SenseVoice inserts no punctuation or spacing, so
it comes out as one lump.

**It does not change the default.** The judgment of section 12 -- pick the one
that does not collapse on a collab stream -- stands. The clipping of Korean
proper nouns confirmed in section 10 (`데이터독` → `데이터`) is still there too.
But a machine that struggles with the default has to have an option.

It goes into `backends.json` and is picked from the 「Transcription」 picker on
screen.

```json
{ "id": "tcpp-lite", "backend": "tcpp",
  "model": "SenseVoiceSmall-Q8_0.gguf", "device": "cpu" }
```

## 34. For English, 74MB is enough

An English-only light model was measured too. The sample is three 18-second
chunks from the Datadog talk (`jrLVa1Md4GU`).

| Model | Size | GPU | **CPU (8 threads)** |
|---|---|---|---|
| whisper-large-v3-turbo Q8_0 | 845 MB | 69.1× realtime | 7.9× realtime |
| SenseVoice Small Q8_0 | 241 MB | 270.2× realtime | 63.1× realtime |
| **moonshine-base Q8_0** | **74 MB** | 89.7× realtime | **91.8× realtime** |
| moonshine-tiny Q8_0 | 34 MB | 151.1× realtime | 169.3× realtime |

**Moonshine is faster on the CPU than on the GPU.** The model is small enough
that the transfer cost exceeds the compute cost. For a model like this
`device: cpu` is a gain, not a loss.

Quality goes like this. At the 60-second mark whisper and moonshine-base **did
not differ by a single character, punctuation included.**

| Model | At the 60-second mark |
|---|---|
| whisper (845MB) | …find and communicate with both internal and external endpoints they're dependent on. |
| moonshine-base (74MB) | …find and communicate with both internal and external endpoints they're dependent on. |
| SenseVoice (241MB) | …find and communicate with both internal and external endpoints that they're dependent on |

**SenseVoice inserts no punctuation or capitals in English.** `dns` comes out in
lowercase and, with no full stop, it becomes one lump. At the 180-second mark
moonshine-base actually placed the comma accurately (`approaches, there are`).

moonshine-tiny (34MB) is twice as fast again but gets things wrong -- it turned
`how it affects` into `how to affect`, and `before it affected` into
`before defective`. **We pick base.**

### What being English-only means

Moonshine refuses other languages outright.

    UnsupportedRequest: transcribe_run: unsupported language (status 10)

Left alone, no subtitles come out, then the session ends and the same exception
piles up in the log. It is a failure a retry cannot change, so **it is asked in
advance with 0.1 s of silence when the session is created** (30 milliseconds
with moonshine). You find out the moment you start, not after taking 20 seconds
of the stream.

If the language is left empty (auto-detect) it is not asked. Feed it Japanese
instead and it hallucinates in English, and the diversity check of section 13
catches that -- in the test a repetition of `I'm going to tell you.` (diversity
0.07) was actually blocked.

## 35. What is left

**The cause of the 3-second delay is still unknown.** It is the figure for
running whisper 845MB on a 780M through Vulkan, and this machine does not have
that combination, so it cannot be reproduced. The reason it died cannot be
settled either, with no traceback -- but with the fix of section 32 it now drops
only that chunk instead of dying, and has to fail five times in a row before
letting go. Five lines of exception are left in the log then, so the cause shows.

---

# Initial loading (2026-08-28)

There was a report of waiting more than 3 seconds on every refresh. All that is
on screen is the list and the subtitles, so there seemed to be no reason for it.

## 36. 4.4 seconds of the 4.4 were someone else's script

Measured with the browser's Resource Timing.

| | Start | End |
|---|---|---|
| `/static/app.css` | 9ms | 20ms |
| `/static/app.js` | 9ms | 20ms |
| **`youtube.com/iframe_api`** | **9ms** | **4405ms** |
| `/api/backends` | 4435ms | 4437ms |
| `/api/videos` | 4444ms | 4452ms |
| `/api/live/sessions` | 4459ms | 4461ms |
| `/api/videos` (again) | 4463ms | 4470ms |
| `/api/live/sessions` (again) | 4470ms | 4471ms |
| `/api/video/<id>` | 4474ms | 4476ms |

**Everything of ours is under 10ms.** The server, the subtitle files and the
list are all fast. `domInteractive` being 4435ms is because
`<script src=".../iframe_api">` is a synchronous script and the parser was
stopped there. Until that script arrives, `init()` cannot send even its first
request.

`app.js`'s `whenApiReady()` was already handling the late-arrival case with the
`onYouTubeIframeAPIReady` callback and polling. **There was never any reason to
block on it.**

## 37. After the fix

One word, `async`, plus a tidy-up of the duplicate requests at startup.

| | Before | After |
|---|---|---|
| domInteractive | 4435ms | **24ms** |
| domContentLoaded | 4436ms | **25ms** |
| Last API response | 4476ms | **39ms** |
| Server requests | 8 (two of them duplicates) | **6** |
| List on screen | ~4.5 s | **39ms** |

`iframe_api` still takes 4.2 s -- it is a round trip to Google, not something we
can shorten. But it now blocks nothing. The screen, the list and the script
stand up within 40ms, and only the player settles in late.

The duplicated `/api/videos` and `/api/live/sessions` were removed by passing
what `init()` received on to `refreshVideoList`. Changing the three to be sent
side by side is not a big share -- some 15ms altogether -- but there was no
reason for them to wait on one another.

The list thumbnails get `fetchpriority="low"`. When fourteen of them pile onto
the same host, the player embed queues up behind them.

## 38. A live session started on auto-detect had not one line translated

While attaching tab audio ingest, a test that measures whether the path carries
through to the end (`bench/tab_ingest.py`) was used, and transcription was fine
while translation came out at 0. At first it looked as if the test had been
written wrong -- that recording was Korean, so there was nothing to carry over.
Switching to a Japanese recording still gave 0.

The cause had nothing to do with tab ingest. **A live session with the source
language left on 「Auto-detect」 was going untranslated, whatever kind it was.**

The language `tcpp_asr.TranscribeCppASR.transcribe()` puts on the subtitle was
`self.forced_lang`. That is the right value if the language was pinned, but on
auto-detect it is an empty string. The runtime knew.

```
>>> session.run(pcm, language="").language
'ja'
```

That value was thrown away and `""` was sent instead, and
`LiveSession._translate` simply turns back when it does not know the source
language.

```python
src = cue.get("lang") or self.lang or ""
if not src or src == self.viewer_lang:
    return
```

There was one more of the same on the refinement path. `stream.Refiner` was
passing `self.asr.forced_lang` straight through, so even with the final fixed
the refined line still went out with no language.

**Why it went unseen this long.** No error is raised. Subtitles pile up
normally and only the translation quietly drops out. And most of the tests we
used ran with the language pinned -- that path never had the problem.

After the fix, the same 50-second stretch:

| | Before | After |
|---|---|---|
| Subtitles | 10 lines | 10 lines |
| Language attached to the subtitle | `''` | `ja` |
| Translations | **0** | **14** |

It is the same on a real stream (HLS). 66 seconds of a Japanese stream started
on auto-detect gave 11 subtitle lines and 17 translations. Before the fix that
session would have been 0.

## 39. Subtitles come out of sound the browser uploads too

A members-only stream is one the server cannot take. Instead the browser uploads
the sound of the tab the user is already listening to. Nothing on the back end
changes -- what `run_stream` takes is only a generator that yields float32
chunks, so a queue goes where the ffmpeg pipe was.

The transport is a chunked POST, not a WebSocket. 16kHz mono int16 is 32KB per
second, so a 2-second chunk is 64KB in one request at 0.5 requests per second.
There is nothing to gain against the price of laying a handshake and framing on
top of the standard library's `http.server`, and the latency is dominated by the
chunk length, not by the transport.

The browser-side chain was measured with a synthetic tone (the share dialog only
opens when a person picks, so only the stream was swapped in).

| | |
|---|---|
| AudioContext sample rate | 16000 (Chrome does the resampling as well) |
| Samples uploaded over 9 seconds | 144,256 (= 9.02 s) |
| `audio_s` the server consumed | 8.0 (the last chunk is still on its way) |

**One thing was caught here.** A new `AudioContext` is born `suspended` because
of Chrome's autoplay policy. In that state the worklet never runs once, so the
sample count is 0, and no error is raised anywhere -- the screen says
「receiving」 and only the subtitles do not grow. `ctx.resume()` was added, and
if no sound arrives within 4 seconds it now says so.

Values confirmed end to end with a real recording (50 seconds, Japanese,
refinement on):

| | |
|---|---|
| Uploaded / consumed by the server | 50 s / 50.0 s |
| Maximum piled up in the queue | 2.0 s (it keeps up with realtime) |
| Audio thrown away | 0 s |
| Subtitles / translations | 10 lines / 14 |

## 40. Chrome does not tell us the title of the shared tab

A session started from tab sound is left in the list as no more than
「Tab audio」. That one line is all there is to tell what was listened to, so two
of them standing side by side cannot be told apart.

The name of the chosen target was expected to come in on the `label` of the
video track `getDisplayMedia` returns, so that was what got used. Read from a
session that is actually sharing, it goes like this.

```
{ label: 'web-contents-media-stream://8D6FD737C5BFC47DBCE78F63FA28FECB',
  surface: 'browser' }
```

**It is an opaque identifier, not the tab title.** `surface` is `browser`, so a
tab was indeed picked; it is Chrome that does not hand the title over. The
filtering rule was left as it was -- using that string as the title is worse
than 「Tab audio」. Picking a window or a screen brings that name in, and another
Chromium build may give the title, so the function was left in place to use it
if it comes.

So the name went the way of **fixing it afterwards**. `POST /api/live/title` and
「✎ Name」 beside the title. A finished session can be fixed too -- in place if
it is an in-memory session, otherwise in the store. What you were listening to
usually becomes a problem when you look at the list after listening to it all,
so this one will be used more often.

It is not attached to a session taken from a URL. yt-dlp fetches the title and
fetches it again on resume, so fixing it would only revert.

---

# Re-check measurements (2026-08-29 evening)

The reference survey (`.claude/review-2026-08-29-2.md`) recommended two things,
and they were measured against this repository's five sample recordings
(`data/*.wav` — 2 Japanese VTuber streams, 2 Korean talks, 1 English talk). The
scripts are `bench/vad_ab.py`, `bench/whisper_ab.py`.

## 41. Silero VAD v5 drops speech on our samples — keeping v4

Going by the figures in the Silero wiki alone (ROC-AUC v4 0.91 → v5 0.96, fewer
false positives on noise), v5 is the better model. But put the same sample
through both models and **the total seconds judged as "speech" splits by what
kind of broadcast it is.**

| Sample | Language · character | v4 speech (s) | v5 speech (s) | Note |
|---|---|---|---|---|
| MPSTWzF2ZKU (108 min) | Japanese collab, BGM | 4,072 | **413** | v5 throws away 90% |
| 71SnC4H-G1Q (26 min) | Japanese, singing included | 854 | **84** | same |
| d1QeXqS53nA (83 min) | Korean talk | 2,342 | 1,124 | half |
| 9zlrjY2Upxo (30 min) | Korean talk | 1,520 | 1,522 | same |
| jrLVa1Md4GU (6 min) | English talk | 286 | 295 | same |

(broadcast profile, min_silence 0.30 · max_speech 4.0. All three profiles show
the same pattern.)

On a clean talk the two agree, and **on a VTuber broadcast with BGM underneath
and a high voice v5 sees most of it as silence.** The weakness Silero's own v6
release notes write down — "instrumental music that sounds like a human voice,
very high voices (synthetic, cartoon, children)" — is exactly this genre. That
genre is this tool's main target, so **we stay on v4 (the k2 re-export,
643KB).** v5 sits in the 「Models & Tools」 list as an option only
(`MIMIWATCH_VAD_MODEL=silero_vad_v5.onnx`), so someone who only watches talks
and lectures can pick it when false positives on noise bother them. v6.2
(improved on high and cartoon voices) is not supported by sherpa-onnx yet, so it
stays a thing to watch.

## 42. Tightening the Whisper thresholds: no difference — keeping the defaults

We passed `no_speech_thold 0.84 · logprob_thold -1.3`, what WhisperJAV uses for
Japanese subtitles, through transcribe.cpp's `WhisperRunOptions` and put it
against the defaults (0.6 · -1.0). The sample is 600~1200 s of the hardest
collab, MPSTWzF2ZKU, 126 segments on the broadcast profile.

| Setting | Empty segments | Hallucinations blocked | Characters | Decode |
|---|---|---|---|---|
| Default | 0/126 | 0 | 2,244 | 46.1s |
| Tightened | 0/126 | 0 | 2,212 | 45.9s |
| Tightened + condition_on_prev_tokens=False | 0/126 | 0 | 2,166 | 45.4s |

Raising the thresholds dropped **not a single segment into empty** — on a
4-second chunk the no_speech probability never climbs that high. The 20 segments
where the two settings differ were all garbage output from places where several
people talk over each other (`isi を作って Six` ↔ `BBの固を持って…`), and
neither could be called the better one. There are no ground-truth subtitles, so
we could not compute WER. **We keep the defaults.** The knobs were left in place
-- write `"whisper": {"no_speech_thold": …}` into the `asr_backends` entry and it
is passed straight through, and with `"refine_prompt": true` the previous refined
line is handed to the refinement pass only as `initial_prompt` (not fed to the
fast pass; section 43 to come).

This slice never once tripped the diversity check of section 13. The
`ほんとに見てない` repetition seen at the 120-second mark came from decoding a
20-second chunk whole; it does not appear on the chunks VAD cut to 4 seconds --
which means cutting short is itself hallucination suppression.

# Measuring against ground-truth subtitle samples (night of 2026-08-29)

## 43. The samples and the scoring

We took as ground truth four music videos for which YouTube provides **manual**
original-language subtitles (`bench/gold_fetch.py`, `data/gold/`). Three
Japanese (音乃瀬奏 You＆合図 · TAK ニャニャニャチュニャ · ヨルシカ 晴る) and one
English (Mili Fly, My Wings). Scoring is `bench/gold.py` -- cut on the same path
as live (VAD → 1 s of preroll → decode the chunk), then join the whole subtitle
together and take CER (Japanese) / WER (English). Furigana, credits and repeats
in the karaoke-style subtitles were stripped out.

**Singing is the worst case for transcription**, so the absolute values are high
(baseline CER 47~80%). A spelling the writer chose on purpose, like `晴る/春`,
counts as an error too. So the tables below have to be read as the **difference**
between settings. At this sample size (1,490 ground-truth characters) run-to-run
variation is about ±1p (the same setting twice: 63.7 / 64.4).

## 44. Every Whisper knob stayed inside the noise; only the VAD threshold moved anything

whisper-large-v3-turbo Q8_0, talk profile (12 s) as the reference.

| Setting | ニャニャ | 晴る | You＆合図 | Fly(en) | Overall |
|---|---|---|---|---|---|
| Baseline (talk, VAD 0.5) | 68.5 | 80.4 | 47.0 | 55.8 | **63.7** |
| broadcast profile (4 s) | 70.4 | 85.2 | 51.4 | 62.0 | 67.4 |
| interview profile (6 s) | 74.3 | 83.5 | 50.9 | 60.1 | 68.1 |
| Trailing padding 0.3 s / 0.5 s | 69.2 / 72.6 | 84.6 / 83.8 | 47.0 / 47.0 | 58.9 / 52.1 | 65.3 / 65.6 |
| Diversity check off / floor 0.2 | 70.5 / 66.0 | 80.4 | 47.0 | 54.6 / 56.4 | 64.3 / 62.9 |
| no_speech 0.84 · logprob -1.3 | 72.2 | 80.4 | 47.0 | 54.6 | 64.9 |
| condition_on_prev_tokens | 71.5 | 80.4 | 47.0 | 57.7 | 65.0 |
| Temperature fallback off | 73.7 | 80.4 | 47.0 | 54.6 | 65.4 |
| **VAD threshold 0.4 / 0.3 / 0.2** | 73.9 / 71.7 / 74.3 | 72.9 / **53.1** / 55.9 | 55.3 / 52.1 / 56.0 | 49.7 / 49.1 / 44.2 | 65.6 / **59.0** / 61.2 |

Three things read out of this.

- **The Whisper threshold, prompt and fallback knobs changed nothing on this
  sample.** Two of the samples did not differ by a single character -- which
  means the thresholds never fired once. Same conclusion as section 42: the
  knobs stay in the settings, the defaults stay as they are.
- **The shorter the cut, the worse it gets** (12 s 63.7 → 6 s 68.1 → 4 s 67.4).
  Cut a song in the middle of a phrase and what came before and after is gone.
  The short ceilings of the live profiles are values chosen on purpose for
  latency and for what gets dropped (section 2, the `live.PROFILES` comment), so
  they do not change -- but it means the refinement pass bringing it back at
  12~25 s matters that much.
- **The VAD threshold was the only gain.** At 0.5, only 57 of `晴る`'s 277
  seconds were judged speech -- it saw the singing over the accompaniment as
  silence -- and at 0.3 that grows to 177 seconds, with CER 80 → 53. Overall
  64.4 → 59.0. 0.2 picks up the accompaniment as well and goes bad again.

**Side effects on conversational samples** (5 min of a Korean talk, 5 min of a
Japanese collab, 5 min of an English talk; broadcast profile):

| Sample | 0.5 speech (s) | 0.3 speech (s) | Empty segments | Hallucinations blocked |
|---|---|---|---|---|
| Korean talk | 294 | 292 | 0 → 0 | 0 → 0 |
| Japanese collab | 258 | 284 | 0 → 0 | 0 → 0 |
| English talk | 276 | 277 | 0 → 0 | 0 → 0 |

It is neutral on conversation and a gain on singing and BGM, so **we lower the
default threshold to 0.3** (`stream.VAD_THRESHOLD`, put back with
`MIMIWATCH_VAD_THRESHOLD`).

## 45. Multilingual alternatives: on singing, whisper-turbo is the best of them

The models transcribe.cpp supports that cover JA and KO, on the same samples.
Overall error rate, VAD 0.5 / 0.3.

| Model | Size | Overall (0.5 / 0.3) | Speed (Mac Metal) |
|---|---|---|---|
| **whisper-large-v3-turbo Q8_0** | 845MB | **64.4 / 59.0** | 16~62× realtime |
| SenseVoice Small Q8_0 (CPU) | 241MB | 70.0 / - | - |
| Fun-ASR-MLT-Nano-2512 Q8_0 | 891MB | 73.2 / 66.1 | 75~250× realtime |
| Qwen3-ASR-0.6B Q8_0 | 850MB | 73.4 / 69.4 | 40~285× realtime |
| Cohere Transcribe 03-2026 Q4_K_M | 1.56GB | 74.5 / 72.5 | 50~197× realtime |
| Moonshine base (English only) | 74MB | 73.6 (en) | - |

On the one English song Cohere and Qwen (46.6) are slightly better than whisper
(49.1), but on the three Japanese songs they all fall 6~14p behind whisper.
**The default transcriber (on the quality side) stays whisper-turbo**, and the
light slot stays SenseVoice too (Fun-ASR is worse on singing and 3.7× the size).
That the alternatives are far faster still stands -- once a ground-truth sample
of a conversational broadcast exists, this has to be measured again. There is no
ground for taking a conclusion drawn on singing straight over to conversation.

# Measuring against an animation sample (night of 2026-08-29)

## 46. 116 minutes of dialogue, effects and BGM mixed: VAD threshold 0.3 halves the dialogue it misses

For a sample that mixes dialogue, sound effects and BGM the way a broadcast
does, we used an animated film (『거울 속 외딴 성』, Lonely Castle in the Mirror,
116 min, Japanese audio). The subtitles that came with it are **Korean fansubs**
(SAMI, 1,439 cues), so they are not ground truth for the transcription, but two
things can be counted from them (`bench/gold_e2e.py`).

**Dialogue capture rate** -- the fraction of Korean cue timings (±0.5 s) that a
transcription segment overlaps. It falls when VAD misses dialogue.

| Profile · VAD threshold | Capture rate | Missed cues | Transcription segments | Speech (s) | Hallucinations blocked |
|---|---|---|---|---|---|
| talk (12 s) · 0.5 | 96.7% | 48 | 893 | 3,350 | 0 |
| talk (12 s) · **0.3** | **98.3%** | 24 | 647 | 4,048 | 0 |
| broadcast (4 s) · 0.5 | 96.5% | 50 | 1,100 | 3,267 | 0 |
| broadcast (4 s) · **0.3** | **98.1%** | 27 | 1,022 | 3,893 | 0 |

The capture rate is **decided by the threshold**, whatever the profile. 0.3
halves the dialogue missed and hallucinations blocked is still 0 -- section 44's
decision (default 0.3) stands on a dialogue-plus-effects sample as well. The
segment count drops at 0.3 because the short gaps between lines get joined as
speech and the segments grow longer (within the 12 s ceiling).

## 47. End to end (transcription → translation) chrF: Gemma 29~31 vs M2M-100 13

The same transcription (whisper-turbo, 0.3) was carried through both translators
and matched against the human translation with a character n-gram F2 (chrF) over
60-second windows. The absolute values are low because the fansubs paraphrase --
look at the difference.

| Transcription split | Translator | chrF | Lines | Failures |
|---|---|---|---|---|
| talk (12 s) | **Gemma 4 E4B** | **29.3** | 647 | 0 |
| talk (12 s) | M2M-100 | 13.4 | 647 | 24 (broken into `⁇`, source kept) |
| broadcast (4 s) | **Gemma 4 E4B** | **31.3** | 1,022 | 0 |

What the eye saw (windows at 20, 50 and 90 minutes): Gemma produces almost the
same sentence as the human translation (`맘에 드는 거 있으면 빌려줄게 / 정말?`
↔ `마음에 드는 거 있으면 빌려줄게 진짜?`, "I'll lend you anything you like /
Really?"). M2M-100 invents things that are not there (`엘리자베스 박사`, "Doctor
Elizabeth"), leaves the source as it is, and spits out emoticon jamo (`ᅲᅲ ᄏᄏ`).
The judgement of sections 5 and 23, confirmed with numbers at 116-minute scale
-- **the light default (M2M-100) is there so that it runs at all, and if you
want quality you have to pick Gemma in the first-run setup.** Writing this
difference onto the first-run setup screen is the right thing to do.

**The 4-second split is 2p better than 12 seconds at the end of the pipeline.**
A 12-second segment runs three or four lines of dialogue together into one line
with no punctuation (`本当?パパがヨーロッパ行った時なんかに買ってくるな`),
leaving the translator to find the sentence boundaries itself. Live's short
split helps not only the latency but the translation.

**One remaining weakness of Gemma is plain to see.** When the heroine's name
`ココロ` is written `心` in the transcription, the translation becomes `마음`
("heart"). Sometimes it gets it right (`心ちゃんみんな心ちゃんよ` → `코코짱 모두
코코짱이야`), sometimes it gets it wrong (`心 ごめん` → `마음 미안해`, "Sorry,
heart"). This is the ground for the 「glossary」 experiment -- collecting the
proper nouns within a session and handing them to the prompt -- and the first
thing to try next.

# Should a VOD get a refinement pass? (2026-09-03)

Section 44 wrote 「the shorter the cut the worse it gets, and **the refinement
pass brings it back at 12~25 s**」, but that bringing-back was an inference, not
measured at the time. The VOD path (`transcribe_vod.transcribe`) has no
refinement and no latency constraint either, so "attaching it would be a gain"
follows naturally. We measured the two things for real.

The refinement pass runs offline under the same rules as live's
(`bench/refine_pass.py` -- it reads `GROUP_GAP_S`·`GROUP_MAX_S`·`PREROLL_S`·`REFINE_MIN_KEEP`
from `stream`). `bench/gold.py --refine`, `bench/gold_e2e.py --refine [--refine-split]`.

## 48. Refinement really does bring it back (section 43 samples, CER/WER)

whisper-large-v3-turbo Q8_0, VAD threshold 0.3. Samples and scoring as in
section 43.

| Setting | ニャニャ | 晴る | Fly(en) | You＆合図 | Overall |
|---|---|---|---|---|---|
| talk (12 s) alone | 71.1 | 53.1 | 47.2 | 49.3 | **57.8** |
| broadcast (4 s) alone | 77.9 | 66.2 | 50.3 | 59.2 | **66.6** |
| talk (12 s) + refinement | 67.9 | 50.6 | 33.1 | 54.1 | **55.9** |
| broadcast (4 s) + refinement | 69.4 | 51.7 | 42.3 | 51.4 | **56.9** |

- **Section 44's inference stands.** The 8.8p lost by cutting at 4 seconds
  (57.8 → 66.6) is nearly all given back by refinement (66.6 → 56.9). Saying
  that this is why live survives cutting short was true.
- **There is a gain at 12 seconds too** (57.8 → 55.9). Only the direction
  differs by sample -- English improves a lot, 47.2 → 33.1, while `You＆合図`
  gets worse, 49.3 → 54.1. That is larger than the ±1p variation section 43
  quoted, but it is a figure dragged along by one sample, so this table alone
  does not yield the conclusion 「attach it to VODs as well」.

## 49. But porting live's refinement over as it is makes the subtitles worse

The same 116-minute sample as sections 46 and 47 (『거울 속 외딴 성』), all the
way to the end of the pipeline. Translation is local-gemma/general. We scored it
at several window widths because refined lines **clump the subtitle lines
together** -- one utterance group becomes one line, so it feels the boundary
effect of a 60-second window more.

| Setting | Lines | Median line length | Capture rate | chrF(60s) | chrF(120s) | Whole |
|---|---|---|---|---|---|---|
| talk (12 s) | 647 | 4.8s | 98.3% | 29.3 | 30.9 | 39.6 |
| broadcast (4 s) | 1,022 | 4.1s | 98.1% | 31.3 | 31.6 | 39.2 |
| talk + refinement | 362 | 11.7s | 98.3% | 27.7 | 30.5 | 39.6 |
| broadcast + refinement | 360 | 10.1s | 98.1% | 27.5 | 29.9 | 39.8 |
| **talk + refinement + re-split** | **1,420** | **2.8s** | **98.9%** | **32.4** | **32.5** | **40.1** |

- **Send the refined line out as one line per group and the end of the pipeline
  loses** (29.3 → 27.7). The characters get better (section 48) while the
  subtitle gets worse, and the reason is that the line grows from 4.8 s to
  11.7 s. The whole-concatenation chrF being identical at 39.6 is the proof --
  the content is the same, only the timing was smeared. **A VOD subtitle carries
  a time range and the player navigates by it. A 25-second cue is not a
  subtitle, however good its characters are.**
- **Re-split it and it wins on everything.** Ask the runtime with
  `timestamps="segment"`, take back the segment timings and split the group
  again (1,420 lines, median 2.8 s), and it is the highest at all three window
  widths (32.4 / 32.5 / 40.1) and the highest capture rate too (98.9%). Across
  sections 45 to 47 it is the best setting on this sample.
- The price is transcription time. With the re-split included it runs at 17×
  realtime, 30~50% longer than talk without refinement. A VOD has no latency
  constraint, so it is a price worth paying.

**Measured again with the shipping code.** The re-split row above was a
prototype living inside the bench. We moved the same rules into
`transcribe_vod.refine_cues`, made the bench call that, and ran the same sample
again.

| | Lines | Median line length | Capture rate | chrF(60s) | chrF(120s) | Whole |
|---|---|---|---|---|---|---|
| Prototype | 1,420 | 2.8s | 98.9% | 32.4 | 32.5 | 40.1 |
| **Shipping code** | 1,417 | 2.5s | 98.3% | **32.1** | 32.2 | 39.8 |

The difference is that the shipping side clamps the segment timings inside the
group boundary `[first sample, last sample]` -- so that a subtitle does not
intrude on its neighbour even when the runtime emits a timing pointing outside
the group -- and the price for it is the capture rate going 98.9 → 98.3 (the
same value as the baseline). We judged non-overlapping lines to be worth more
than the 0.6p lost and kept the clamping. chrF is still 2.8p above the baseline,
and above the 4-second split (31.3) as well.

## 50. An aside: section 47's 「4 seconds is 2p better」 is in large part the window boundary

We rescored the same stored results with only the window width changed (the two
right-hand columns of the table above).

| | chrF(60s) | chrF(120s) | Whole |
|---|---|---|---|
| talk (12 s) → broadcast (4 s) | +2.0p | +0.7p | **−0.4p** |

Section 47's conclusion (cutting short helps the translation) is that large only
at a 60-second window; widen the window and it shrinks, take the window away and
it flips. **The observation itself -- that a 12-second line run together with no
punctuation gives the translator trouble -- stands** (section 47's example
sentence is unchanged). Only do not quote its size as 2p. And because section
49's re-split beats the 4-second split, the direction 「shorten the VOD split to
4 seconds」 is dropped.

## 51. Conclusions

1. **Refinement is worth it, confirmed** (section 48). The judgement section 44
   deferred now stands.
2. **Porting live's `Refiner` to a VOD as it is, is the wrong port** (section
   49). One line per group is fine in live because that line goes by in a
   moment; a VOD subtitle stays, and is a thing you navigate to.
3. **「refine → re-split on the segment timings」 is now attached as the VOD
   default** (`transcribe_vod.refine_cues`). The runtime already hands the
   values out (`t0_ms`/`t1_ms` in `transcribe_cpp.Result.segments`), and the
   adapter's knob is `tcpp_asr.TranscribeCppASR.transcribe(segments=True)` --
   off by default, switched on for the refinement pass only. There is no reason
   for live's short chunks to take a different decode path as well.
4. **It can be turned off.** 「Polish with refined lines」 on the screen (the
   same box as live), `--no-refine`, and `refine` on `POST /api/transcribe`.
   There are places where a 30~50% longer transcription is not worth it.
5. **Refinement does not run on the light default transcriber.** The model has
   to be able to give segment timings, and asking the runtime's
   `Capabilities.max_timestamp_kind` gives this.

   | Transcriber | arch | max_timestamp_kind | Refinement |
   |---|---|---|---|
   | whisper-large-v3-turbo (`tcpp-best`) | whisper | `segment` | **runs** |
   | SenseVoice Small (`tcpp-lite`, light default) | sensevoice | `none` | skipped |
   | moonshine base (`tcpp-lite-en`) | moonshine | `none` | skipped |

   Asking anyway raises `UnsupportedRequest`, so rather than take that once per
   group we look once at the start and do not attach refinement (the reason goes
   into the log). Refinement without the re-split is the side that lost in
   section 49, so it is not an alternative either. **Per sections 33~34 the
   light pair is the default, so to see this gain on a freshly installed machine
   you have to pick the heavy pair in the first-run setup** -- the same place as
   section 47's advice to pick Gemma when you want quality.
6. There was one sample (a 116-minute animated film) and the ground truth was a
   fansub. Section 52 is one more look, at a recorded broadcast, and it is there
   that this provisional mark is lifted.
7. For a transcriber that cannot give timings, and for a group caught by the
   rollback, the final lines are left as they are. Where it does not get better
   it must at least not get worse.

## 52. Confirmed on a broadcast sample (29-minute ASMR)

This is the spot section 51 left open with 「one sample (an animated film), so
provisional」. We looked again with one real recorded broadcast -- 29 minutes of
白上フブキ's ear-cleaning ASMR (`vuvdEqKK3KY`), Japanese. **There are no
ground-truth subtitles, so CER and chrF cannot be computed.** What can be looked
at is the structure, the retention rate, and the eye. whisper-large-v3-turbo,
talk profile, VAD 0.3, the same path as the server
(`transcribe_vod.transcribe`).

| | Lines | Median length | Max | Over 7 s | Median characters per line | Total characters | Transcription |
|---|---|---|---|---|---|---|---|
| Refinement off | 175 | 3.4s | 14.2s | **44** | 12 | 3,105 | 33× realtime |
| **Refinement on** | **315** | **2.0s** | 11.9s | **8** | 8 | **3,255** | 21× realtime |

- **The lines become readable.** Lines over 7 seconds go 44 → 8. One 12-second
  final line (`まままま、耳掃除をサボりましたねまぁ、ダメですよ、耳掃除サボっちゃよいしょ、よいしょ`)
  splits into three lines with punctuation on them. The opposite direction of
  section 49's 「one line per group is not a subtitle」 -- it means that a long
  final line, too, is heavy as a subtitle.
- **No characters are lost** (3,105 → 3,255). Matched by nearby timing (±4 s),
  the retention rate is 82~85% both ways, and the places where it came out low
  were cases of **the final line producing garbage and the refinement writing it
  down properly**: `犬` → `じゃあ耳かきセリフ久しぶりにいきましょうか`, `www` →
  `うふふふふ`, `ブルーベージ` → `さてさて、ここに溜め込んでいる悪い奴は両国だー`.
  We found no place where refinement swallowed something.
- The subtitle structure has no flaws: 0 order inversions, 0 overlaps, 0 with
  start > end.
- Transcription goes 33 → 21× realtime, so it takes **1.6×** as long. A little
  longer than section 49's 30~50% estimate -- in this sample the speech is
  scattered in short bursts, so there are many groups.

**Two things are left as they are.**

- In whispered stretches whisper's segment timings stick to a 2-second grid
  (`85.36~87.36`, `87.36~89.36`, ...). That is the runtime emitting round values
  when it cannot pin the timing down, and the subtitles do not intrude on their
  neighbours.
- **The speaker tags made five speakers out of a solo broadcast** (S1 274 · S2
  33 · 8 for the rest). Exactly what `speaker_id`'s docstring warned about --
  CAM++ needs enough voice in a segment, and the final segments of this sample
  are a median of 3.4 s (that document's premise, that a VOD cuts at 12 s, does
  not hold for a broadcast whose dialogue is cut short). It is not a problem
  with the way refinement inherits the tags but a limit of the tags themselves,
  and 「Attach speaker tags」 is off by default. Touching the threshold on a
  sample with no ground truth is fitting rather than measuring, so we did not
  touch it.

**Conclusion: section 51's provisional mark is lifted.** On a broadcast sample
too, refinement (with the re-split) makes the subtitles better.
