# Usage guide

*[한국어](GUIDE.ko.md)*

The detailed guide, moved out of the README. Installing and running are in
[README](../README.md), and the rules for people and agents who change the code
are in [AGENTS.md](../AGENTS.md).

## How to use

The screen has three columns. **The video list is on the left**, the player is
in the middle, **the subtitle log is on the right**. Both sides collapse — the
list with `V`, the subtitle log with `S`.

**Put a YouTube URL, a Twitch channel URL or an m3u8 URL into "＋ Add" on the
left.** The server decides whether it is live or a VOD, so you do not have to
choose. A bare m3u8 gives no way to know, so it is treated as live. Hovering a
row in the list shows 🗑, which deletes **that row's video**.

The list shows the thumbnails YouTube hands out. The player is already
embedded, so the browser talks to Google anyway — this is not a new party to
talk to. A session where you pasted an m3u8 directly has no video id, so the
slot is simply empty.

**The transcription and translation engines are chosen as you add it.** It
starts with that engine the moment you add it, so pick the light side if the
machine is modest. "Manage" at the top changes the same values — the two places
point at one value.

Once it has started, **"Stop"** in the progress indicator at the top stops it.
It stops within a few seconds even mid-transcription.

The top bar holds **only what you are watching now and what is running now** —
the title, the progress state, and "Manage" (my language, transcription,
translation, engine management).

**Specify the source language.** Left empty, the model decides for itself, and
when that decision wobbles a whole sentence comes out in a different language.
If you know the language of the stream, specifying it is far more stable.

**The target language is remembered on the machine.** "My language" in Manage
decides which language the subtitles come out in. The last choice is stored on
the server (in the settings file), so the web page and the browser extension
share one value: the default you set in one place follows you to the other, and
it survives a fresh browser profile.

### Genre

**It fits the translation prompt to the character of the speech.** A speaker at
a tech talk and a speaker on a game stream use different words, so one set of
prompts is unlikely to do both well. It applies to live and VOD alike.

| Genre | What it does |
|---|---|
| General | When you do not know the genre, or it is mixed |
| Tech talk · seminar | Restores product and service names the transcriber mangled, to fit the context |
| Game stream | Does not finish sentences that were left unfinished. Leaves interjections as interjections |
| Chat · variety | Transliterates slang and abbreviations instead of inventing a meaning |
| Song · lyrics | Keeps the imagery and the word order, and does not insert a subject that is not there |

The effect was measured. With the same model over the same 63 lines, 47 lines
changed, and `RAT fifty three` in the talk's subtitles came back as `Route 53`
([measurement section 25][m]).

**The three preceding subtitle lines go to the translator along with the
line.** One subtitle line on its own often does not pin down the meaning —
`束縛強め。` without context is "restraint is strong", and with context it is
"so possessive".

The genre you chose stays with the transcription result. It is not asked again
when you re-translate with a different engine, and opening a video shows that
video's genre in the picker again.

[m]: ../measurements/RESULTS.md

### Live options

**Content type** — decides how often speech is cut. When several people talk
over each other, cutting short reduces what is dropped. **It is a different
axis from genre** — this one is the cutting interval, genre is the vocabulary
it is carried into.

| Type | Max segment | Where to use |
|---|---|---|
| Talk · lecture | 12 s | One person pausing between sentences |
| Panel · interview | 6 s | Taking turns, with pauses |
| General stream | 4 s | One or two people, short pauses |
| Collab · group conversation | 3 s | Speech overlaps |

**Polish with refined lines** — switched on, it waits for one utterance group
to finish, joins it and transcribes it again. The context is longer so it is
more accurate, but the subtitle settles late and a line that is already up
changes. On a stream where short remarks come and go fast, switching it off and
sending each sentence out immediately is easier to follow. The default is on.

### Screen controls

| | |
|---|---|
| `S` key | Collapse/expand the subtitle log panel |
| `V` key | Collapse/expand the video list |
| `F` key / "⛶ Fullscreen" | Player to fullscreen |
| Subtitle mode | Source only / translation only / both |
| Subtitle position | **Drag the subtitle to move it.** "⤾ Reset position" puts it back at bottom centre |
| Offset | Fine adjustment of live subtitles (unnecessary for VODs, which align automatically) |
| Status line | Shows the name of the transcription engine actually running now |
| "⊞ Add tile" | Attaches another live stream next to the one you are watching (multiview, below) |
| `1`~`4` keys / clicking a tile | Moves the focus of sound and subtitles in multiview |

**The transcription engine can be changed while it is running.** Pick one in
"Manage" and it is swapped in place — the subtitles already transcribed stay,
and only what comes after is handled by the new engine. Same rule as the
translation engine.

It used to require restarting the session, and that changed the session id;
since subtitles are stored by session id, the subtitle log up to then
disappeared. Within one video the subtitles have to be continuous.

If the new engine does not support that stream's language (a Japanese stream on
an English-only model, say), it is not changed and it says so. The engine you
were using stays.

**Fullscreen uses ours, not YouTube's.** YouTube's fullscreen button enlarges
the iframe, and the browser paints only the subtree of the fullscreen element,
so the subtitles outside it vanish entirely. So that button is removed (`fs=0`)
and a button that enlarges the box holding video and subtitles together is put
there instead. The subtitle size grows along with the screen.

