# mimiwatch

*[English](README.md)*

YouTube 영상과 라이브 방송을 **자신의 언어로 이해하기 위한 로컬 도구**입니다.
원본 영상 위에 전사 자막과 번역 자막을 얹어 보여 줍니다.

모델은 전부 이 기계에서 돕니다. 외부로 나가는 것은 영상 오디오를 받아 오는
요청과, 하루 한 번 깃허브 릴리스에서 새 판이 있는지 묻는 조회뿐입니다
(끄려면 [사용 안내 › 판올림](docs/GUIDE.ko.md#판올림) 참고).

## 무엇을 하는가

**녹화본** — 주소를 넣으면 오디오를 받아 전사하고 번역합니다. 각 발화에
미디어 타임스탬프가 붙으므로 플레이어의 재생 위치와 **자동으로 정렬**됩니다.

**라이브** — 진행 중인 방송을 받아 적습니다. 짧게 끊은 확정 자막이 먼저
나가고, 발화 한 무리가 끝나면 합쳐서 다시 해독한 정제본이 그 자리를
대신합니다.

**로컬 파일** — 이 기계의 영상·음성 파일(mp3, wav, mp4, mkv 등 ffmpeg가
여는 것)도 같은 길로 전사·번역합니다.

**멀티뷰** — 라이브 넷까지 나란히 놓고, 초점 타일의 소리와 자막만 냅니다.

**브라우저 확장** — 임베드가 막힌 방송은 유튜브 페이지 자체에 자막을 얹습니다.

**언어가 같으면 아무것도 하지 않습니다.** 한국어 사용자가 한국어 영상을 볼
때는 번역도 자막도 켜지 않습니다.

## 요구 사항

- macOS(Apple Silicon·Intel), Linux, 또는 Windows 10 1803 이상
- 디스크 약 7GB (모델 6.3GB — 가벼운 기본 조합만 쓰면 약 730MB)
- 저장소에서 돌리려면 Python 3.10 이상. 묶음으로 받으면 파이썬도 필요 없습니다

**아무것도 빌드하지 않습니다.** 전사 런타임(transcribe.cpp)은 맥·리눅스·
윈도우 모두 미리 만들어진 휠을 쓰고, 맥 휠에는 Metal이 들어 있습니다.

`ffmpeg`는 권장이지 필수는 아닙니다. 시스템에 있으면 그것을 쓰고, 없으면
화면의 「엔진 관리 › 모델·도구」에서 정적 빌드 한 파일을 받을 수 있습니다.

```sh
brew install ffmpeg        # 윈도우: winget install --id Gyan.FFmpeg
```

`yt-dlp`는 준비물이 아닙니다. 설치 스크립트가 가상환경 안에 최신으로 넣습니다.

## 두 가지 길

**묶음으로.** 릴리스에서 `mimiwatch-*-macos-arm64.zip` 또는
`mimiwatch-*-windows-x64.zip`을 받아 풀고 두 번 누릅니다. 브라우저에 화면이
열리고, 첫 실행이면 「초기 설정」이 떠서 엔진을 고르고 그에 맞는 모델을
받습니다. 처음 열 때 걸리는 게이트키퍼·SmartScreen 안내와 파일이 어디에
놓이는지는 [docs/PACKAGING.ko.md](docs/PACKAGING.ko.md)에 있습니다.

**저장소에서.** 아래처럼 설치합니다. 코드를 고치거나 실측 스크립트를
돌리려면 이쪽입니다.

## 설치

```sh
git clone https://github.com/chisacam/mimiwatch.git
cd mimiwatch
./install.sh               # 윈도우: .\install.ps1
```

스크립트는 가상환경을 만들고 의존성을 넣고, 기본 조합의 모델을 받고,
`backends.example.json`을 복사해 `backends.json`을 만듭니다. **이미 끝난
단계는 건너뛰므로** 내려받기가 끊기면 그냥 다시 실행하십시오.

**기본은 가벼운 CPU 엔진입니다** (전사 SenseVoice Small, 번역 M2M-100).
품질은 무거운 쪽(whisper-large-v3-turbo, Gemma 4)이 훨씬 낫습니다 — GPU가
있고 메모리가 넉넉하면 첫 실행의 「초기 설정」이나 「관리 › ⚙ 엔진 관리」에서
올리십시오. 설치할 때부터 Gemma까지 받아 두려면:

```sh
WITH_GEMMA=1 ./install.sh        # 윈도우: .\install.ps1 -WithGemma
```

모델의 기본 위치는 `~/.local/share/mimiwatch/models`, 윈도우는
`%LOCALAPPDATA%\mimiwatch\models`입니다. `MIMIWATCH_MODEL_DIR`로 바꿀 수
있습니다. 어떤 모델이 있고 무엇이 필요한지는 화면의 「모델·도구」와
`modelhub.py list`가 같은 목록으로 보여 줍니다.

## 실행

```sh
./run.sh            # 윈도우: .\run.ps1
```

http://localhost:8900 을 엽니다. 다른 포트는 `PORT=8951 ./run.sh`
(윈도우는 `.\run.ps1 -Port 8951`). 설치할 때 모델 위치를 바꿨다면 실행할
때도 같은 환경변수를 주십시오.

끌 때는 위쪽 막대 오른쪽 끝의 **「⏻ 종료」**를 누르십시오. 받는 중인 방송을
먼저 제대로 닫고 서버를 멈춥니다. 터미널의 `Ctrl-C`도 같은 경로를 탑니다.

## 쓰는 법

화면은 세 칸입니다. **왼쪽은 영상 목록**, 가운데는 플레이어, **오른쪽은
자막 내역**입니다. 양옆은 각각 접힙니다 — 목록은 `V`, 자막 내역은 `S`.

1. **왼쪽 「＋ 추가」**에 YouTube 주소, Twitch 채널 주소, m3u8 주소, 또는
   이 기계의 파일 경로를 넣습니다. 라이브인지 녹화본인지는 서버가 판단합니다.
2. **원본 언어를 지정하십시오.** 비워 두면 모델이 스스로 판별하는데, 판별이
   흔들리면 문장 하나가 통째로 다른 언어로 나옵니다.
3. **전사·번역 엔진과 장르**를 함께 고르고 「시작」을 누릅니다. 낮은 사양이라면
   가벼운 엔진을 고르십시오. 위쪽 「관리」에서 진행 중에도 바꿀 수 있습니다.
4. 자막이 어긋나면 오른쪽 자막 내역의 **✎ 편집** 모드로 원문·번역·시각을
   고치고, **⟳ 번역** 모드로 대목을 골라 다시 번역합니다. **⤓ 내보내기**로
   SRT·WebVTT·텍스트·JSON 을 받습니다.

그 밖의 모든 것 — 라이브 선택지, 멀티뷰, 지난 방송 이어받기, 엔진 교체와
CPU 설정, 멤버십 전용 방송, 브라우저 확장, 판올림, 문제가 생겼을 때 — 는
**[docs/GUIDE.ko.md](docs/GUIDE.ko.md)**에 있습니다.

## 문서

- [`docs/GUIDE.ko.md`](docs/GUIDE.ko.md) — 상세 사용 안내
- [`docs/PACKAGING.ko.md`](docs/PACKAGING.ko.md) — 묶음 배포와 판올림
- [`docs/WINDOWS.ko.md`](docs/WINDOWS.ko.md) — 윈도우에서 다른 점
- [`docs/REQUIREMENTS.ko.md`](docs/REQUIREMENTS.ko.md) — 요구사항과 설계 판단
- [`measurements/RESULTS.ko.md`](measurements/RESULTS.ko.md) — 모델 선정과 성능 실측
- [`AGENTS.md`](AGENTS.md) — 코드를 고치는 사람과 에이전트를 위한 규칙

## 출처

전사 파이프라인의 구조(VAD 분할, 선행 오디오, 2패스 정제)는
[hayamimi](https://github.com/oboroge0/hayamimi)(MIT, oboroge0)에서
가져왔습니다. 지금은 저장소 의존 없이 필요한 부분만 `stream.py`와
`speaker_id.py`에 옮겨 두었습니다.

m3u8 스트림의 화면 재생은 [hls.js](https://github.com/video-dev/hls.js)(Apache-2.0,
video-dev)를 씁니다. `web/vendor/hls.min.js`에 그대로 묶여 있고 라이선스는 그 옆
`hls.LICENSE.txt`입니다.

## 라이선스

코드와 프로그램은 [PolyForm Noncommercial 1.0.0](LICENSE)입니다. **상업적 목적이
아니라면 누구나** 쓰고 고치고 나눌 수 있습니다 — 개인 사용, 연구, 교육, 비영리
단체와 공공기관의 사용이 여기 들어갑니다. 상업적으로 쓰려면 따로 허락을 받으십시오.

참고한 hayamimi(MIT)에서 가져온 라이브 전사 루프의 구조에는 그쪽 고지가
`LICENSE` 안에 그대로 남아 있습니다. 모델과 ffmpeg은 저장소에 담기지 않고
사용자가 직접 내려받으며 각자의 라이선스(예: Gemma 이용 약관)를 따릅니다.
