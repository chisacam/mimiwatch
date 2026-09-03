# Requirements (v0.4)

*[한국어](REQUIREMENTS.ko.md)*

**Working project name**: mimiwatch
**Purpose**: a web tool that targets YouTube and m3u8 streams, lays real-time transcription and translated subtitles over the original video, and so lets the user follow it in their own language
**Relationship**: a separate project that uses hayamimi as its transcription engine. It does not modify the hayamimi repository; it connects only through the library and a network interface.
**Written**: 2026-08-27 (v0.4 reflects what is finished and what was learned while implementing it. The measured evidence is in `measurements/RESULTS.md`.)

---

## 1. Goals and non-goals

### 1-1. Goals

The only goal is to make a talk or a live stream held in a foreign language **something the user can follow in their own language**. Subtitle quality and synchronisation accuracy directly decide that goal, so they come before every other feature.

### 1-2. Non-goals

The following are out of scope for this project.

- Features whose purpose is downloading and archiving video
- Distributing or sharing subtitle files
- Growing into a multi-user service. **The design assumes a single person running it locally.**
- Improving hayamimi's own recognition accuracy. That belongs in the hayamimi repository.

---

## 2. User flows

The flow splits completely according to the nature of the input. That branch is the skeleton of the design.

### 2-1. VOD flow (automatic synchronisation)

1. The user pastes a YouTube address.
2. The server extracts audio only and **transcribes the whole thing faster than realtime**. hayamimi's offline benchmark puts the RTF around 0.05, so an hour-long video finishes within a few minutes.
3. When transcription ends, a subtitle list is produced with a **media-relative timestamp** on every utterance.
4. When the user starts playback, the YouTube iframe's `getCurrentTime()` is matched against the subtitle timestamps, so it **synchronises automatically**.
5. The two-pass refinement pass and speaker separation can both be applied in full, because there is no realtime constraint.

**This flow needs no manual offset.**

### 2-2. Live flow (manual offset)

1. The user enters the address of a stream in progress, or an m3u8 address.
2. The server takes audio from the stream and transcribes and translates it in realtime.
3. The browser plays the video in the YouTube embed player.
4. The server's processing delay and the browser's playback position are independent of each other, so **the user corrects the subtitle display timing with a slider**.
5. The correction is kept for the session, and can be adjusted again when playback is paused or buffering happens.

---

## 3. System layout

```
[input]               [server]                        [browser]
YouTube URL   ──> URL resolution (yt-dlp)
m3u8 URL      ──> audio extraction (ffmpeg)
                        │
                        ├─ 16kHz mono PCM ──> hayamimi ingest
                        │                        │
                        │                    transcription (RoutedASR)
                        │                        │
                        │                    translation layer (new)
                        │                        │
                        └─────────────────> event stream (SSE)
                                                 │
                                                 v
                                      subtitle layer over the YouTube iframe
                                      + overlay settings panel
```

---

## 4. Functional requirements

### 4-1. Input

| ID | Requirement | Priority |
|---|---|---|
| **R1.1** | Given a YouTube address, it must decide whether it is a video and whether it is live, and branch into the matching flow. | required |
| **R1.2** | An m3u8 address must be enterable directly. It is the fallback path for when the YouTube route is blocked or broken, so it is treated as a first-class input. | required |
| **R1.3** | Local video file input is supported. | optional |
| **R1.4** | When input resolution fails, the cause must be told to the user. When yt-dlp is what failed in particular, point them at entering m3u8 directly. | required |
| **R1.5** | A Twitch channel address must be accepted as live. Audio goes through the yt-dlp path, the picture through the official embed player. | recommended |
| **R1.6** | **Multiview**: show several live streams (up to four) split across one screen, but emit sound and subtitles for the focused one only. A stream that does not have focus only has its sound kept (the last 30 seconds), and when focus arrives transcription starts from there -- the transcription model runs one session at a time. | recommended |

### 4-2. Transcription