In fullscreen too you can **change the subtitle mode, font size and background,
and drag the subtitle around.** It shares the same values as what you chose in
the window, so changing either side moves both. Only the subtitle log panel is
outside the box and therefore not visible.

**The subtitle position is recorded as ratios** — the horizontal ratio of the
centre measured from the left of the box, and the vertical ratio of the bottom
edge measured from the floor. Recorded in pixels, the spot you chose in the
window becomes a corner in fullscreen. Enlarging the font leaves the bottom
edge where it was.

The old "Position" slider is gone. Dragging and the slider used the same value
but with different ranges — the slider was fixed at 0–40% while the limit for
dragging changes every time with the height of the subtitle block at that
moment, and horizontally the slider had no effect. There is one place that
writes the value now, and only the button that resets it is left.

The controls stand **vertically at the right edge** and disappear after 2.5
seconds. The subtitles sit at the bottom centre of the screen, so the two do
not overlap. The background is kept faint as well, so the video shows through
while they float there.

**To call them back, move the cursor to the right edge at mid height.** Mouse
movement over the video does not reach us — inside the iframe is a different
origin, so the events do not cross. So a strip that receives them is laid over
just that spot. The subtitles and YouTube's control bar are both given a wide
berth.

**The cursor is not hidden.** While the cursor is over the iframe its shape is
decided by YouTube's document, so it cannot be touched from outside. Bringing
the cursor onto a transparent layer was tried and did not work — the cursor
shape is re-evaluated when the pointer moves, and covering it with a layer
after the pointer has stopped is not reflected until the next movement, and
that movement immediately takes the layer away.

### Multiview

When you want to watch several viewpoints together, as with a collab, **"⊞ Add
tile"** attaches another live stream next to the one you are watching —
YouTube, Twitch or m3u8, any of them, up to four. **Dragging a row from the
list onto the player area** attaches it too — a stream that is being received
comes in as it is, a stopped one is resumed as the same session and comes in as
a waiting tile. The layout is chosen from vertical, horizontal, 1+2, 1+3 and
2×2 (with two, the default is vertical — a stream that is wide horizontally
looks bigger that way), and in 1+2/1+3 the focused tile takes **the large slot
on top** while the rest take one row below. Dragging a tile's strip or cover
onto another tile swaps their places.

**Sound and subtitles come from the focused tile only.** Clicking a tile that
is not focused, or pressing the `1`~`4` keys, moves the focus, and the sound,
the on-screen subtitles, the subtitle log on the right and the status line at
the top all follow. The focused tile takes player controls (play, pause,
volume) as usual; the rest are under a transparent cover, and clicking makes
them the focus.

The server **transcribes the focused stream only.** The rest keep receiving
sound and hold the last 30 seconds, and when the focus arrives transcription
starts from those 30 seconds — which is why, within a few seconds of moving the
focus, the conversation just before appears as subtitles. There are no
subtitles for the time it was not focused (it was not being transcribed). The
transcription model is shared as one copy, so even with four streams the only
thing that gets heavier is four ffmpegs and four 30-second rings.

A tile's ✕ stops receiving that stream's subtitles and closes the tile. In the
list, members of a bundle carry a ⊞ mark, and clicking any member opens the
whole bundle. A refresh brings the bundle and the focus back, because they live
on the server, but **restarting the server forgets the bundle** and leaves only
the member sessions as "interrupted" — re-bundling is the user's job.

Twitch uses the official embed player. That player only opens if the parent
page is `localhost` or `127.0.0.1`, so always open the screen as one of those
two (the server only opens that way). A bare m3u8 is played with hls.js — if
the stream server blocks cross-origin access (CORS) the picture does not open,
but the subtitles keep piling up, because the server receives it with ffmpeg.

### Past streams

Live sessions and subtitles stay in the database. **Refreshing the tab brings
you back to the stream you were watching**, and restarting the server does not
lose the subtitles that were transcribed. You can pick a past session from the
list and read it again.

Opening a stopped session shows **"Resume"** at the top — pressing it appends
**to the same session**, so the subtitles up to then stay and new ones keep
piling up after them. That holds for a session left "interrupted" because the
server died, for one whose reception was cut off, and **for one the user
stopped themselves** — a live stream often pauses briefly and comes back, and
starting a new session then splits the subtitles into two sets. The **▶** that
appears when you hover a row in the list does the same thing. The extension
popup has the same button.

Only a session whose stream has already ended has nothing to resume. **"⟳
Transcribe the whole video"** appears instead then — it re-transcribes that
video, which YouTube kept as a VOD, from end to end (the live subtitles are
left alone and a separate VOD entry is created).

**Resume starts at the current live edge.** It does not rewind to fill in up to
where it stopped — that would mean the later you resume the longer the wait
before the subtitles for the place you are watching arrive, because the gap is
transcribed first, and with a slow transcription engine it is later still.
Instead it writes one line in the subtitle log saying how long the gap was.

    ⋯ about 42 s went unreceived while the server was down ⋯

The principle of not leaving a silent hole is unchanged. Only the case where
reception is briefly cut within a session and reattaches by itself is rewound —
the cut is a few seconds then, so it picks up right away inside the DVR window.

