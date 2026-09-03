# Shipping as a bundle

*[한국어](PACKAGING.ko.md)*

Instead of cloning the repository and running `./install.sh`, this is the path
where you get a **single-executable bundle** and double-click it. We build for
macOS (Apple Silicon) and Windows (x64).

```
mimiwatch.app            macOS -- double-click it in Finder and the screen opens in your browser
mimiwatch\mimiwatch.exe  Windows -- double-click it and one console window appears, then the browser opens
```

The Windows bundle is Vulkan only (NVIDIA included). The CUDA build could not
carry RTX 50, so it was dropped from the releases -- the circumstances and how
to build it yourself are in the NVIDIA section of [WINDOWS.md](WINDOWS.md).

## What goes into the bundle and what does not

**Included**: Python, the server and the screen, the four runtimes
(transcribe.cpp, llama.cpp, sherpa-onnx, CTranslate2), yt-dlp, the CA
certificate bundle. macOS carries Metal, Windows carries Vulkan (+CPU). About
110MB (macOS), a little over 45MB as a zip.

**Not included**: the models (6.3GB) and ffmpeg. The models are meant to live
outside the program anyway (`~/.local/share/mimiwatch/models`, on Windows
`%LOCALAPPDATA%\mimiwatch\models`), so changing the version does not download
them again. Someone who was running from the repository keeps using the same
models. **On the first run, a banner at the top of the screen says "nothing
needed for transcription is here yet" and "First-time setup" appears, which lets
you pick the transcription and translation engines and downloads only the models
that combination needs.** The
default is the light CPU engines (SenseVoice Small + M2M-100, about 730MB). To
pick more, it is "Manage › ⚙ Engine management › Models and tools".

As for ffmpeg, if the system has it that is what is used (the Homebrew location
is checked too), and if not, a single static build is downloaded from the same
screen. It is kept out of the bundle to avoid the GPL distribution problem --
we are not the ones distributing it, the user downloads it.

## Where the files land

Running from the repository and running as a bundle differ. The inside of a
bundle is read-only, so the settings and the store come out into the user area.
The rules live in one place, `paths.py`.

| | From the repository (`./run.sh`) | As a bundle |
|---|---|---|
| Models | `~/.local/share/mimiwatch/models` | same |
| Tools (ffmpeg, yt-dlp) | `~/.local/share/mimiwatch/tools` | same |
| Settings `backends.json` | inside the repository | `~/.local/share/mimiwatch/backends.json` |
| Subtitle DB and audio `data/` | inside the repository | `~/.local/share/mimiwatch/data` |
| Log | the terminal | `~/.local/share/mimiwatch/mimiwatch.log` (the macOS .app has no window) |

On Windows, `%LOCALAPPDATA%\mimiwatch` takes the place of
`~/.local/share/mimiwatch`. `MIMIWATCH_HOME` moves the whole root, and
`MIMIWATCH_MODEL_DIR`, `MIMIWATCH_DATA_DIR` and `MIMIWATCH_CONFIG` move them one
at a time.

## yt-dlp goes stale -- which is why we recommend the standalone executable

Keeping yt-dlp in the virtualenv is what made "re-run the install script and it
updates" hold (README, "yt-dlp lives in the virtualenv"), and the bundle takes
that back. The yt-dlp inside the bundle has its version baked in, so a few
months later, when YouTube changes its extraction path, it becomes "could not
find any audio".

That is why "Models and tools" has the **yt-dlp standalone executable**. That
file, the one yt-dlp itself distributes, updates itself with `-U`, and when it
sits in the tools directory it is used ahead of the one inside the bundle. When
live says "got no formats at all", downloading it (or downloading it again, if
it is already there, to get the latest) is all it takes. There is no need to
build a new bundle.

**YouTube also requires a JS runtime (deno)** (since 2025.11). It is needed for
VODs and membership streams; public live HLS does without it. If you download it
from "Models and tools", the server tells yt-dlp directly with
`--js-runtimes deno:<path>`, so it works even in an .app launched from Finder
(where PATH is short). The solver script (yt-dlp-ejs) is inside the bundle.

The yt-dlp inside the bundle is called as `mimiwatch --ytdlp …` -- the bundle
has no Python to call `python -m yt_dlp` with, so the server launches itself
again. The reason for keeping it as a child process is unchanged (a time limit,
and being able to kill it).

## Building

```sh
packaging/build.sh                 # macOS/Linux → dist/mimiwatch-<version>-macos-arm64.zip
.\packaging\build.ps1              # Windows     → dist\mimiwatch-<version>-windows-x64.zip
```

A build-only virtualenv (`.venv-build`) is created separately. The development
`.venv` has things like pytest and ruff mixed in, and the bundle should contain
only what is needed to run.

- **macOS**: `llama-cpp-python` has no macOS wheel on PyPI, so it is built from
  source (cmake needed, `brew install cmake`). On Apple Silicon, Metal is on by
  default. The first time takes 5-10 minutes; after that pip remembers the wheel
  it built and it takes a little over a minute.
- **Windows**: nothing is built. `llama-cpp-python` comes from the builder's own
  index as a Vulkan wheel (it falls back to CPU when there is no GPU), and
  `transcribe-cpp` is a PyPI wheel. `-Backend cuda` (not included in the
  releases) downloads the cu124 wheel and pulls the runtime DLLs that the wheel
  lacks (cudart, cuBLAS) out of the `nvidia-*-cu12` packages into
  `llama_cpp\lib` -- a little over 726MB.
- **GitHub Actions** (`.github/workflows/build.yml`): pushing a `v*` tag builds
  both platforms and attaches them to the release. It can also be run by hand
  (`workflow_dispatch`).