| ID | Requirement | Priority |
|---|---|---|
| **R2.1** | The default engine is hayamimi; it must run locally with no outbound transmission. | required |
| **R2.2** | **Every utterance must get a media-relative timestamp.** hayamimi's current implementation does not have this, so the ingest layer adds it. | required |
| **R2.3** | The user must be able to pin the source language. We actually hit the problem of language detection getting stuck on a single-language stream in the 2026-08-27 seminar capture, and hayamimi's `--mode single --lang` option solves it. | required |
| **R2.4** | Automatic language detection must also be selectable. | required |
| **R2.5** | The transcription engine must be replaceable with an external HTTP endpoint. | optional |
| **R2.6** | In the VOD flow, the two-pass refinement pass and speaker separation are applied. | recommended |

### 4-3. Translation

| ID | Requirement | Priority |
|---|---|---|
| **R3.1** | **It must translate from an arbitrary source language into a user-specified language.** hayamimi's current translation path only works on Japanese source text (`realtime_transcribe.py`'s `if self.translators and lang == "ja"`), so this layer is designed anew. | required |
| **R3.2** | The default translator uses local M2M-100. It supports about 100 languages, but only some combinations have measured quality. | required |
| **R3.3** | The translator must be replaceable with an **OpenAI-compatible HTTP endpoint**. LM Studio, Ollama, the Claude API and an in-house gateway are all absorbed by one adapter. | required |
| **R3.4** | An endpoint configuration consists of an address, a model name, an API key and a prompt template. | required |
| **R3.5** | When an external endpoint fails, it must fall back to the local translator automatically and tell the user. | recommended |
| **R3.6** | The target language must be changeable during playback. | recommended |
| **R3.7** | **The viewer's language is set in advance as a global setting.** This is so the user is not made to choose whether to translate every single time. | required |
| **R3.8** | **When the source language and the viewer's language match, no translation is performed.** This is so as not to create needless delay and cost, and quality loss from mistranslation. | required |
| **R3.9** | Translation options are offered on screen only when the two languages differ. The user must be able to overturn that judgement for an individual video. | required |
| **R3.10** | The source language defaults to automatic detection, but the detection result must be shown to the user and be correctable. This is because the 2026-08-27 measurement confirmed the problem of language detection getting stuck. | required |
| **R3.11** | **The translation prompt must be selectable to match the nature of the speech.** In the 2026-08-28 measurement (sections 19~26), what decided translation quality was not model capacity but the nature of the speech. In a technical talk the transcriber mangles product names; in a game stream the speaker does not finish sentences. One set of prompts cannot do both well. | required |
| **R3.12** | **A few of the preceding subtitle lines are passed to the translator as reference.** A single subtitle line often does not have a settled meaning on its own (`って。`, `束縛強め。`). But the boundary between the reference lines and the text to translate has to be made clear in the prompt — left loose, the model translates the reference lines. | required |

### 4-4. Subtitle display

| ID | Requirement | Priority |
|---|---|---|
| **R4.1** | The subtitle layer is overlaid on top of the YouTube iframe. | required |
| **R4.2** | **The display mode must be switchable during playback.** The three modes are source only, translation only, and both. | required |
| **R4.3** | When both are shown, the translation is placed as the lead and the source text as support. When a translation looks wrong, the source has to be available for comparison. | required |
| **R4.4** | Utterances in progress (partial) and settled utterances (final) are distinguished visually. hayamimi already emits the two events separately. | recommended |
| **R4.5** | The live flow provides an offset slider that adjusts the subtitle display timing. | required |
| **R4.6** | Font size, colour, background opacity, display position and maximum line count must be adjustable. | required |
| **R4.7** | Settings must be saved in the browser and survive into the next run. | recommended |
| **R4.8** | **The final subtitle must be displayed first and replaced when the refined line arrives.** Refinement delay reaches up to 20 seconds, so waiting for the refined line alone leaves the screen empty for a long time. | required |
| **R4.9** | **The user must be able to check the recognition state of the source audio.** Recognition quality differed greatly from stream to stream even at the same settings, so it has to be possible to tell whether the cause of a problem is the settings or the source. | recommended |