**It does not resume automatically.** Resuming reception after the server
comes back up requires explicit user action (clicking the button).

### Searching the library

The video list has a search box. It searches **the subtitle text, not the
titles** — both the source lines and their translations — across the whole
library. You type words and the list becomes the matching lines, each with the
video name and its moment; pressing a line opens the video and moves there.

It searches by word: every word you type must stand in a line (a line that
holds all of them matches), and the marked stretch shows the place it stood.
That is how you find the line half remembered — "the one where they said
mimi".

### Re-transcribing

Hovering a VOD row in the list shows **⟳**. Pressing it opens the "Add video"
dialog with that URL, and choosing the transcription engine, language and genre
there and pressing "Start" transcribes it again. **The same engine works
too** — before, the only way to re-transcribe was to paste the same URL again,
which made it look as if you had to change the engine. Lines whose text is
unchanged inherit the translation already made and the hand edits.

### A stream that has ended is watched like a VOD

YouTube keeps a live stream as a VOD once it ends. Open that session again and
the subtitles **follow the playback position** — press a line in the subtitle
log to go to that spot and the subtitles appear from there, and the subtitle log
on the right centres the current line.

While it is being received it does not do that. Subtitles arrive a few seconds
behind the speech, so matching by time shows nothing. It holds the most
recently recognised line until the next one arrives instead. **Even in the same
session the rule differs depending on whether it is being received or has
ended.**

Live subtitle times are relative to the start of the stream, and YouTube's VOD
uses the same reference, so they match as they are.

### Editing subtitles

Transcription gets things wrong. It hears noise as speech, writes proper nouns
wildly, and the translation goes wrong once more on top of that.

Under the subtitle log header there is a **mode**. It decides what happens when
you press a line.

| Mode | Pressing a line |
| --- | --- |
| **Read** (default) | Moves the video to that point — as before |
| **✎ Edit** | Edits the source, translation and time in place |
| **⟳ Translate** | Selects lines to translate again |

In read mode the edit button is not shown at all. The subtitle log's real job
is reading, and an editor must not open because of an accidental tap while reading.
Returning the mode to read closes an editor that was open.

The mode is not saved. It always starts as read.

| Field | |
| --- | --- |
| Source | The words as transcribed |
| Translation | The result from the translation engine currently chosen |
| Time | The time (in seconds) this line appears |
| 🗑 | Deletes this line |

`⌘/Ctrl+Enter` saves, `Esc` cancels. Plain `Enter` is a line break — a subtitle
line is not always one sentence.

**Editing the source does not translate again automatically.** It marks that
translation as belonging to the old sentence instead.

> I'm starting to get nervous. What do I do.　**⟲ Differs from source**

Fixing the translation by hand as well clears the differing-translation badge.
A translation edited by hand keeps its edit mark (✎), so a bulk re-translation
later does not overwrite that line.

**Moving the time carries the duration with it.** Moving only the start leaves
the end behind and makes a subtitle whose "end precedes the start", and SRT
tools either drop such a line or refuse the whole file.

Editing while a live stream is being received is reflected in the subtitle log
window immediately.

### Re-translating

You can run just the passages where the translation went wrong. Select lines in
**⟳ Translate** mode.

- Clicking selects the line; clicking again deselects it
- Shift-clicking selects the range from the previously selected line to
  here at once (the same as a file explorer)
- "All" / "None"

The strip below shows how many lines, and "⟳ Re-translate" starts it. The
translation engine and genre currently chosen are used as they are — you can
change the engine and run the same passage again to compare.

**Translations edited by hand are skipped.** Those lines carry a ✎, and while
you select it tells you in advance: "N hand-edited lines will be skipped". If a
bulk run overwrites a translation you had fixed, there is no way back.

A line that carries **⟲ Differs from source** because you edited the source has
the mark cleared when it is translated again. The new translation belongs to the
current source, after all.

It works while a live stream is being received too. A re-translated line
reaches the subtitle log window immediately as well.

### Export

**⤓ Export** in the subtitle log header takes the accumulated subtitles as a
file. It works for VODs and live alike.

| Format | Use | Missing-segment notes |
| --- | --- | --- |
| SRT | Video editors · players | Dropped |
| WebVTT | Web players | Dropped |
| Text | Reading with timestamps attached | Kept |
| JSON | Re-importing · re-translating | Kept |

What to include is the same axis as on screen — both / translation only /
source only. A line with no translation stays as the source even under
"translation only". Dropping it would make that utterance never have happened.

**Live subtitles have no end time.** What knows the moment speech ends is the
VAD, and the subtitle is finalised later carrying only a start time. SRT/VTT
demand an end time, so one is invented — until the next subtitle starts, **6
seconds at most**. Lines packed tightly together get at least 0.8 seconds (a
0.2-second subtitle cannot be read and some tools drop it). VODs have their
ranges actually measured, so those are used as they are.

It works by URL too.

```
/api/export?id=live:<session>&fmt=srt&view=both
/api/export?id=<video id>&fmt=txt&view=tr
```

### Burn-in

**Burn into video** in the export dialog writes the subtitles back onto the
picture. The server hands the lines to ffmpeg as an SRT, and ffmpeg encodes a
new file beside the source, which is never touched. A re-burn numbers its file
(`x - mimiwatch (2).mp4`). The audio is copied through as it is; only the
video is re-encoded.

