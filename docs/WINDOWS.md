# 윈도우

**가장 쉬운 길은 묶음입니다.** 릴리스에서 `mimiwatch-*-windows-x64.zip`을 받아
풀고 `mimiwatch.exe`를 두 번 누르면 콘솔 창이 하나 뜨고 브라우저에 화면이
열립니다. 파이썬도 이 스크립트도 필요 없고, 모델은 첫 실행 때 화면에서
받습니다. SmartScreen이 「알 수 없는 게시자」로 막으면 「추가 정보 › 실행」.
자세한 것은 [PACKAGING.md](PACKAGING.md). 아래는 저장소에서 돌리는 길입니다.

```powershell
git clone https://github.com/chisacam/mimiwatch.git
cd mimiwatch
winget install --id Python.Python.3.12
winget install --id Gyan.FFmpeg     # 선택. 없으면 화면의 「모델·도구」에서 받을 수 있습니다
# 위 둘을 새로 깔았다면 터미널을 새로 여십시오 (PATH가 갱신됩니다)
# yt-dlp는 install.ps1 이 가상환경 안에 최신으로 넣습니다
.\install.ps1
.\run.ps1
```

http://localhost:8900 을 엽니다.

스크립트 실행이 막히면 이 세션에서만 풀어 주십시오. 기계 전체 정책을
바꿀 필요는 없습니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 맥/리눅스와 다른 점

**어느 쪽도 빌드하지 않습니다.** 전사 런타임(transcribe.cpp)은 맥·리눅스·윈도우
전부 미리 만들어진 휠입니다. 다른 것은 번역 런타임(llama.cpp)의 출처와 모델
위치뿐입니다.

| | macOS / Linux | Windows |
|---|---|---|
| 전사 런타임 | `pip install transcribe-cpp` (휠, Metal / Vulkan 포함) | 같음 (win_amd64 휠, Vulkan 포함) |
| GPU 가속 | Metal (자동) / Vulkan | **Vulkan** (휠에 포함) |
| 번역 런타임 | PyPI의 llama-cpp-python (맥은 소스 빌드) | 만든 쪽 인덱스의 win_amd64 휠 |
| 준비물 | python3 (ffmpeg 권장) | python (ffmpeg 권장) |
| 모델 위치 | `~/.local/share/mimiwatch/models` | `%LOCALAPPDATA%\mimiwatch\models` |

## GPU 가속: Vulkan을 씁니다

`.\install.ps1`은 그래픽 카드와 `System32\vulkan-1.dll`이 있으면 Vulkan
경로를 고릅니다. **AMD·NVIDIA·Intel 모두 같은 경로입니다.**

별도로 깔 것이 없습니다. Vulkan *SDK*는 빌드할 때만 필요하고, 우리는
빌드하지 않습니다. Vulkan *런타임*(`vulkan-1.dll`)은 요즘 그래픽 드라이버에
함께 들어옵니다.

```powershell
.\install.ps1                    # 알아서 고릅니다 (NVIDIA면 cuda, 다른 GPU면 vulkan)
.\install.ps1 -Backend cpu       # GPU를 쓰지 않습니다
.\install.ps1 -Backend vulkan    # 자동 판단을 무시하고 강제합니다
.\install.ps1 -Backend cuda      # NVIDIA 전용 (아래 절)
```

설치가 끝나면 실제로 무엇이 잡혔는지 찍어 줍니다.

```
> 전사 런타임 (transcribe.cpp)
  [OK] 설치
  쓸 수 있는 백엔드: cpu, vulkan
```

`vulkan`이 없으면 그래픽 드라이버를 갱신하십시오. 전사는 CPU로도 돕니다 —
느릴 뿐입니다.

### 내장 그래픽이라면 CPU도 재 보십시오

`.\install.ps1 -Backend`는 **llama-cpp-python 휠을 무엇으로 깔지**만
정합니다. 실제로 어디서 돌릴지는 `backends.json`의 `device`가 정하고,
전사와 번역을 따로 고를 수 있습니다.

```json
{ "id": "tcpp-best",   "backend": "tcpp",  "device": "cpu" }
{ "id": "local-gemma", "backend": "gemma", "device": "cpu" }
```

내장 그래픽(780M 등)은 시스템 메모리를 CPU와 나눠 쓰고 대역폭도 좁습니다.
코어가 넉넉한 노트북이라면 CPU 쪽이 더 빠르거나, 최소한 전사와 번역이 같은
iGPU를 다투는 일을 피할 수 있습니다.

**기본 전사기가 버거우면 가벼운 쪽도 있습니다.**

