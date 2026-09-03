# Windows

*[한국어](WINDOWS.ko.md)*

**The easiest path is the bundle.** Download `mimiwatch-*-windows-x64.zip` from
the releases, unpack it and double-click `mimiwatch.exe`: a console window opens
and the screen comes up in your browser. You need neither Python nor these
scripts, and the models are fetched from the screen on first run. If SmartScreen
blocks it as "unknown publisher", use "More info › Run anyway".
Details in [PACKAGING.md](PACKAGING.md). Below is the path that runs from the repository.

```powershell
git clone https://github.com/chisacam/mimiwatch.git
cd mimiwatch
winget install --id Python.Python.3.12
winget install --id Gyan.FFmpeg     # Optional. Without it, you can fetch it from 「Models · Tools」 on the screen
# If you just installed those two, open a new terminal (PATH is refreshed)
# yt-dlp is put into the virtual environment at its latest by install.ps1
.\install.ps1
.\run.ps1
```

Open http://localhost:8900.

If script execution is blocked, lift it for this session only. There is no need
to change the machine-wide policy.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## What differs from macOS/Linux

**Neither side builds anything.** The transcription runtime (transcribe.cpp) is a
prebuilt wheel on macOS, Linux and Windows alike. The only differences are where
the translation runtime (llama.cpp) comes from and where the models live.

| | macOS / Linux | Windows |
|---|---|---|
| Transcription runtime | `pip install transcribe-cpp` (wheel, includes Metal / Vulkan) | same (win_amd64 wheel, includes Vulkan) |
| GPU acceleration | Metal (automatic) / Vulkan | **Vulkan** (included in the wheel) |
| Translation runtime | llama-cpp-python from PyPI (source build on macOS) | win_amd64 wheel from the maintainer's index |
| Prerequisites | python3 (ffmpeg recommended) | python (ffmpeg recommended) |
| Model location | `~/.local/share/mimiwatch/models` | `%LOCALAPPDATA%\mimiwatch\models` |

## GPU acceleration: we use Vulkan

`.\install.ps1` picks the Vulkan path when there is a graphics card and a
`System32\vulkan-1.dll`. **AMD, NVIDIA and Intel all take the same path.**

There is nothing extra to install. The Vulkan *SDK* is only needed for building,
and we do not build. The Vulkan *runtime* (`vulkan-1.dll`) ships with modern
graphics drivers.

```powershell
.\install.ps1                    # Picks for itself (cuda on NVIDIA, vulkan on other GPUs)
.\install.ps1 -Backend cpu       # Does not use the GPU
.\install.ps1 -Backend vulkan    # Ignores the automatic decision and forces it
.\install.ps1 -Backend cuda      # NVIDIA only (the section below)
```

When the install finishes it prints what was actually detected.

```
> 전사 런타임 (transcribe.cpp)
  [OK] 설치
  쓸 수 있는 백엔드: cpu, vulkan
```

If `vulkan` is missing, update the graphics driver. Transcription also runs on
the CPU -- it is only slower.

### On integrated graphics, measure the CPU too

`.\install.ps1 -Backend` only decides **which llama-cpp-python wheel to
install**. Where things actually run is decided by `device` in `backends.json`,
and transcription and translation can be chosen separately.

```json
{ "id": "tcpp-best",   "backend": "tcpp",  "device": "cpu" }
{ "id": "local-gemma", "backend": "gemma", "device": "cpu" }
```

Integrated graphics (the 780M and the like) share system memory with the CPU and
have narrow bandwidth. On a laptop with cores to spare the CPU side can be
faster, or at least it avoids having transcription and translation fight over
the same iGPU.

**If the default transcriber is too heavy, there are lighter ones.**

```json
{ "id": "tcpp-lite", "label": "SenseVoice Small (light · CPU)",
  "backend": "tcpp", "model": "SenseVoiceSmall-Q8_0.gguf", "device": "cpu" }
{ "id": "tcpp-lite-en", "label": "Moonshine base (light · English only)",
  "backend": "tcpp", "model": "moonshine-base-Q8_0.gguf", "device": "cpu" }
```

`SenseVoice Small` (241MB) is 8 times faster than whisper on the CPU, and **for
English streams `Moonshine base` (74MB) is 12 times faster**. Moonshine's English
quality is effectively the same as the default, but it refuses other languages.

Put them in `asr_backends` and they appear in the "Transcription" selector on
the screen. For details see "Running on CPU" in the README.

## NVIDIA: the CUDA build was dropped from the releases