**Only local files.** The premise is a video file on this machine, and the
only kind that has one is a local file. A YouTube or Twitch VOD is transcribed
from an audio rendition, and the video itself is never downloaded, so there is
no file here to burn into.

**The ffmpeg needs the `subtitles` filter.** That filter only exists in a build
with libass, and a stock Homebrew `ffmpeg` does not have it. Before it starts,
it asks — an ffmpeg that cannot burn is said so plainly, not with a bare exit
code.

It encodes the whole video, so it takes a stretch of real time. The dialog
closes and the job box follows the progress; stopping leaves no file behind.

## Swapping engines

Open it with the ⚙ button.

**Transcription** — the default is local Whisper. You can also add an
OpenAI-compatible `/v1/audio/transcriptions` endpoint. VODs are cut into
windows of a few minutes and sent, live sends one chunk of speech (2–12 s) at a
time — in live the subtitle is late by the round trip, but in exchange you can
pay for better recognition than a free local model. Local↔remote can be swapped
while it is running.

**Translation** — the default is local Gemma. Adding an OpenAI-compatible
`/v1/chat/completions` endpoint lets a bigger model run on another machine. If
the remote fails it falls back to local automatically, and which side answered
is recorded.

The settings are in `backends.json`. Editing it directly is fine.

**New default engines come in by themselves.** `backends.json` is created once
on first run and is not overwritten after that — it holds real endpoints and API
keys. That created the problem of default engines added later never reaching
existing users. Now **only the ones never seeded before** are picked and put in.
An engine you deleted does not come back to life (it stays in the `seeded`
list).

There is no need to restart the server. The settings are re-read on every
request — refresh the browser and it appears.

### Running on the CPU

**The default is GPU.** Transcription picks the fastest device available with
`auto`, and translation puts every layer on the GPU.

On a modest machine the CPU may be better. Integrated graphics share system
memory with the CPU and have narrow bandwidth, so on a laptop with cores to
spare the CPU side is faster, or at least does not get in the way of other
work. It also avoids transcription and translation fighting over the same small
iGPU.

Write `device` into each entry in `backends.json`.

```json
{ "id": "tcpp-best", "backend": "tcpp", "device": "cpu" }
{ "id": "local-gemma", "backend": "gemma", "device": "cpu" }
```

| Value | Meaning |
|---|---|
| `auto` (default) | The fastest device available. GPU if there is a GPU |
| `cpu` | CPU even if there is a GPU |
| `vulkan` `metal` `cuda` `rocm` | Pinned exactly (falls back to `auto` if absent) |

**The thread count is decided automatically to match `device`** — 4 for GPU,
half the logical cores (8 at most) for CPU. Writing `threads` yourself takes
precedence.

To try transcription on the CPU for a moment without editing the file, an
environment variable works too.

```sh
TRANSCRIBE_BACKEND=cpu ./run.sh
```

**A sense of the speed** — measuring 20 seconds of audio on this machine (Apple
M5 Pro), Metal is 57× realtime and the CPU with 8 threads is 7.7×. **The
transcription result was identical down to the character.** The CPU is only
slower, it does not trade quality away, and it is still far from the 1× that
live needs. These multiples differ from machine to machine, though, so measure
again on yours.

```sh
.venv/bin/python bench/asr_device.py
```

When something does not work, `bench/doctor.py` prints the prerequisites,
backends, model loading and URL resolution in one go.

```sh
.venv/bin/python bench/doctor.py
.venv/bin/python bench/doctor.py "https://www.youtube.com/live/..."
```

M2M-100 is unrelated to this setting. It uses CTranslate2 pinned to the CPU.

### Models stay resident

Transcription and translation models load the first time they are used and
**stay for as long as the process lives.** Sessions and jobs share one copy, so
running a re-translation while a live stream is being received does not load a
second Gemma, and the first subtitle of the next stream is not late by a model
load either.

If you do other things on this machine, you can have idle models released.

```sh
MIMIWATCH_MODEL_IDLE_S=600 ./run.sh     # released if no session or job uses it for 10 minutes
```

A stream being received turns the clock back with every subtitle line, so a
release never happens in the middle of a two-hour stream. After a release, using
it again loads it again (a few seconds).

### If the default transcriber is too heavy

Since `whisper-large-v3-turbo` (845MB) is heavy, **SenseVoice Small** (241MB)
is downloaded alongside it for heavy-going machines. Put it into
`asr_backends` in `backends.json` and it appears in the "Transcription" picker
on screen.

```json
{ "id": "tcpp-lite", "label": "SenseVoice Small (light · CPU)",
  "backend": "tcpp", "model": "SenseVoiceSmall-Q8_0.gguf", "device": "cpu" }
```

**For an English stream there is something lighter.** `Moonshine base` (74MB)
is one eleventh of the default with English quality that is practically the same.

```json
{ "id": "tcpp-lite-en", "label": "Moonshine base (light · English only)",
  "backend": "tcpp", "model": "moonshine-base-Q8_0.gguf", "device": "cpu" }
```

Values measured on the same 20-second chunk on this machine (M5 Pro).

