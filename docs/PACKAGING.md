# 묶음으로 배포하기

저장소를 받아 `./install.sh`를 돌리는 대신, **실행 파일 하나짜리 묶음**을 받아
두 번 눌러 띄우는 길입니다. 맥(Apple Silicon)과 윈도우(x64)를 만듭니다.

```
mimiwatch.app            macOS -- Finder에서 두 번 누르면 브라우저에 화면이 열립니다
mimiwatch\mimiwatch.exe  Windows -- 두 번 누르면 콘솔 창이 하나 뜨고 브라우저가 열립니다
```

## 묶음에 무엇이 들고 무엇이 들지 않는가

**듭니다**: 파이썬, 서버와 화면, 네 런타임(transcribe.cpp·llama.cpp·sherpa-onnx·
CTranslate2), yt-dlp, CA 인증서 묶음. 맥은 Metal, 윈도우는 Vulkan(+CPU)이 들어
있습니다. 약 110MB(맥), zip으로 45MB 남짓입니다.

**들지 않습니다**: 모델(6.3GB)과 ffmpeg. 모델은 어차피 프로그램 밖
(`~/.local/share/mimiwatch/models`, 윈도우 `%LOCALAPPDATA%\mimiwatch\models`)에
두는 것이 원칙이라, 판을 바꿔도 다시 받지 않습니다. 저장소에서 돌리던 사람의
모델도 그대로 씁니다. **첫 실행 때 화면 위쪽에 「전사에 필요한 것이 아직
없습니다」 띠와 함께 「초기 설정」이 떠서 전사·번역 엔진을 고르게 하고, 그 조합에
필요한 모델만 받습니다.** 기본은 가벼운 CPU 엔진(SenseVoice Small + M2M-100,
약 730MB)입니다. 더 고르려면
「관리 › ⚙ 엔진 관리 › 모델·도구」입니다.

ffmpeg는 시스템에 있으면 그것을 쓰고(홈브루 자리도 봅니다), 없으면 같은
화면에서 정적 빌드 한 파일을 받습니다. 묶음에 넣지 않는 것은 GPL 배포 문제를
피하기 위해서입니다 -- 우리가 배포하는 것이 아니라 사용자가 받는 것입니다.

## 파일이 어디에 놓이는가

저장소에서 돌 때와 묶음으로 돌 때가 다릅니다. 묶음 안은 읽기 전용이라 설정과
저장소가 사용자 영역으로 나옵니다. 규칙은 `paths.py` 한 곳에 있습니다.

| | 저장소에서 (`./run.sh`) | 묶음으로 |
|---|---|---|
| 모델 | `~/.local/share/mimiwatch/models` | 같음 |
| 도구(ffmpeg·yt-dlp) | `~/.local/share/mimiwatch/tools` | 같음 |
| 설정 `backends.json` | 저장소 안 | `~/.local/share/mimiwatch/backends.json` |
| 자막 DB·오디오 `data/` | 저장소 안 | `~/.local/share/mimiwatch/data` |
| 로그 | 터미널 | `~/.local/share/mimiwatch/mimiwatch.log` (맥 .app은 창이 없으므로) |

윈도우는 `~/.local/share/mimiwatch` 자리에 `%LOCALAPPDATA%\mimiwatch`입니다.
`MIMIWATCH_HOME`으로 뿌리를 통째로, `MIMIWATCH_MODEL_DIR`·`MIMIWATCH_DATA_DIR`·
`MIMIWATCH_CONFIG`로 하나씩 바꿀 수 있습니다.

## yt-dlp는 낡습니다 -- 그래서 독립 실행 파일을 권합니다

가상환경에 yt-dlp를 두어 "설치 스크립트를 다시 돌리면 판올림"을 성립시켰던
것이(README 「yt-dlp는 가상환경 안에 있습니다」) 묶음에서는 되돌아옵니다. 묶음
안의 yt-dlp는 판이 박혀 있어 몇 달 뒤 유튜브가 추출 경로를 바꾸면 「오디오를
찾지 못했습니다」가 됩니다.

그래서 「모델·도구」에 **yt-dlp 독립 실행 파일**이 있습니다. yt-dlp가 배포하는
그 파일은 `-U`로 스스로 판올림하고, 도구 디렉터리에 있으면 묶음 안의 것보다
먼저 쓰입니다. 라이브가 "포맷을 하나도 받지 못했습니다"라고 하면 그것을
받거나(있으면 다시 받아 최신으로) 하면 됩니다. 묶음을 새로 만들 필요가 없습니다.