On NVIDIA too, use the release bundle (`windows-x64`, Vulkan). We did build a CUDA
build (`build.ps1 -Backend cuda`), but decided to drop it from the releases.
llama-cpp-python's cu124 wheel **carries no RTX 50 (Blackwell, sm_120) kernels**, so
on the newest cards it either does not open or has to lean on PTX JIT; and since
transcription (transcribe.cpp) has no CUDA wheel anyway and stays on Vulkan, all
that is gained is translation speed -- while the bundle grows 7-fold to 726MB.
That trade does not add up.

You can build it yourself (`.\packaging\build.ps1 -Backend cuda`, or `.\install.ps1
-Backend cuda`). The GPUs supported then are as in the table below, which was
confirmed by opening the `ggml-cuda.dll` fatbin header in the wheel
(llama-cpp-python 0.3.35 cu124).

| Generation | Representative products | Compiled targets | Supported |
|---|---|---|---|
| Pascal | GeForce GTX 1050~1080 Ti, TITAN Xp | sm_60 · sm_61 | ✓ |
| Volta | TITAN V, Tesla V100 | sm_70 | ✓ |
| Turing | GTX 1650~1660 Ti, RTX 2060~2080 Ti | sm_75 | ✓ |
| Ampere | RTX 3050~3090 Ti, A100 | sm_86 · sm_80 | ✓ |
| Ada Lovelace | RTX 4050~4090 | sm_89 | ✓ |
| Hopper | H100 | sm_90 | ✓ |
| Blackwell | RTX 5050~5090 | none (the driver JITs the sm_90 PTX) | **unverified** -- the Vulkan build if it fails |
| Maxwell and older | GTX 900 · pre-700 | none | ✗ use the Vulkan build |

**The driver must be 551.61 or newer** (the CUDA 12.4 runtime). On older drivers the
translator fails to open a CUDA device -- then either raise the driver or use the
Vulkan build. The CUDA toolkit does not need to be installed: the runtimes that are
needed (cudart64_12, cublas64_12, cublasLt64_12) are inside the bundle (pulled out of
the `nvidia-*-cu12` PyPI packages and placed next to llama_cpp).

## AMD: why we do not use whisper.cpp-amd

[lemonade-sdk/whisper.cpp-amd][wa] is a real and well-made project. It
distributes prebuilt ROCm, Vulkan, Ryzen AI NPU and CPU builds, and it bundles
the ROCm libraries whole so there is nothing separate to install. **If you are
running whisper on AMD, it is a good choice.**

It does not drop into mimiwatch as-is, though. Three things get in the way.

1. **It is a different library.** What mimiwatch uses is not whisper.cpp but
   [transcribe.cpp][tc]. The model files differ too -- whisper.cpp takes
   `ggml-*.bin`, transcribe.cpp takes `handy-computer`'s GGUF.
2. **There is only `whisper-cli.exe`.** With no server, it cannot be reached
   through mimiwatch's external transcription path either (the OpenAI-compatible
   `/v1/audio/transcriptions`).
3. **It cannot be used for live.** Calling a CLI per segment means reloading the
   model every time. The live path uses Python bindings that load the model once
   and reuse it.

**The requirement itself -- AMD acceleration -- is already met by Vulkan.** Same
library, same model, same bindings, and it works for live as well.

The Ryzen AI **NPU** is a different story. transcribe.cpp has no NPU backend, so
there is no path today. Using it would mean writing a new transcription adapter
wrapping whisper.cpp-amd, and even then it could not be used for live (point 3
above). It becomes worth revisiting when transcribe.cpp gains an NPU backend, or
whisper.cpp-amd puts out a server.

[wa]: https://github.com/lemonade-sdk/whisper.cpp-amd
[tc]: https://github.com/handy-computer/transcribe.cpp

## Members-only streams

Give the cookie file path as an environment variable. For details and the
procedure for exporting cookies safely, see "Members-only streams" in the
README.

```powershell
$env:MIMIWATCH_YTDLP_COOKIES = "C:\Users\USERNAME\cookies.txt"
.\run.ps1
```

## Changing the model location

```powershell
.\install.ps1 -ModelDir 'D:\models'
.\run.ps1     -ModelDir 'D:\models'
```

**You must give the same value at install time and at run time.** Otherwise the
server will not find the models.

## Where it snags