| Model | Size | GPU | **CPU** |
|---|---|---|---|
| whisper-large-v3-turbo | 845MB | 56.8× realtime | **7.7× realtime** |
| SenseVoice Small | 241MB | 272.8× realtime | **62.4× realtime** |
| Moonshine base (English) | 74MB | 89.7× realtime | **91.8× realtime** |

**Moonshine is faster on the CPU than on the GPU.** The model is small enough
that the transfer cost exceeds the compute cost.

Quality goes like this.

- **Moonshine base** — at the 60-second mark of an English sample, **not one
  character differed from whisper, punctuation included.** In exchange it
  refuses languages other than English outright. It is checked in advance when
  the session is created, so you know the moment you start.
- **SenseVoice Small** — on a Japanese sample it transcribed `工場内` and
  `できた銃で` more accurately than whisper. In exchange it inserts no
  punctuation or spacing, so it comes out as one block, and it shortens Korean
  proper nouns (`데이터독` → `데이터`).

This is where the reason for not changing the default lies. For the details see
[measurement sections 33–34][m].

## Shutdown

Shut it down with **"⏻ Shut down"** at the right end of the top bar ("Server ·
Shut down" inside ⚙ engine management does the same). It differs from killing
the process — it **closes the streams being received properly first**, stops
the server, and then **closes this window and the Script window.** Killing it
outright leaves sessions as "interrupted", and then you cannot tell later
whether the user shut it down themselves or the server died.

Used as a bundle there is no terminal, so this button is the only way to shut
down. If the browser will not let a script close the tab (a tab that came in
from a bookmark and went through several pages), only the shutdown screen is
left, so close it yourself.

If you are running it from a terminal, `Ctrl-C` takes the same path. To start it
again, run `./run.sh` or mimiwatch again.

## Updates

The server checks GitHub releases once a day, and if there is a new version it
raises a notification strip at the top of the screen (also checked by hand in
"Manage › ⚙ Engines › Update").

- **Used as a bundle**, "Download" on the strip fetches the zip for this
  platform, and "Restart and apply" closes the streams being received properly,
  swaps the bundle and comes back up on the new version. The old version stays
  as `.old` in the same place, so a swap that goes wrong is reverted with it.
  Models, settings and subtitles live outside the program, so they stay as they
  are.
- **Running from the repository** it only tells you — get it with `git pull`.
- **A private repository** answers 404 to the check and the readout says so
  ("release not found (404) -- a private repository needs a token"). Put a
  GitHub token in the field under "Update", or set the environment variable
  `MIMIWATCH_GITHUB_TOKEN` (it also covers the automatic check). The token is
  sent to api.github.com and nowhere else; the field's value is kept in this
  browser only.

To switch it off, put `"update_check": false` into `backends.json` or start it
with the environment variable `MIMIWATCH_NO_UPDATE_CHECK=1`. Even with the check
off, the "Check for updates" button under "Update" still works.

## Measurement summary

The evidence is in [`measurements/RESULTS.md`](../measurements/RESULTS.md).

**Transcription was settled on Whisper alone.** Four live streams were recorded
for 180 seconds each and the same audio was fed to several models for
comparison. Fun-ASR transcribed 7% more of a solo stream but ignored the
language pin on a collab and spat out Indonesian and Vietnamese. A model that
endures everything was chosen over one that only handles one kind of stream
well.

**Lowering the quantisation does not make it faster.** On Metal the weight size
is not the bottleneck. Cutting 845MB to 511MB only wobbles between 13× and 16×
realtime, and the quality drops.

**For translation Gemma is clearly better.** On the same subtitles M2M-100 made
a frog a turtle, `潜る` a submarine, and `おやすみなさい` "hello hello". Gemma
got them all right, at 0.15 seconds per line.

**Live latency averages 0.4 seconds.** That is measured over 25 transcribed
lines on a collab stream.

**GPU acceleration is decided by the platform.** macOS is Metal, Windows is
Vulkan. AMD, NVIDIA and Intel all go down the one Vulkan path on Windows —
there is nothing to install besides the driver. The reason `whisper.cpp-amd` is
not used on AMD is written down in [docs/WINDOWS.md](WINDOWS.md).

**It was not switched to a translation-only model.** Google's TranslateGemma 4B
is half the size, slightly faster, and on whole sentences it is equal, but on
the fragments that make up half of live speech it invents words — it finishes
`なんも反応がないな。ゲームあっ。` as "the game is over". **Fitting the prompt
to the genre** was far cheaper and had a bigger effect instead.

## yt-dlp lives in the virtualenv

It is kept inside `.venv` rather than installed system-wide. YouTube changes
its extraction path often and yt-dlp follows each time, but a copy installed
with `winget` or `brew` does not update itself. After a few months it **fails
to get the format list at all** and live becomes "no audio found" — it is not
that there are no formats, it is that nothing could be read.

**Running the install script again updates it.**

```sh
./install.sh          # Windows: .\install.ps1
```

Steps already done are skipped and only yt-dlp is brought up to date. Even if
an old installation is using the system copy, running it once more brings it
inside the virtualenv. `bench/doctor.py` tells you which one is in use now.

**If you are using a bundle**, the yt-dlp inside has its version baked in and
this path does not exist. Download the **yt-dlp standalone executable** from
"Models & Tools" instead — if it is in the tools directory it is used first, and
that file updates itself with `-U`. For the details see
[docs/PACKAGING.md](PACKAGING.md).

**Two months of age is enough to break live.** In the 2026-08 measurement a
version 7 weeks old already failed to get a single live audio format. The server
warns that a version more than 45 days old is stale, and then you should
download the tool again or bring it up with `yt-dlp --update-to nightly`
(nightly is the channel yt-dlp recommends to ordinary users).

### YouTube needs a JS runtime (deno)

Since November 2025 yt-dlp uses an **external JavaScript runtime** to solve
YouTube's signature challenge. Public live (HLS) does without it, but **VODs and
membership streams fetched with cookies lose their formats without deno.**
Download deno in "Engines › Models & Tools" (`brew install deno` works too) — if
it is on the system, that one is used. The server tells yt-dlp the path of the
deno it found directly (`--js-runtimes`), so it works even in an environment
with a short PATH, such as a bundle launched from Finder. The solver script
(`yt-dlp-ejs`) ships inside the bundle and the virtualenv, and if the versions
do not match it is fetched from GitHub. `bench/doctor.py` (`mimiwatch --doctor`
for the bundle) shows which one is in use.
[yt-dlp EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS)

## Members-only streams

Two separate things get in the way — **getting the audio** and **putting the
picture on screen**.

Audio has two routes. Either the server holds cookies and fetches it directly,
or the browser hands over the sound of a tab you are already listening to.
**The latter is recommended** — it needs no cookies and no update every time
YouTube's extraction path changes.

### Audio (recommended): taking the sound of another tab

Change **Audio source** in "＋ Add" to "Sound from another tab in this browser"
and Chrome's screen-sharing window appears. Pick the tab where YouTube is open
and **switch on "Also share tab audio".** The sound coming from that tab is
transcribed as it is.

It is sound the user picked themselves in the sharing window, sound they are
already listening to. Nothing is being circumvented, so no cookies are needed
either.

**Write the name yourself.** Chrome does not tell us the title of the shared
tab — it gives the capture track's `label` an opaque identifier like
`web-contents-media-stream://8D6F…`. That one line is the only thing that lets
you recognise what you listened to in the list, so two "Tab audio" rows side by
side cannot be told apart.

Even if you started with it empty you can fix it later. Press **"✎ Name"**
beside the title at the top and it changes in place. It works for finished
sessions too — what you listened to usually only becomes a problem when you look
at the list after listening to it all.

Once it starts, **the subtitle log window opens by itself.** This flow has no
video to embed, so there is no reason to stay on the main screen.

| | |
| --- | --- |
| Browser | Chromium-based only (Firefox and Safari do not hand over tab audio) |
| What is sent | 16kHz mono int16 PCM, `POST /api/ingest/<session>` every 2 seconds |
| Bandwidth | 32KB per second |

Keep three things in mind.

**It is tied to the browser.** Close that tab or stop sharing and the subtitles
stop too. The route that receives by URL runs on the server, independent of the
browser.

**It cannot be rewound.** The sound from while the sharing was cut is left
nowhere. But **it can be appended to the same session.** Open a stopped tab
session and "Resume" appears — pressing it asks you to share the tab again, and
from then on the numbering and times continue to pile up in the same subtitle
log. What could not be received is written into the subtitles as such.

Stop sharing or close that tab and the session becomes "ended". Since the server
did not die and that is the normal exit path, unlike a URL session it can be
resumed at any time as long as it is merely stopped.

**If transcription cannot keep up with realtime it is thrown away.** It holds up
to 5 minutes' worth and beyond that drops the oldest first while telling you how
many seconds were thrown away. Change to a lighter transcription engine then
(the "If the default transcriber is too heavy" section).

Since there is no video to attach, the on-screen subtitles and the offset slider
are unused. The way to read it is the subtitle log panel — or "⧉ Pop out".

### Audio (alternative 1): the extension hands over login cookies

Press **"🔑 Hand over login cookies, from this URL"** in the extension popup on
a YouTube tab and the extension reads this browser's YouTube login cookies
(HttpOnly included) at that moment, hands them to the server and starts "by
URL". If you had a stopped stream selected, it is a resume. It is not a switch
you leave on but **that one press**, and the file left on the server is deleted
with "⚙ Engines › YouTube login cookies › Delete". With that, the server keeps
receiving a membership stream even if you close the browser, and rewinding (DVR)
works too.

Know two things before you press it.

- **Account risk.** It amounts to running yt-dlp with the cookies of your
  everyday account. Receiving one stream locally is usually no problem, but
  yt-dlp's own warning is that YouTube may put up a bot check or a temporary
  restriction. That is why it is off by default and is one-shot.
- **Cookies are the key to the account.** The server keeps them only at
  `~/.local/share/mimiwatch/cookies/youtube.txt` with permission `0600` and does
  not put the contents on screen or in the logs. Delete them when you are done.
  They are read and handed over fresh every time, so even if YouTube swaps the
  cookies out (rotation) the newest at that moment is what goes.

Using cookies sends yt-dlp down a client that demands a JS runtime, so deno has
to be there ("Models & Tools").

### Audio (alternative 2): giving a cookie file directly

YouTube does not accept id and password login and OAuth no longer works either.
Cookies are all there is.

```sh
MIMIWATCH_YTDLP_COOKIES=/path/to/cookies.txt ./run.sh
```

**Using the cookies of your everyday browser as they are does not last long.**
YouTube keeps swapping out the account cookies of an open tab. As yt-dlp's own
guidance says, they have to be taken from an incognito window so they are not
rotated.

1. Open an incognito (new private) window and log in to YouTube
2. **In the same tab**, go to `https://www.youtube.com/robots.txt`
   (that window must have no other tab)
3. Export the `youtube.com` cookies to a file in Netscape format with an
   extension
4. **Close that window without logging out** — that session is never opened
   again, so the cookies are not rotated

For convenience there is also a path that reads straight from the browser. For
the reason above it is not recommended for YouTube.

```sh
MIMIWATCH_YTDLP_COOKIES_BROWSER=chrome ./run.sh
```

On Windows, put `$env:MIMIWATCH_YTDLP_COOKIES="C:\path\cookies.txt"` before
`.\run.ps1`. If both are given the file wins — given together, yt-dlp overwrites
the incognito session's cookies with the everyday ones.

**A cookie file is a key that can log in to that account.** Do not keep it
inside the repository, and narrow its permissions.

### Video: embedding is usually blocked

Members-only streams are often set up so that they cannot be embedded in another
site. Then this appears where the player would be.

> This video is set up so that it cannot be embedded in another site … keep it
> open on YouTube and read the subtitle log on the right — the subtitles keep
> piling up.

**This is not a problem we can fix.** The video's owner decides it, and it has
nothing to do with being logged in.

But **the subtitles work as they are.** The audio comes in separately by either
of the two routes above, so you can have YouTube up in another window and read
the subtitle log on the right. When receiving with cookies, use the offset
slider to align if you want the on-screen subtitles — automatic alignment needs
to read the playback position of the embedded player, and that player is not
there.

### Popping the subtitle log out

For streams whose embedding is blocked, a separate window holding just the
subtitle log can be opened. Press **⧉ Pop out** in the subtitle log header and a
tall narrow window opens. It is for keeping YouTube up on one side and this
window beside it.

This window has no list and no player, only the subtitle log. The subtitles pile
up in realtime exactly as in the main window — the same code draws them.
Closing the main window does not matter, but the server has to be up.

What can be adjusted inside the window:

| Tool | What it does |
| --- | --- |
| Both / translation only / source only | What to show on a subtitle log line |
| Font size | 11–24px |

The choices are remembered, and they apply to the main window's subtitle log too.

The URL is `?script=<video or stream key>`. You can put it in your bookmarks,
and if what it pointed at has been deleted it tells you so.

**Limits.** This window does not know the video, so there are no on-screen
subtitles and pressing a line does not jump to that point. It is a reading
window.

## Overlay mode (browser extension)

A stream whose embedding is blocked cannot be put into our page. **The extension
lays subtitles onto the YouTube page itself**, so it is not bound by that
restriction. You watch the video on YouTube as usual, and only the subtitles
appear on top of it.

There is one bonus. Here the real `<video>` can be grabbed, so `currentTime` is
read directly — on our page the embedded player's clock cannot be read, so live
subtitle alignment was manual.

### Installing

It is not put on the store. It is a thing that needs a local server anyway, so
it is kept in the repository and attached to releases as `mimiwatch-ext-*.zip` —
people using a bundle can download and unpack that.

1. Open `chrome://extensions` in Chrome
2. Switch on **Developer mode** at the top right
3. **Load unpacked** and pick this repository's `ext/` (or the `ext` folder from
   the unpacked zip)

