# 윈도우

```powershell
git clone https://github.com/chisacam/mimiwatch.git
cd mimiwatch
winget install --id Python.Python.3.12
winget install --id Gyan.FFmpeg
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

**빌드 도구가 필요 없습니다.** `install.sh`는 transcribe.cpp를 받아
CMake로 빌드하지만, 윈도우에는 미리 만들어진 휠이 있습니다. 컴파일러도,
CMake도, Vulkan SDK도, git 클론도 없습니다.

| | macOS / Linux | Windows |
|---|---|---|
| 전사 런타임 | 소스를 받아 CMake 빌드 | `pip install transcribe-cpp` (win_amd64 휠) |
| GPU 가속 | Metal (자동) / CUDA / ROCm | **Vulkan** (휠에 포함) |
| 번역 런타임 | PyPI의 llama-cpp-python | 만든 쪽 인덱스의 win_amd64 휠 |
| 준비물 | git, cmake, ffmpeg | ffmpeg |
| 모델 위치 | `~/.local/share/mimiwatch/models` | `%LOCALAPPDATA%\mimiwatch\models` |

## GPU 가속: Vulkan을 씁니다

`.\install.ps1`은 그래픽 카드와 `System32\vulkan-1.dll`이 있으면 Vulkan
경로를 고릅니다. **AMD·NVIDIA·Intel 모두 같은 경로입니다.**

별도로 깔 것이 없습니다. Vulkan *SDK*는 빌드할 때만 필요하고, 우리는
빌드하지 않습니다. Vulkan *런타임*(`vulkan-1.dll`)은 요즘 그래픽 드라이버에
함께 들어옵니다.

```powershell
.\install.ps1                    # 알아서 고릅니다
.\install.ps1 -Backend cpu       # GPU를 쓰지 않습니다
.\install.ps1 -Backend vulkan    # 자동 판단을 무시하고 강제합니다
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
iGPU를 다투는 일을 피할 수 있습니다. 자세한 것은 README의 「CPU로 돌리기」를
보십시오.

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
| 내려받다 끊김 | — | 그냥 다시 실행하십시오. 받다 만 것은 `.part`로 남고 완성본만 인정합니다 |
| `NativeCommandError`로 중간에 멈춤 | 0.1의 결함 (이슈 #1) | 최신 판을 받으십시오. Windows PowerShell 5.1이 명령의 stderr 한 줄을 종료 오류로 바꾸던 문제입니다 |
| 전사가 시작되자마자 실패 | GPU에 모델을 못 올렸을 수 있습니다 | `bench/doctor.py`로 확인하고, `device=auto`만 실패하면 `backends.json`에 `"device": "cpu"`를 적으십시오 |
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
