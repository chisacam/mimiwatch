# 묶음으로 배포하기

*[English](PACKAGING.md)*

저장소를 받아 `./install.sh`를 돌리는 대신, **실행 파일 하나짜리 묶음**을 받아
두 번 눌러 띄우는 길입니다. 맥(Apple Silicon)과 윈도우(x64)를 만듭니다.

```
mimiwatch.app            macOS -- Finder에서 두 번 누르면 브라우저에 화면이 열립니다
mimiwatch\mimiwatch.exe  Windows -- 두 번 누르면 콘솔 창이 하나 뜨고 브라우저가 열립니다
```

윈도우 묶음은 Vulkan 하나입니다(NVIDIA 포함). CUDA 판은 RTX 50 을 담지 못해 릴리스에서
뺐습니다 -- 사정과 직접 만드는 법은 [WINDOWS.ko.md](WINDOWS.ko.md)의 NVIDIA 절에 있습니다.

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
안의 yt-dlp는 판이 박혀 있어 몇 달 뒤 유튜브가 추출 경로를 바꾸면 "yt-dlp could not
find any audio at this URL." 가 됩니다.

그래서 「모델·도구」에 **yt-dlp 독립 실행 파일**이 있습니다. yt-dlp가 배포하는
그 파일은 `-U`로 스스로 판올림하고, 도구 디렉터리에 있으면 묶음 안의 것보다
먼저 쓰입니다. 라이브가 "포맷을 하나도 받지 못했습니다"라고 하면 그것을
받거나(있으면 다시 받아 최신으로) 하면 됩니다. 묶음을 새로 만들 필요가 없습니다.

**유튜브는 JS 런타임(deno)도 요구합니다**(2025.11~). 녹화본·멤버십 방송에 필요하고 공개
라이브 HLS 는 없어도 됩니다. 「모델·도구」에서 받으면 서버가 `--js-runtimes deno:<경로>`로
직접 알려 주므로 Finder 에서 띄운 .app(PATH 짧음)에서도 됩니다. 풀이 스크립트(yt-dlp-ejs)는
묶음에 들어 있습니다.

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
  `-Backend cuda`(릴리스에는 안 넣음)는 cu124 휠을 받고, 휠에 없는 런타임 DLL(cudart·
  cuBLAS)을 `nvidia-*-cu12` 패키지에서 꺼내 `llama_cpp\lib`에 넣습니다 -- 726MB 남짓입니다.
- **깃허브 액션**(`.github/workflows/build.yml`): `v*` 태그를 밀면 두 플랫폼을
  만들어 릴리스에 붙입니다. 수동으로도(`workflow_dispatch`) 돌릴 수 있습니다.

명세는 `packaging/mimiwatch.spec`입니다. 네 런타임은 `collect_all`로 패키지
디렉터리 구조를 그대로 옮기므로 `@loader_path`로 서로를 찾는 규칙이 묶음
안에서도 성립합니다. **transcribe_cpp는 네이티브 제공자를 패키지 메타데이터의
entry point로 찾으므로** dist-info를 같이 넣습니다(`copy_metadata`) -- 이것이
빠지면 시작조차 못 합니다.

## 처음 열 때 걸리는 것

**맥 — 왜 막히나.** 앱은 ad-hoc 서명이 되어 있고 그 서명은 유효합니다(`codesign
--verify --deep --strict` 통과). 다만 Apple 의 **공증(notarization)** 이 없어
 Gatekeeper 가 격리(quarantine) 표시된 파일을 막습니다. 공증은 유료 Developer
ID($99/년)를 요구하므로 이 배포에는 없습니다. **한 번만 격리를 벗기면 그 뒤로는
경고 없이 열립니다** — 유효하게 ad-hoc 서명된 앱은 격리만 없으면 Gatekeeper 가
건드리지 않기 때문입니다.

**매번 뜨는 이유.** Downloads 같은 곳에서 바로 실행하면 macOS 가 앱을 무작위
읽기 전용 경로로 옮겨 실행합니다(App Translocation). 그래서 「그래도 열기」 승인이
그 경로에 묶여 다음 실행에 남지 않습니다. **`/Applications`(또는 아무 폴더)로 한 번
옮기면** translocation 이 멈추고 승인이 남습니다.