묶음 안의 yt-dlp는 `mimiwatch --ytdlp …`로 부릅니다 -- 묶음에는 `python -m
yt_dlp`를 부를 파이썬이 없어 서버가 자기 자신을 다시 띄우는 것입니다. 자식
프로세스로 두는 이유는 그대로입니다(시간 상한, 죽일 수 있어야 함).

## 만들기

```sh
packaging/build.sh                 # 맥·리눅스 → dist/mimiwatch-<판>-macos-arm64.zip
.\packaging\build.ps1              # 윈도우   → dist\mimiwatch-<판>-windows-x64.zip
```

빌드 전용 가상환경(`.venv-build`)을 따로 만듭니다. 개발용 `.venv`에는 pytest·ruff
같은 것이 섞여 있고, 묶음에는 실행에 필요한 것만 들어가야 합니다.

- **맥**: `llama-cpp-python`은 PyPI에 맥 휠이 없어 소스에서 빌드됩니다(cmake
  필요, `brew install cmake`). Apple Silicon에서는 Metal이 기본으로 켜집니다.
  처음 한 번 5~10분, 그 뒤로는 pip이 만든 휠을 기억해 1분 남짓입니다.
- **윈도우**: 아무것도 빌드하지 않습니다. `llama-cpp-python`은 만든 쪽 인덱스의
  Vulkan 휠(GPU가 없으면 CPU로 내려갑니다), `transcribe-cpp`는 PyPI 휠입니다.
- **깃허브 액션**(`.github/workflows/build.yml`): `v*` 태그를 밀면 두 플랫폼을
  만들어 릴리스에 붙입니다. 수동으로도(`workflow_dispatch`) 돌릴 수 있습니다.

명세는 `packaging/mimiwatch.spec`입니다. 네 런타임은 `collect_all`로 패키지
디렉터리 구조를 그대로 옮기므로 `@loader_path`로 서로를 찾는 규칙이 묶음
안에서도 성립합니다. **transcribe_cpp는 네이티브 제공자를 패키지 메타데이터의
entry point로 찾으므로** dist-info를 같이 넣습니다(`copy_metadata`) -- 이것이
빠지면 시작조차 못 합니다.

## 처음 열 때 걸리는 것

**맥**: 서명이 없어 처음 열면 「확인되지 않은 개발자」로 막힙니다. 두 번 누른
뒤 시스템 설정 › 개인정보 보호 및 보안 아래쪽의 「그래도 열기」, 또는 터미널에서:

```sh
xattr -dr com.apple.quarantine mimiwatch.app
```

**윈도우**: SmartScreen이 「알 수 없는 게시자」로 막습니다. 「추가 정보 › 실행」.

두 경고 다 서명 인증서(연 99달러 / 코드 서명 인증서)가 없어서 뜨는 것입니다.
1인 취미 프로젝트라 사지 않았습니다.

## 진단

```sh
mimiwatch --doctor                 # 준비물·백엔드·모델 적재를 한 번에 찍습니다
mimiwatch --doctor "https://www.youtube.com/live/..."
mimiwatch --port 8951 --no-browser
```

`bench/doctor.py`를 묶음에 넣어 둔 것입니다. 무엇이 안 되면 그 출력을 붙여
주시면 됩니다.

## 확인한 것과 확인하지 못한 것 (2026-08-29)

확인: 맥(M5 Pro, macOS 26)에서 묶음을 만들어 `--doctor`로 Metal·CPU 두 장치에
whisper를 올렸고, 묶음 안의 yt-dlp로 유튜브 주소를 풀었고, 설정·저장소·로그가
사용자 영역에 생기는 것을 보았습니다. 모델·도구 내려받기는 깃허브 릴리스·
허깅페이스·gzip 정적 ffmpeg·yt-dlp 독립 실행 파일 네 종류를 실제로 받아
실행했습니다.

확인하지 못한 것: **윈도우 빌드는 아직 윈도우에서 돌려 본 적이 없습니다.**
`build.ps1`은 `install.ps1`과 같은 헬퍼 규칙을 따르고 액션 러너에서 돌아갈
것을 전제로 썼습니다. 첫 태그 빌드에서 확인하십시오. Finder에서 .app을 두 번
눌러 띄우는 경로(PATH가 짧은 환경에서 홈브루 ffmpeg을 찾는지)도 터미널이
아닌 실제 클릭으로는 보지 않았습니다.