### 4-5. Records

| ID | Requirement | Priority |
|---|---|---|
| **R5.1** | When a session ends, it must be possible to save the transcription and the translation to a file. | recommended |
| **R5.2** | The save formats supported are Markdown with timestamps, and SRT. | recommended |
| **R5.3** | Leave open a path for handing a saved transcription to an external tool to produce a summary or a write-up. | optional |

---

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| **N1** | **A single person running it locally is the baseline.** Authentication and multi-user handling are not implemented. |
| **N2** | In the live flow, the delay from the end of an utterance to the subtitle being displayed targets 3 seconds or less, translation included. |
| **N3** | It must run on CPU alone. This follows hayamimi's design premise as it is. |
| **N4** | **Assume the yt-dlp path can break at any time.** Even when it fails, the whole tool must not stop; it must be possible to carry on by entering an m3u8 directly. |
| **N5** | What is sent to an external endpoint is the recognised text only; audio is not sent. The user must be able to check on screen whether anything is being sent. |

---

## 6. Plan for reusing existing assets

| hayamimi asset | How it is used |
|---|---|
| `ws_ingest.py`'s `IngestServer` (audio reception) | **Used as it is.** We succeeded in connecting ffmpeg's output without modification. |
| `ws_ingest.py`'s `_forward_events` (subtitles sent back) | **Not used.** It loses events. Subtitles are received over SSE. |
| `subtitle_server.py`'s SSE `/events` | **The default path for receiving subtitles.** In measurement, SSE delivered 92 items while the WebSocket return path delivered only 15. The screen is written anew, but it receives over this path. |
| `RoutedASR` | Loaded and used as a library. The settings corresponding to `--mode single --lang` are exposed. |
| `translate_m2m.py` | Wrapped as one of the translator implementations. The Japanese-pinned call path is not used; the new layer calls it directly. |
| `OVERLAY_HTML` | Reference only. It is a standalone page meant for an OBS browser source, so the component that lays over the video is written anew. |

**New dependencies**: `yt-dlp`, `ffmpeg`. Neither tool is currently installed on this system.

---

## 7. Open issues

Matters not yet decided, or needing confirmation.

**7-1. Media timestamps for live streams — resolved**
YouTube live HLS provides `EXT-X-PROGRAM-DATE-TIME` on every 2-second segment. There is a basis for automatic alignment in live too, so the manual offset is demoted to a fallback for streams that lack that tag. The YouTube iframe player does not expose the value directly, though, so how to connect it to the player's playback position is confirmed in the demo.

**7-2. Unit of translation — settled on the refined line**
Quality actually recovers at the refinement stage. We confirmed a case where a final subtitle's `ちいかはね` was restored to `ちいかわが今はやってます` in the refined line.

**But a new problem follows from it.** The two-pass refinement pass waits for 2 seconds or more of silence before it runs, and silence never comes for a speaker who talks without a break. In both streams refinement was pushed back by up to 20 seconds, and the share of lines over 5 seconds was 40 percent and 50 percent respectively. It reproduced with two different speakers, so it is structural.

**Response**: translate and display the final subtitle first, and quietly replace it when the refined line arrives. It requires no modification to hayamimi, it keeps something on screen at all times, and it fits the display style that leaves the previous sentence in small type.

**7-3. Absorbing the delay of an external endpoint**
Local M2M-100's translation delay was confirmed to sit around a median of 0.2 seconds, so it is not the bottleneck. Therefore **wiring up an external endpoint is an option for quality, not for speed**. In measurement, cases came up of `社長` (company president) mistranslated as a national president, and `ホロメン` as a hormone. How to handle the response latency of a remote API still needs a decision.

**7-4. The project name**
`mimiwatch` is a name attached provisionally. We need a name that shows the relationship with hayamimi while also stating the purpose.

**7-5. The technology stack**
Keeping the server in Python is favourable for reusing hayamimi. Whether the front end goes static HTML or uses a framework is not decided. For state management no bigger than overlay settings and switching the display mode, static HTML is enough.