권하는 순서:

1. **터미널로 받으면 처음부터 격리가 없어 바로 열립니다.** `curl` 로 받은 파일에는
   격리 속성이 붙지 않습니다.
   ```sh
   curl -L -o mimiwatch.zip https://github.com/chisacam/mimiwatch/releases/latest/download/mimiwatch-<판>-macos-arm64.zip
   ditto -x -k mimiwatch.zip ~/Applications/    # 심볼릭 링크를 지켜 풀고, 옮겨 둡니다
   open ~/Applications/mimiwatch.app
   ```
2. 브라우저로 받았으면: **먼저 응용 프로그램 폴더로 옮기고**, 두 번 눌러 차단 창을
   닫은 뒤 **1시간 안에** 시스템 설정 › 개인정보 보호 및 보안 맨 아래 「그래도
   열기」. 옮겨 두었으므로 이후에는 다시 묻지 않습니다.
3. 한 줄로 끝내려면 — 옮긴 뒤 격리를 벗깁니다. 그 뒤로는 경고가 없습니다.
   ```sh
   xattr -dr com.apple.quarantine ~/Applications/mimiwatch.app
   ```

「손상되어 열 수 없습니다」가 뜨면 서명이 깨진 것입니다 — zip을 **Finder(Archive
Utility)나 `ditto`로** 풀어야 합니다. 다른 압축 도구는 묶음 안의 심볼릭 링크를 실제
파일로 풀어 서명 해시가 어긋납니다(PyInstaller 6의 .app은 Frameworks와 Resources를
링크로 잇습니다).

**공증까지 하려면** Apple Developer Program($99/년)에 가입해 Developer ID 인증서를
받고, 빌드 때 `MIMIWATCH_CODESIGN="Developer ID Application: 이름 (팀ID)"` 로 서명한 뒤
`xcrun notarytool submit` 으로 공증·스테이플하면 첫 실행 경고까지 사라집니다. 지금
`packaging/build.sh` 는 그 환경변수가 있으면 ad-hoc 대신 그 인증서로 서명합니다.

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

## 판올림 (자동 업데이트)

묶음은 자기 판을 압니다 -- 빌드가 `MIMIWATCH_VERSION`(태그)을 `_version.txt`
로 구워 넣습니다(mimiwatch.spec). 서버는 하루 한 번 깃허브 releases/latest 를
확인해 태그가 더 새로우면 화면에 알리고, 「받기 → 다시 시작하며 적용」이면:

1. 이 플랫폼의 자산(`mimiwatch-<판>-macos-arm64.zip` / `-windows-x64.zip`)을
   사용자 영역 `updates/` 에 받습니다. `.part` 이어 받기는 모델과 같습니다.
   윈도우의 `-cuda`/`-cpu` 변형은 자동 판올림 대상이 아닙니다.
2. 교체 스크립트(`updates/apply.sh`·`apply.ps1`)를 띄우고 서버가 스스로
   꺼집니다. 스크립트는 프로세스가 끝나기를 기다렸다가 옛 묶음을 `.old` 로
   물리고 새 것을 그 자리에 놓은 뒤 **같은 인자로** 다시 띄웁니다. 맥은
   압축을 `ditto` 로 풀어(.app 의 심볼릭 링크·서명 보존) `xattr -cr` 까지
   해 주므로 게이트키퍼 안내를 다시 거치지 않습니다.
3. 실패하면 옛 묶음을 되돌립니다. 무슨 일이 있었는지는 `updates/apply.log`.

모델·설정·자막 DB 는 묶음 밖(사용자 영역)이라 판을 갈아도 그대로입니다.
확인을 끄려면 `backends.json` 의 `"update_check": false` 또는
`MIMIWATCH_NO_UPDATE_CHECK=1`. 저장소에서 돌 때는 알림만 하고 적용은
거절합니다(`git pull` 안내).