To move up a version, overwrite the new files in the same folder and press
**refresh (⟳)** in `chrome://extensions`. Move the folder and the extension
disappears.

The server (`./run.sh`) has to be up separately.

### Using it

Press the extension icon on a YouTube tab and the popup appears.

**Overlaying something already received** — pick a live session or a VOD in
"What to overlay" and those subtitles attach to the screen. If that tab is not
holding the extension yet (which is the case right after reloading the
extension) it refreshes once by itself — it does not when it can reach it. The
spot you were watching jumping is a loss in itself.

For a VOD, playback has to reach that subtitle's time for it to appear on
screen. To check right away whether it attached, try switching on "Subtitle log
in the chat column" — that side shows everything accumulated, regardless of the
playback position.

**Transcribing anew on this tab** — there are two routes.

| | When | |
| --- | --- | --- |
| **By URL** | Ordinary public streams | The server receives it with yt-dlp. Keeps receiving even if you close the browser |
| **By this tab's sound** | Members-only and so on | Takes the sound coming from this tab. Ends when the tab closes |

There is no need to paste the URL again — the extension already knows which tab
it is. **The session name uses the tab title as it is, too.** On the page side
Chrome does not hand that title over, so it was left as just "Tab audio", but
the extension can simply read it.

Language, genre and refinement are remembered for the next start. **Refinement
starts switched off** — different from the default on the mimiwatch page. That
side handles VODs as well, but the extension only starts live, and in live
refinement is usually a loss. It waits 2 seconds for one utterance group to
finish, joins it and transcribes it again, so when speech goes back and forth
quickly the subtitle settles late and a line already read changes entirely.