The spec is `packaging/mimiwatch.spec`. The four runtimes are moved with
`collect_all`, which keeps the package directory structure as it is, so the rule
by which they find each other through `@loader_path` holds inside the bundle
too. **transcribe_cpp finds its native provider through the package metadata's
entry point**, so the dist-info goes in with it (`copy_metadata`) -- without
that it cannot even start.

## What gets in the way the first time you open it

**macOS -- why it is blocked.** The app is ad-hoc signed and that signature is
valid (it passes `codesign --verify --deep --strict`). What it lacks is Apple's
**notarization**, so Gatekeeper blocks files marked with quarantine.
Notarization requires a paid Developer ID ($99/year), which this distribution
does not have. **Strip the quarantine once and it opens without a warning from
then on** — because Gatekeeper leaves a validly ad-hoc-signed app alone as long
as the quarantine is gone.

**Why it appears every time.** If you run it straight out of somewhere like
Downloads, macOS moves the app to a random read-only path and runs it there (App
Translocation). So the "Open Anyway" approval is tied to that path and does not
survive into the next run. **Move it once into `/Applications` (or
any folder)** and translocation stops, and the approval stays.

The recommended order:

1. **If you download it with the terminal there is no quarantine to begin with,
   so it opens right away.** A file fetched with `curl` gets no quarantine
   attribute.
   ```sh
   curl -L -o mimiwatch.zip https://github.com/chisacam/mimiwatch/releases/latest/download/mimiwatch-<version>-macos-arm64.zip
   ditto -x -k mimiwatch.zip ~/Applications/    # extracts with the symlinks preserved, and puts it in place
   open ~/Applications/mimiwatch.app
   ```
2. If you downloaded it with a browser: **move it into the Applications folder
   first**, double-click it, close the blocking dialog, and then **within an
   hour** go to System Settings › Privacy & Security, at the very bottom, and
   "Open Anyway". Since you moved it, it will not ask again after that.
3. To be done with it in one line — strip the quarantine after moving it. There
   is no warning after that.
   ```sh
   xattr -dr com.apple.quarantine ~/Applications/mimiwatch.app
   ```

If "is damaged and can't be opened" appears, the signature is broken — the zip
has to be extracted **with Finder (Archive Utility) or `ditto`**. Other archive
tools expand the symlinks inside the bundle into real files, and the signature
hashes then do not match (the .app from PyInstaller 6 joins Frameworks and
Resources with links).

**To go all the way to notarization**, join the Apple Developer Program
($99/year), get a Developer ID certificate, sign at build time with
`MIMIWATCH_CODESIGN="Developer ID Application: Name (TeamID)"`, then notarize
and staple with `xcrun notarytool submit`, and even the first-run warning
disappears. As it stands, `packaging/build.sh` signs with that certificate
instead of ad-hoc when that environment variable is present.

**Windows**: SmartScreen blocks it as "unknown publisher". "More info › Run
anyway".

Both warnings appear because there is no signing certificate ($99 a year / a
code signing certificate). This is a solo hobby project, so it was not bought.

## Diagnostics

```sh
mimiwatch --doctor                 # prints the prerequisites, the backends and a model load in one go
mimiwatch --doctor "https://www.youtube.com/live/..."
mimiwatch --port 8951 --no-browser
```

This is `bench/doctor.py`, put into the bundle. If something does not work,
pasting that output is enough.

## What was checked and what was not (2026-08-29)

Checked: on macOS (M5 Pro, macOS 26) the bundle was built, `--doctor` loaded
whisper onto both devices, Metal and CPU, the yt-dlp inside the bundle resolved
a YouTube address, and the settings, the store and the log were seen appearing
in the user area. Model and tool downloads were actually fetched and run for all
four kinds: GitHub releases, Hugging Face, the gzip static ffmpeg and the yt-dlp
standalone executable.

What was not checked: **the Windows build has never been run on Windows.**
`build.ps1` follows the same helper rules as `install.ps1` and was written on
the assumption that it will run on the Actions runner. Check it on the first tag
build. The path of double-clicking the .app in Finder (whether it finds Homebrew
ffmpeg in an environment where PATH is short) has also not been seen by an
actual click rather than from a terminal.

## Update (automatic updates)

The bundle knows its own version -- the build bakes `MIMIWATCH_VERSION` (the
tag) into `_version.txt` (mimiwatch.spec). Once a day the server checks GitHub
releases/latest, and if the tag is newer it tells you on screen; on "Download →
restart and apply":

1. The asset for this platform (`mimiwatch-<version>-macos-arm64.zip` /
   `-windows-x64.zip`) is downloaded into `updates/` in the user area. `.part`
   resume is the same as for models. The Windows `-cuda`/`-cpu` variants are not
   subject to automatic updates.
2. The swap script (`updates/apply.sh`, `apply.ps1`) is launched and the server
   shuts itself down. The script waits for the process to end, moves the old
   bundle aside as `.old`, puts the new one in its place, and launches it again
   **with the same arguments**. On macOS it extracts with `ditto` (preserving
   the .app's symlinks and signature) and even does `xattr -cr`, so you do not
   go through the Gatekeeper business again.
3. On failure the old bundle is put back. What happened is in
   `updates/apply.log`.

The models, the settings and the subtitle DB are outside the bundle (in the user
area), so they stay as they are across a version change. To turn the check off,
`"update_check": false` in `backends.json` or `MIMIWATCH_NO_UPDATE_CHECK=1`.
When running from the repository it only notifies and refuses to apply (it
points you at `git pull`).