```json
{ "id": "tcpp-lite", "label": "SenseVoice Small (가벼움 · CPU)",
  "backend": "tcpp", "model": "SenseVoiceSmall-Q8_0.gguf", "device": "cpu" }
{ "id": "tcpp-lite-en", "label": "Moonshine base (가벼움 · 영어 전용)",
  "backend": "tcpp", "model": "moonshine-base-Q8_0.gguf", "device": "cpu" }
```

`SenseVoice Small`(241MB)은 CPU에서 whisper보다 8배, **영어 방송이라면
`Moonshine base`(74MB)가 12배** 빠릅니다. Moonshine은 영어 품질이 기본과
사실상 같지만 다른 언어는 거부합니다.

`asr_backends`에 넣으면 화면의 「전사」 선택기에 나타납니다. 자세한 것은
README의 「CPU로 돌리기」를 보십시오.

## NVIDIA: CUDA 판은 릴리스에서 뺐습니다

NVIDIA 도 릴리스 묶음(`windows-x64`, Vulkan)을 쓰십시오. CUDA 판(`build.ps1 -Backend
cuda`)을 만들어 봤지만 릴리스에서는 빼기로 했습니다. llama-cpp-python 의 cu124 휠은
**RTX 50(Blackwell, sm_120) 커널을 담지 않아** 최신 카드에서는 열리지 않거나 PTX JIT 에
기대야 하고, 전사(transcribe.cpp)는 어차피 CUDA 휠이 없어 Vulkan 이라 얻는 것이 번역
속도뿐인데 묶음이 726MB 로 7배 커집니다. 그 거래는 맞지 않습니다.

직접 만들 수는 있습니다(`.\packaging\build.ps1 -Backend cuda`, 또는 `.\install.ps1
-Backend cuda`). 그때 지원되는 GPU 는 아래 표대로이고, 이것은 휠의 `ggml-cuda.dll`
fatbin 헤더를 열어 확인한 것입니다(llama-cpp-python 0.3.35 cu124).

| 세대 | 대표 제품 | 컴파일된 대상 | 지원 |
|---|---|---|---|
| Pascal | GeForce GTX 1050~1080 Ti, TITAN Xp | sm_60 · sm_61 | ✓ |
| Volta | TITAN V, Tesla V100 | sm_70 | ✓ |
| Turing | GTX 1650~1660 Ti, RTX 2060~2080 Ti | sm_75 | ✓ |
| Ampere | RTX 3050~3090 Ti, A100 | sm_86 · sm_80 | ✓ |
| Ada Lovelace | RTX 4050~4090 | sm_89 | ✓ |
| Hopper | H100 | sm_90 | ✓ |
| Blackwell | RTX 5050~5090 | 없음 (sm_90 PTX 를 드라이버가 JIT) | **미확인** -- 안 되면 Vulkan 판 |
| Maxwell 이하 | GTX 900 · 700 이전 | 없음 | ✗ Vulkan 판을 쓰십시오 |

**드라이버는 551.61 이상**(CUDA 12.4 런타임)이어야 합니다. 더 오래된 드라이버에서는
번역기가 CUDA 장치를 못 열어 실패합니다 -- 그때는 드라이버를 올리거나 Vulkan 판을
쓰십시오. CUDA 툴킷은 깔 필요가 없습니다: 필요한 런타임(cudart64_12, cublas64_12,
cublasLt64_12)은 묶음 안에 들어 있습니다(`nvidia-*-cu12` PyPI 패키지에서 꺼내 llama_cpp
옆에 둡니다).

## AMD: whisper.cpp-amd 는 왜 안 쓰는가

[lemonade-sdk/whisper.cpp-amd][wa]는 실재하고 잘 만들어진 프로젝트입니다.
ROCm·Vulkan·Ryzen AI NPU·CPU 빌드를 미리 만들어 배포하고, ROCm 라이브러리를
통째로 넣어 두어 따로 깔 것이 없습니다. **AMD에서 whisper를 돌린다면 좋은
선택입니다.**

다만 mimiwatch에는 그대로 끼워지지 않습니다. 세 가지가 걸립니다.

1. **다른 라이브러리입니다.** mimiwatch가 쓰는 것은 whisper.cpp가 아니라
   [transcribe.cpp][tc]입니다. 모델 파일도 다릅니다 — whisper.cpp는
   `ggml-*.bin`, transcribe.cpp는 `handy-computer`의 GGUF입니다.
2. **`whisper-cli.exe`만 있습니다.** 서버가 없어서, mimiwatch의 외부 전사
   경로(OpenAI 호환 `/v1/audio/transcriptions`)로도 닿을 수 없습니다.