Subtitle mode, font size, background and offset are adjusted in the popup, and
the position is set by dragging the subtitle.

Switch on **"Subtitle log in the chat column"** and the subtitle log stands in
the right column. On live it goes in in place of the chat (switch it off and the
chat comes back), otherwise it attaches above the related videos. Pressing a
line moves to that point — here the real `<video>` can be grabbed, so it simply
works. Dragging downwards changes the height, and scrolling up to read stops the
following by itself.

It is read-only. Editing, re-translating and exporting are on the mimiwatch page.

**Moving to a different video takes the subtitles down.** The session is bound
to the tab, not to the video, so left alone old subtitles would keep attaching
over the wrong video. When it can tell which video the subtitles belong to it
takes them down by itself, and when it cannot — started by tab sound but the URL
could not be read, and so on — it does not take them down and asks over the
player. Choose "Keep" and it remembers that video as this subtitle's own and
does not ask again.

**A stopped stream is resumed.** Pick a stopped stream ("· stopped") in the
picker and "By URL" and "By this tab's sound" change to **"▶ Resume (from this URL / from
this tab's sound)"**, appending to the same session from whichever source you
pressed — it may differ from the stored source. A stream received by URL that
turns members-only partway through can go to tab sound, and one received by tab
sound that you want to carry on after closing the browser can be changed to
URL. It is the same whether the server died or the user stopped it. A stream
that is over ("· stream over") has nothing to resume, so transcribing the whole
video is done on the mimiwatch page.

