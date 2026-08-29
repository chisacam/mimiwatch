"""이 프로그램이 파일을 어디에 두고 어디서 찾는지를 한 곳에 모읍니다.

두 가지 모양으로 돕니다.

  - **저장소에서** (`./run.sh`): 화면(`web/`)과 예시 설정은 저장소 안에 있고,
    설정(`backends.json`)과 저장소(`data/`)도 저장소 안에 둡니다. 예전 그대로입니다.
  - **묶음으로** (PyInstaller가 만든 실행 파일): 화면과 예시 설정은 묶음 안에
    들어 있고(`sys._MEIPASS`), 묶음 안은 **읽기 전용**이므로 설정과 저장소는
    사용자 영역으로 나옵니다 -- 모델이 이미 있는 그 자리입니다.

모델은 어느 쪽이든 저장소 밖(`~/.local/share/mimiwatch/models`, 윈도우는
`%LOCALAPPDATA%\\mimiwatch\\models`)에 둡니다. 저장소를 지워도 6GB를 다시 받지
않고, 묶음을 새 판으로 바꿔도 그대로입니다.

환경변수로 하나씩 바꿀 수 있습니다.

  MIMIWATCH_HOME        사용자 영역의 뿌리 (모델·도구·묶음일 때의 설정과 저장소)
  MIMIWATCH_MODEL_DIR   모델만 다른 곳에
  MIMIWATCH_DATA_DIR    저장소(자막 DB·내려받은 오디오)만 다른 곳에
  MIMIWATCH_CONFIG      설정 파일 하나만 다른 곳에

이 모듈은 프로젝트 안의 다른 모듈을 가져오지 않습니다. 누구나 가져올 수 있어야
하기 때문입니다 -- `config`·`store`·`stream`이 전부 여기에 기댑니다.
"""
from __future__ import annotations

import os
import shutil
import sys


def frozen() -> bool:
    """PyInstaller 묶음으로 돌고 있는가."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


# 묶음 안에 들어 있는 것들(화면, 예시 설정)의 자리. 저장소에서 돌 때는 저장소.
BASE = sys._MEIPASS if frozen() else os.path.dirname(os.path.abspath(__file__))  # type: ignore[attr-defined]


def home() -> str:
    """사용자 영역의 뿌리. 모델은 늘 이 아래에, 묶음일 때는 설정과 저장소도."""
    env = os.environ.get("MIMIWATCH_HOME")
    if env:
        return env
    if sys.platform == "win32":
        # `~/.local/share`도 동작하기는 하지만 윈도우에서 프로그램 데이터를
        # 찾는 곳이 아닙니다. 수 GB를 거기 두면 사용자가 찾지 못합니다.
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "mimiwatch")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "mimiwatch")


def model_dir() -> str:
    return os.environ.get("MIMIWATCH_MODEL_DIR") or os.path.join(home(), "models")


def data_dir() -> str:
    """자막 DB와 내려받은 오디오. 저장소에서 돌 때는 예전처럼 `<저장소>/data`."""
    env = os.environ.get("MIMIWATCH_DATA_DIR")
    if env:
        return env
    return os.path.join(home(), "data") if frozen() else os.path.join(BASE, "data")


def config_path() -> str:
    env = os.environ.get("MIMIWATCH_CONFIG")
    if env:
        return env
    if frozen():
        return os.path.join(home(), "backends.json")
    return os.path.join(BASE, "backends.json")


def tools_dir() -> str:
    """내려받은 실행 도구(ffmpeg, yt-dlp). 모델과 같은 규칙으로 저장소 밖에 둡니다."""
    return os.path.join(home(), "tools")


def tool(name: str) -> str | None:
    """도구 디렉터리에 받아 둔 실행 파일. 없으면 None."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    path = os.path.join(tools_dir(), exe)
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


# Finder나 시작 메뉴에서 띄운 묶음은 PATH가 짧습니다(맥은 /usr/bin:/bin 정도).
# 터미널에서는 잘 찾던 홈브루 ffmpeg을 그 자리에서는 못 찾으므로 흔한 자리를
# 함께 봅니다.
_EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"] \
    if sys.platform != "win32" else []


def which(name: str) -> str | None:
    """PATH에 있는 실행 파일, 없으면 위의 흔한 자리, 그래도 없으면 도구 디렉터리."""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_PATH:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return tool(name)


def log_path() -> str:
    """묶음이 창 없이 돌 때 표준 출력을 받아 두는 파일."""
    return os.path.join(home(), "mimiwatch.log")