| Symptom | Cause | Fix |
|---|---|---|
| `.\install.ps1` does not run | Execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `python` opens the Microsoft Store | The Windows default placeholder | `winget install --id Python.Python.3.12`, then a new terminal |
| `ffmpeg` is not found | The PATH of the already-open terminal is stale | Open a new terminal |
| llama-cpp-python fails to install | No wheel for that Python version | Retry with `-Backend cpu`; if that still fails, use Python 3.12 |
| No `vulkan` among the backends | The driver is stale | Update the graphics driver. It runs on the CPU without it |
| The download breaks off | — | Just run it again. A partial download stays as `.part` and resumes. It also works from 「Models · Tools」 on the screen |
| It stops midway with `NativeCommandError` | A defect in 0.1 (issue #1) | Get the latest version. This was Windows PowerShell 5.1 turning a single stderr line from a command into a terminating error |
| Transcription fails right after it starts | The model may have failed to load onto the GPU | Check with `bench/doctor.py`, and if only `device=auto` fails, write `"device": "cpu"` into `backends.json` |
| A live session fails with `OSError: [WinError 6] 핸들이 잘못되었습니다` | A defect in 0.3.1 | Get the latest version. This was a failure to start ffmpeg in a process launched with a broken standard-error handle (a bundle started without a console, Task Scheduler, a service). To stay on 0.3.1, launch it yourself from a console window |
| "오디오를 찾지 못했습니다" in live | **yt-dlp is stale** | Run `.\install.ps1` again. The yt-dlp inside the virtual environment is raised to the latest |

## What has been verified and what has not

**These scripts have never yet been run on Windows.** But a good part of them has
actually been run under PowerShell 7 on macOS. The places that are easy to get
wrong are mostly platform-independent -- exit-code checks, passing a here-string
as an argument, whether temporary files get cleaned up on failure, and the like.

```sh
brew install powershell
.venv/bin/python bench/ps_test.py
```

`bench/ps_test.py` runs `install.ps1` by **taking line ranges out of it rather
than copying it**. A copy would let what is tested drift apart from what is
shipped.

Verified:

- Syntax and PSScriptAnalyzer static analysis (`install.ps1`, `run.ps1`)
- Model download: producing the finished file, cleaning up `.part`, skipping when
  it already exists, `exit 1` on failure
- Python discovery, splatting an empty array
- The check step that passes a here-string via `-c` (both the passing and the
  failing path)
- Whether it drops to the CPU without stopping when there is no GPU, no WMI, no
  drive
- That the Python code has no POSIX-only elements (`fcntl`, `fork`, `/dev/null`)
- That the required wheels really exist as `win_amd64` (downloaded
  `transcribe-cpp-native` 0.2.2 to confirm that `ggml-vulkan.dll` is inside)

Three bugs this testing actually caught:

1. `$LASTEXITCODE` after `& cmd | Select-Object -First 1` is not updated (the
   pipeline is cut short early). In a fresh shell this variable is empty, so
   **even with Python installed it said "not found" and stopped.**
2. When `$env:SystemRoot` is empty, `Join-Path` throws. Of all places, it stops
   the whole install right at the "we could not detect a GPU, so let us go CPU"
   spot.
3. `Test-Path` has the same problem in front of a drive that does not exist.

**What has not been verified**: the things that only happen on Windows -- the
`winget` guidance, the GPU list from `Get-CimInstance`, the Vulkan loader
detection, whether the wheel actually installs and `vulkan` shows up in
`transcribe_cpp.backends()`, whether it gets all the way through without MSVC.

### What this harness missed

**Windows PowerShell 5.1 turns a single stderr line from a native command into a
terminating error** (`NativeCommandError`). It does so when
`$ErrorActionPreference='Stop'`, and `2>$null` does not stop it. pwsh 7 has no
such behaviour, so it never surfaced in the macOS testing, and an actual Windows
user reported it as issue #1.

These scripts have several places where stderr is normal -- the check that tries
to `import` a package that is not installed yet, `curl`'s progress bar, `pip`'s
notices. So every native call was gathered into one of two helpers:
`Invoke-Native` (lets the output flow through as-is) and `Get-Native` (catches it
and returns it). The places where the progress bar has to stay alive are called
through `Start-Process -NoNewWindow` -- it hands over the console handle, so
stderr never passes through PowerShell's error stream at all.

`[9]` in the harness guards this regression. In a form that can be checked under
pwsh 7 as well, it watches whether the helper catches stderr and returns only
the exit code.

## When you are stuck: doctor

It prints in one go what works and what does not. Running this one thing and
pasting the output is faster than trading several commands back and forth.

```powershell
.\.venv\Scripts\python.exe bench\doctor.py
.\.venv\Scripts\python.exe bench\doctor.py "https://www.youtube.com/live/..."
```

It looks at the prerequisites, the available backends, **loading the
transcription model with auto and with cpu each**, the translation backend
configuration, and, if you give it a URL, the resolution of that URL too. It
does not touch the translation model (5GB), so it takes a few seconds.

If only `device=auto` fails and `device=cpu` works, the model failed to load onto
the GPU. Writing `"device": "cpu"` into `backends.json` is enough ("Running on
CPU" in the README).

Try it first and let me know if anything snags.