---

## 8. Implementation status (as of 2026-08-28)

### 8-1. Done

| Requirement | Status | Notes |
|---|---|---|
| R1.1 YouTube address input and flow branching | ✅ | the server decides whether it is live and splits automatically |
| R1.2 direct m3u8 input | ✅ | on-screen playback through hls.js too (2026-08-30) |
| R1.5 Twitch live | ✅ | 2026-08-30. Official Embed JS, `parent=localhost\|127.0.0.1` |
| R1.6 multiview | ✅ | 2026-08-30. Reader thread + 30-second ring + focus episodes (`live.py`), tiles and adapters (`web/app/tiles.js`, `adapters.js`) |
| R1.4 telling the user why it failed | ✅ | |
| R2.1 hayamimi local transcription | ✅ | VOD at 38~88x |
| R2.2 assigning media timestamps | ✅ | uses the sample position of the VAD segment as it is |
| R2.3 pinning the source language | ✅ | equivalent to `--mode single` |
| R2.5 external transcription engine | ✅ | both VOD and live. Two branches: OpenAI-compatible HTTP, and transcribe.cpp local GGUF |
| R3.1 translation from an arbitrary source language | ✅ | `translate.py` takes the source language as a parameter |
| R3.3 OpenAI-compatible endpoint | ✅ | verified on Backend.AI GO with Gemma 4 E4B |
| R3.5 local fallback on failure | ✅ | the breaker trips after 3 consecutive failures |
| R3.7~R3.10 language match judgement | ✅ | when the languages are the same, both translation and subtitles are skipped |
| R3.11 per-genre translation prompts | ✅ | five of them: general, technical, game, chat, song. Both live and VOD |
| R3.12 context from preceding subtitles | ✅ | 3 lines. Tightening the boundary took contamination from 4/63 cases to 0 |
| video management UI | ✅ | list, add and delete in the left panel. The top holds only title, processing status and manage |
| engine choice at registration time | ✅ | the add dialog and "manage" point at the same value |
| swapping the transcription engine live | ✅ | the adapter's insides are changed, keeping the session and the subtitles |
| resuming a broken session | ✅ | same session id. Lossless inside the DVR window; outside it, the seconds that could not be filled are written into the subtitles |
| explicit server shutdown | ✅ | "Server · Shut down" in the ⚙ panel and `Ctrl-C` take the same path. Live sessions are closed and it stops |
| R4.1~R4.9 subtitle display | ✅ | off, both, translation only, source only; offset; collapsing the script |

### 8-2. Facts learned while implementing

**The live media position cannot be computed from the sequence number.** `EXT-X-MEDIA-SEQUENCE × segment length` is right on some streams and yields 0 on others. That is because YouTube serves low-latency streams the whole DVR playlist starting from 0. We changed it to subtracting `release_timestamp` from `PROGRAM-DATE-TIME`.

**ffmpeg reads from the start of the playlist by default.** On a stream that serves the whole DVR, that means transcribing content from 42 minutes ago. `-live_start_index -2` starts at the live edge.

**Live subtitles must not be looked up by media time.** Because a refined line absorbs several final lines and takes the group's start time, a subtitle that has just arrived is treated as an item from 30 seconds ago. They are displayed in arrival order.

**The VAD forced-split interval decides the recognition rate with multiple speakers.** At the 12-second default, 44% of a four-person collab hit the cap and stopped at 5.0 lines per minute (a VOD from the same channel gives 8.2). It recovered to 7.9 lines at 6 seconds and 13.8 at 4 seconds. It is selected through a content-type profile.

**Speaker tags are only worth anything when the conditions are right.** In a collab where utterances overlap, CAM++ groups everything as `S1`. The tags are hidden until two or more distinct speakers have been confirmed.

**Transcription routing confirmed**: Japanese goes to ReazonSpeech (`rz`). whisper-tiny is for language detection only, and is not called when the language is pinned.