**"Hide from page" and "■ Stop transcribing" are different.**

The former only takes it down on this tab — the server keeps transcribing.
**It is a toggle, so pressing again changes it to "Put back on page".** It
leaves the picker as it is, so it does not forget what you were watching.

The latter ends the session on the server. The accumulated subtitles stay in both
cases and can be chosen again.

**If the subtitles are not visible**, look at the status line at the bottom of
the popup. It splits "not visible" up and says which — `player not found` /
`no room on screen` / `no subtitle in the current segment` / `subtitles off` /
`clock stopped`. Where to look diverges from there. "Hide from page" only clears it from
the screen — to end what was being transcribed, it is "Stop" on the mimiwatch
page.

List management, engine settings and subtitle editing are not in the popup. The
mimiwatch page keeps them — building those too would make two sets.

## Known limits

**Aligning live subtitles with the video is manual.** Match them with the offset
slider. The value is remembered per channel, so only the first broadcast of a
channel takes the effort -- the next one comes up already matched. VODs match
automatically.

**In multiview, streams that are not focused are not transcribed.** They hold
only the last 30 seconds of sound and start from there when the focus arrives.
Transcribing all four would mean running four transcription models at once, so it
is not done. One ffmpeg comes up per stream.

**Overlapping speech cannot be separated.** When two people talk at the same
time one is buried or they are mixed. Setting the content type short reduces it
but does not remove it.

**Speaker tags are VOD-only.** Live segments are short, so speaker embeddings do
not settle. A 70-second stream once produced six people, and there were not that
many.

**English is 1.7× slower than a dedicated model.** The quality is the same. It
is enough for live but may be noticeable on a long VOD.

**Chinese has never been measured.** Whisper supports it, but there is no basis
for comparison.

## If something goes wrong

**It says the model is missing** — run `./install.sh` again. What is already
downloaded is skipped. If you use a different location, check that
`MIMIWATCH_MODEL_DIR` is the same as it was at install time.

**The server does not come up** — if `.venv` is missing, `run.sh` says so. Run
`./install.sh` first.

**I started live and nothing happens** — look at the browser console. If
subtitles are being printed in the server log (the `[ja/whisper-...]` lines) but
the screen is empty, it is a problem on the screen side. Clear out stale scripts
with a hard refresh (`Cmd+Shift+R`).

**The transcription comes out in the wrong language** — if you do not specify
the source language when adding the video, the model decides for itself. Specify
it.

**The same words appear twice in the subtitles** — this is the case where the
refined line failed to absorb the final. Send the `[refine/...]` line from the
server log together with the final before it.

**The port is already in use** — a previous server may still be alive.

```sh
lsof -ti:8900 | xargs kill
```

### Where things are

| Path | Contents |
|---|---|
| `backends.json` | Engine settings (not committed to git). `MIMIWATCH_CONFIG` can point at another file |
| `data/mimiwatch.db` | Jobs, sessions and **all the subtitles** (VOD and live). `MIMIWATCH_DATA_DIR` can move the location |
| `data/legacy/` | `<video id>.json` left behind by old versions. Nobody reads them |
| `~/.local/share/mimiwatch/models` | Models (`%LOCALAPPDATA%\mimiwatch\models` on Windows) |
| `ext/` | The browser extension (loaded unpacked) |

Deleting `data/` wholesale returns it to the initial state. The models are not
deleted.

**VOD subtitles are in SQLite too.** It used to be one JSON file per video, and
then fixing one subtitle line meant rewriting that video's subtitles from end to
end, and dying mid-write lost all of them. The move happens automatically once on
first run. **The originals are not deleted but moved to `data/legacy/`** — use it
for a while and delete them if it is fine.