3. **라이브에 쓸 수 없습니다.** CLI를 구간마다 부르면 매번 모델을 다시
   읽어야 합니다. 라이브 경로는 모델을 한 번 올려 두고 재사용하는
   파이썬 바인딩을 씁니다.

**AMD 가속이라는 요구 자체는 Vulkan으로 이미 채워집니다.** 같은 라이브러리,
같은 모델, 같은 바인딩이고, 라이브에서도 그대로 돕니다.

Ryzen AI **NPU**는 이야기가 다릅니다. transcribe.cpp에 NPU 백엔드가 없으므로
지금은 길이 없습니다. 쓰려면 whisper.cpp-amd를 감싸는 전사 어댑터를 새로
써야 하고, 그래도 라이브에는 못 씁니다(위 3번). 다시 볼 만해지는 조건은
transcribe.cpp가 NPU 백엔드를 갖거나, whisper.cpp-amd가 서버를 내놓는
것입니다.

[wa]: https://github.com/lemonade-sdk/whisper.cpp-amd
[tc]: https://github.com/handy-computer/transcribe.cpp

## 멤버십 전용 방송

쿠키 파일 경로를 환경변수로 주면 됩니다. 자세한 것과 쿠키를 안전하게
내보내는 절차는 README의 「멤버십 전용 방송」을 보십시오.

```powershell
$env:MIMIWATCH_YTDLP_COOKIES = "C:\Users\USERNAME\cookies.txt"
.\run.ps1
```

## 모델 위치 바꾸기

```powershell
.\install.ps1 -ModelDir 'D:\models'
.\run.ps1     -ModelDir 'D:\models'
```

**설치할 때와 실행할 때 같은 값을 주어야 합니다.** 안 그러면 서버가 모델을
찾지 못합니다.

## 걸리는 곳