### 8-3. Transcription engine (finished 2026-08-28)

We attached transcribe.cpp's GGUF models through Python bindings. Calling the
CLI once per segment means reloading the model every time, which cannot be used
for live, so we took the approach of loading the model once and reusing it.

`tcpp_asr.TranscribeCppASR` imitates the surface of hayamimi's `RoutedASR`, so
`run_stream` and `Refiner` can be used as they are. We made use of the fact that
pinning the language closes the refinement pass's language re-detection branch
by itself.

- **Japanese and Korean**: whisper-large-v3-turbo Q8_0
- **Everything else**: automatic fallback to hayamimi RoutedASR
- The reasoning and the measurements for the candidates that dropped out
  (Fun-ASR, SenseVoice, Voxtral, Moonshine, Qwen3, Cohere) are in
  `measurements/RESULTS.md` chapters 11~14

### 8-4. Persisting work state — done (2026-08-28)

**The problem**: transcription job records lived in a module dictionary in `jobs.py`, and live sessions and their subtitles only in `live.py`'s `_sessions`/`_finished` and in browser memory. One server restart and a job in progress answered polling with a 404, and the subtitles taken down from a two-hour stream vanished wholesale. Refreshing the tab did the same — because live had no cue file.

**What we did**: a single `data/mimiwatch.db` holds jobs, sessions and live subtitles (`store.py`). It uses only the standard-library `sqlite3`, and builds the schema at startup with `CREATE TABLE IF NOT EXISTS`, so there is no migration step for the user to run. Transcription results (`data/<id>.json`) and engine settings (`backends.json`) already survive as files, so they are left as they are.

- **One connection, wrapped in a lock.** The server is a `ThreadingHTTPServer` and live spins up a translation thread for every subtitle line, so a per-thread connection amounts to opening a connection for every one-second thread. No path holds a transaction for long (even the SSE handler reads once at connection time and is done), so lock waiting is not a problem. WAL and `synchronous=NORMAL` are turned on.
- **Job and session records are one JSON column**, **subtitles are a real table**. The first two become the HTTP response body as they are and are only ever looked up by id, so there is no reason to spread them into columns; but subtitles have to be updatable row by row, because a refined line absorbs and erases final lines and the translation is attached later.
- **What was cut off is written down as cut off.** The ffmpeg child process died along with the process, so reception cannot carry on. At startup, jobs and sessions left as `running` are turned into `interrupted`, and the subtitles already accumulated are left readable. Resuming reception is not implemented.
- **Accumulated subtitles are sent back right after the SSE connects.** Without inventing a new event type, the stored cues and translations are resent in exactly the shape the browser was already receiving. So a tab refresh comes straight back to the stream you were watching, and a finished session closes the stream once it has sent the whole backlog. Past streams can be reopened from the list (`GET /api/live/sessions`).

**Two things this exposed**: the first status frame of the SSE had no `type`, so the browser had been ignoring it all along (it is the only frame that can say why something was cut off, so it was fixed). And `.live-badge`'s `display:flex` beat `[hidden]`, so the LIVE badge was always up.

### 8-5. Remaining work

- Real-use verification of the alignment accuracy between live subtitles and the video (a manual offset is provided for now)
- Separating overlapping utterances (speaker tags do not solve it)
- Resuming reception on a broken live session. For now it is marked as interrupted and only lets you read the accumulated subtitles (see 8-4)
- A session stopped by hand disappears from the list until the next refresh. The record is still there, so it is a display-only problem


---

## 9. Proposed next steps

1. **Measure the most uncertain thing first.** Confirm first whether the path of taking YouTube audio with yt-dlp and feeding it to hayamimi actually works, and how much delay there is on a live stream. If that does not work, the rest of the design is meaningless.
2. Finish the VOD flow first. There is no synchronisation problem there, so we can concentrate on the translation layer and the UI.
3. Add the live flow and offset correction.
4. Add the external endpoint adapter.

---

*This document is a draft and is updated as discussion goes on.*
