# mimiwatch

*[한국어](README.ko.md)*

A **local tool for understanding YouTube videos and live streams in your own
language**. It lays transcription subtitles and translation subtitles over the
original video.

Every model runs on this machine. The only things that leave it are the
requests that fetch a video's audio, and a once-a-day query asking GitHub
Releases whether a new version exists (to turn that off, see
[Guide › Updates](docs/GUIDE.md#updates)).

## What it does

**VOD** — give it a URL and it fetches the audio, transcribes it and
translates it. Each utterance carries a media timestamp, so the subtitles
**line up automatically** with the player's playback position.

**Live** — it writes down a stream while it is running. Short finals go out
first, and when one utterance group ends, the group is joined, decoded again,
and the refined line takes their place.

**Local files** — video and audio files on this machine (mp3, wav, mp4, mkv
and whatever else ffmpeg opens) go through the same path for transcription and
translation.

**Multiview** — up to four live streams side by side, with only the focused
tile's sound and subtitles coming out.

**Browser extension** — for streams where embedding is blocked, subtitles go
onto the YouTube page itself.

**When the languages are the same, it does nothing.** A Korean user watching a
Korean video gets neither translation nor subtitles.

## Requirements

- macOS (Apple Silicon or Intel), Linux, or Windows 10 1803 or later
- About 7GB of disk (6.3GB of models — about 730MB if you use only the light default pair)
- Python 3.10 or later to run from the repository. With the bundle you do not even need Python

**Nothing is built.** The transcription runtime (transcribe.cpp) uses prebuilt
wheels on mac, Linux and Windows alike, and the mac wheel has Metal in it.

`ffmpeg` is recommended, not required. If the system has it, that one is used;
if not, you can download a single static build from "Engines › Models & Tools"
in the UI.

```sh
brew install ffmpeg        # Windows: winget install --id Gyan.FFmpeg
```

`yt-dlp` is not a prerequisite. The install script puts the latest one inside
the virtualenv.

## Two paths

**As a bundle.** Download `mimiwatch-*-macos-arm64.zip` or
`mimiwatch-*-windows-x64.zip` from the releases, unpack it and double-click.
The UI opens in the browser, and on a first run "Initial setup" comes up, where
you pick engines and download the models that go with them. The Gatekeeper and
SmartScreen prompts that catch you on the first open, and where the files are
put, are in [docs/PACKAGING.md](docs/PACKAGING.md).

**From the repository.** Install as below. This is the side you want if you are
going to change the code or run the measurement scripts.

## Installation

```sh
git clone https://github.com/chisacam/mimiwatch.git
cd mimiwatch
./install.sh               # Windows: .\install.ps1
```

The script creates a virtualenv, installs the dependencies, downloads the
models of the default pair, and copies `backends.example.json` to
`backends.json`. **It skips steps that are already done**, so if a download
breaks off, just run it again.

**The default is the light CPU engines** (SenseVoice Small for transcription,
M2M-100 for translation). Quality is far better on the heavy side
(whisper-large-v3-turbo, Gemma 4) — if you have a GPU and memory to spare, move
up in "Initial setup" on the first run, or in "Manage › ⚙ Engines". To fetch
Gemma as well at install time:

```sh
WITH_GEMMA=1 ./install.sh        # Windows: .\install.ps1 -WithGemma
```

Models live in `~/.local/share/mimiwatch/models` by default, and in
`%LOCALAPPDATA%\mimiwatch\models` on Windows. `MIMIWATCH_MODEL_DIR` changes
that. Which models exist and what is needed is shown by "Models & Tools" in the
UI and by `modelhub.py list`, from the same list.

## Running

```sh
./run.sh            # Windows: .\run.ps1
```

Open http://localhost:8900. For another port, `PORT=8951 ./run.sh`
(on Windows `.\run.ps1 -Port 8951`). If you changed the model location at
install time, give the same environment variable when running too.

To turn it off, press **"⏻ Quit"** at the right end of the top bar. It closes
the streams it is receiving properly first, then stops the server. `Ctrl-C` in
the terminal takes the same path.

## How to use it

The screen has three columns. **The video library is on the left**, the player
in the middle, **the subtitle transcript on the right**. Either side folds
away — `V` for the library, `S` for the transcript.

1. Into **"＋ Add" on the left** goes a YouTube URL, a Twitch channel URL, an
   m3u8 URL, or a file path on this machine. Whether it is live or a VOD is for
   the server to decide.
2. **Set the source language.** Left empty, the model decides for itself, and
   when that detection wavers a whole sentence comes out in another language.
3. Pick **the transcription engine, the translation engine and the genre**
   together, then press "Start". On a low-spec machine, pick the light engines.
   "Manage" at the top can change them while a job is running.
4. If a subtitle is off, fix the source text, the translation and the timing in
   the **✎ Edit** mode of the transcript on the right, and pick a passage to
   translate again in the **⟳ Translate** mode. **⤓ Export** gives you SRT,
   WebVTT, text and JSON.

Everything else — live options, multiview, resuming a past stream, swapping
engines and CPU settings, members-only streams, the browser extension, updates,
and what to do when something goes wrong — is in
**[docs/GUIDE.md](docs/GUIDE.md)**.

## Documentation

- [`docs/GUIDE.md`](docs/GUIDE.md) — detailed usage guide
- [`docs/PACKAGING.md`](docs/PACKAGING.md) — bundle distribution and updates
- [`docs/WINDOWS.md`](docs/WINDOWS.md) — what differs on Windows
- [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) — requirements and design decisions
- [`measurements/RESULTS.md`](measurements/RESULTS.md) — model selection and performance measurements
- [`AGENTS.md`](AGENTS.md) — rules for the people and agents who change the code

## Credits

The structure of the transcription pipeline (VAD splitting, lead-in audio,
two-pass refinement) was taken from
[hayamimi](https://github.com/oboroge0/hayamimi) (MIT, oboroge0). Now only the
parts that are needed are ported into `stream.py` and `speaker_id.py`, with no
dependency on that repository.

On-screen playback of m3u8 streams uses
[hls.js](https://github.com/video-dev/hls.js) (Apache-2.0, video-dev). It is
bundled as it is in `web/vendor/hls.min.js`, and its license sits next to it in
`hls.LICENSE.txt`.

## License

The code and the program are [PolyForm Noncommercial 1.0.0](LICENSE).
**Anyone whose purpose is not commercial** may use it, change it and share it —
personal use, research, education, and use by nonprofit organizations and
public institutions are included here. For commercial use, get permission
separately.

For the structure of the live transcription loop taken from hayamimi (MIT),
that project's notice remains inside `LICENSE` as it is. The models and ffmpeg
are not contained in the repository; the user downloads them directly and they
follow their own licenses (for example the Gemma terms of use).