| 증상 | 원인 | 해결 |
|---|---|---|
| `.\install.ps1` 이 실행되지 않음 | 실행 정책 | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `python`이 마이크로소프트 스토어를 엶 | 윈도우 기본 자리표시자 | `winget install --id Python.Python.3.12` 후 새 터미널 |
| `ffmpeg` 를 못 찾음 | 열려 있던 터미널의 PATH가 낡음 | 터미널을 새로 여십시오 |
| llama-cpp-python 설치 실패 | 파이썬 판에 맞는 휠이 없음 | `-Backend cpu` 로 다시, 그래도 안 되면 파이썬 3.12를 쓰십시오 |
| 백엔드에 `vulkan`이 없음 | 드라이버가 낡음 | 그래픽 드라이버 갱신. 없어도 CPU로 돕니다 |
| 내려받다 끊김 | — | 그냥 다시 실행하십시오. 받다 만 것은 `.part`로 남고 이어 받습니다. 화면의 「모델·도구」에서도 됩니다 |
| `NativeCommandError`로 중간에 멈춤 | 0.1의 결함 (이슈 #1) | 최신 판을 받으십시오. Windows PowerShell 5.1이 명령의 stderr 한 줄을 종료 오류로 바꾸던 문제입니다 |
| 전사가 시작되자마자 실패 | GPU에 모델을 못 올렸을 수 있습니다 | `bench/doctor.py`로 확인하고, `device=auto`만 실패하면 `backends.json`에 `"device": "cpu"`를 적으십시오 |
| 라이브 세션이 `OSError: [WinError 6] 핸들이 잘못되었습니다`로 실패 | 0.3.1의 결함 | 최신 판을 받으십시오. 표준 오류 핸들이 성치 않은 채로 뜬 프로세스(콘솔 없이 띄운 묶음, 작업 스케줄러·서비스)에서 ffmpeg을 세우지 못하던 문제입니다. 0.3.1을 그대로 쓰려면 콘솔 창에서 직접 띄우십시오 |
| 라이브에서 "오디오를 찾지 못했습니다" | **yt-dlp가 낡았습니다** | `.\install.ps1` 을 다시 돌리십시오. 가상환경 안의 yt-dlp가 최신으로 올라갑니다 |

## 무엇을 확인했고 무엇을 확인하지 못했는가

**이 스크립트는 아직 윈도우에서 실행해 본 적이 없습니다.** 다만 macOS의
PowerShell 7로 상당 부분을 실제로 돌려 보았습니다. 틀리기 쉬운 곳은 대개
플랫폼과 무관하기 때문입니다 — 종료 코드 판정, 여기-문자열 인자 전달,
실패했을 때 임시 파일을 치우는지 같은 것들입니다.

```sh
brew install powershell
.venv/bin/python bench/ps_test.py
```

`bench/ps_test.py`는 `install.ps1`을 **베끼지 않고 행 범위로 떼어 와서**
돌립니다. 베껴 두면 시험한 것과 배포하는 것이 갈라지기 때문입니다.

확인한 것:

- 구문과 PSScriptAnalyzer 정적 분석 (`install.ps1`, `run.ps1`)
- 모델 내려받기: 완성본 생성, `.part` 정리, 이미 있으면 건너뛰기,
  실패 시 `exit 1`
- 파이썬 탐색, 빈 배열 스플래팅
- 여기-문자열을 `-c`로 넘기는 확인 단계 (통과 경로와 실패 경로 양쪽)
- GPU·WMI·드라이브가 없을 때 멈추지 않고 CPU로 내려가는지
- 파이썬 코드에 POSIX 전용 요소가 없다는 것 (`fcntl`, `fork`, `/dev/null`)
- 필요한 휠이 `win_amd64`로 실재한다는 것 (`transcribe-cpp-native` 0.2.2에
  `ggml-vulkan.dll`이 들어 있음을 내려받아 확인)

이 시험이 실제로 잡아낸 버그 셋:

1. `& cmd | Select-Object -First 1` 뒤의 `$LASTEXITCODE`는 갱신되지
   않습니다(파이프라인이 일찍 끊깁니다). 새 셸에서는 이 변수가 비어 있어서
   **파이썬이 깔려 있어도 "없습니다"라고 말하고 멈췄습니다.**
2. `$env:SystemRoot`가 비면 `Join-Path`가 예외를 냅니다. 하필 "GPU를 못
   알아봤으니 CPU로 가자"는 자리에서 설치가 통째로 멈춥니다.
3. 없는 드라이브 앞에서는 `Test-Path`도 같은 문제를 냅니다.

**확인하지 못한 것**: 윈도우에서만 되는 것들입니다 — `winget` 안내,
`Get-CimInstance`의 GPU 목록, Vulkan 로더 판정, 휠이 실제로 설치되고
`transcribe_cpp.backends()`에 `vulkan`이 뜨는지, MSVC 없이 끝까지 가는지.

### 이 하네스가 놓쳤던 것

**Windows PowerShell 5.1은 네이티브 명령의 stderr 한 줄을 종료 오류로
바꿉니다**(`NativeCommandError`). `$ErrorActionPreference='Stop'`일 때
그렇고, `2>$null`로는 막히지 않습니다. pwsh 7에는 그 동작이 없어서 macOS
시험에서는 드러나지 않았고, 실제 윈도우 사용자가 이슈 #1로 알려 주었습니다.

이 스크립트에는 stderr가 정상인 자리가 여럿입니다 — 아직 깔지 않은 패키지를
`import` 해 보는 확인, `curl`의 진행 막대, `pip`의 알림. 그래서 네이티브
호출을 전부 `Invoke-Native`(출력을 그대로 흘려보냄)와 `Get-Native`(붙잡아
돌려줌) 둘 중 하나로 모았습니다. 진행 막대가 살아 있어야 하는 자리는
`Start-Process -NoNewWindow`로 부릅니다 — 콘솔 핸들을 물려주므로 stderr가
PowerShell의 오류 스트림을 아예 거치지 않습니다.

하네스의 `[9]`가 이 회귀를 지킵니다. pwsh 7에서도 확인할 수 있는 형태로,
헬퍼가 stderr를 붙잡고 종료 코드만 돌려주는지 봅니다.

## 막혔을 때: doctor

무엇이 되고 무엇이 안 되는지 한 번에 찍어 줍니다. 명령을 여럿 주고받는
것보다 이 하나를 돌려 붙여 넣는 편이 빠릅니다.

```powershell
.\.venv\Scripts\python.exe bench\doctor.py
.\.venv\Scripts\python.exe bench\doctor.py "https://www.youtube.com/live/..."
```

준비물, 쓸 수 있는 백엔드, **전사 모델을 auto와 cpu 각각으로 올려 보기**,
번역 백엔드 설정, 그리고 주소를 주면 그 해석까지 봅니다. 번역 모델(5GB)은
건드리지 않으므로 몇 초면 끝납니다.

`device=auto`만 실패하고 `device=cpu`는 되면, GPU에 모델을 못 올린 것입니다.
`backends.json`에 `"device": "cpu"`를 적으면 됩니다(README의 「CPU로 돌리기」).

처음 돌려 보시고 걸리는 곳이 있으면 알려 주십시오.
