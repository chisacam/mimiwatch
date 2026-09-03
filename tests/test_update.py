"""판올림: 판 비교, 플랫폼 자산 고르기, 확인 캐시, 저장소 실행에서의 적용 거절.

네트워크에 나가지 않습니다 -- 깃허브 조회(`_get_json`)는 가짜로 바꿉니다.
"""
import platform as _platform

import pytest

import update


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """상태와 사용자 영역(updates/ 캐시)을 시험마다 새로."""
    monkeypatch.setenv("MIMIWATCH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "_state",
                        {"state": "idle", "error": None, "progress": None,
                         "latest": None, "available": False, "checked": 0.0, "file": None})


def test_parse_version():
    assert update.parse_version("v0.3.2") == (0, 3, 2)
    assert update.parse_version("0.10.0") == (0, 10, 0)
    assert update.parse_version("v0.3.2-5-gabc123") == (0, 3, 2)
    assert update.parse_version("gabc123") == ()
    assert update.parse_version("") == ()


def test_is_newer():
    assert update.is_newer("v0.4.0", "v0.3.2")
    assert update.is_newer("v0.10.0", "v0.9.9")          # 문자열 비교가 아니라 숫자 비교
    assert not update.is_newer("v0.3.2", "v0.4.0")
    # 태그보다 앞선 작업본(0.3.2-5-g…)의 기준은 0.3.2 -- 같은 태그는 새 판이 아닙니다.
    assert not update.is_newer("v0.3.2", "v0.3.2-5-gabc")
    # 지금 판을 못 읽으면(태그 없는 해시) 비교할 근거가 없는 것이지 새 판이 아닙니다.
    assert not update.is_newer("v0.4.0", "abc123f")


ASSETS = [
    {"name": "mimiwatch-v0.4.0-windows-x64-cuda.zip", "browser_download_url": "u-cuda", "size": 3},
    {"name": "mimiwatch-v0.4.0-macos-arm64.zip", "browser_download_url": "u-mac", "size": 1},
    {"name": "mimiwatch-v0.4.0-windows-x64.zip", "browser_download_url": "u-win", "size": 2},
    {"name": "mimiwatch-ext-v0.4.0.zip", "browser_download_url": "u-ext", "size": 4},
]


def test_pick_asset():
    assert update.pick_asset(ASSETS, "darwin", "arm64")["url"] == "u-mac"
    # cuda/cpu 변형은 자동 판올림 대상이 아닙니다. 기본(Vulkan) 판을 고릅니다.
    assert update.pick_asset(ASSETS, "win32", "amd64")["url"] == "u-win"
    assert update.pick_asset(ASSETS, "linux", "x86_64") is None
    assert update.pick_asset([], "darwin", "arm64") is None


def test_check_caches_across_restart(monkeypatch):
    # 자산 고르기는 **지금 기계의** 플랫폼을 봅니다(`pick_asset` 의 기본값). 이 시험이
    # 보려는 것은 「고른 자산이 하루짜리 캐시를 거쳐 그대로 나오는가」이므로 플랫폼을
    # 못박습니다 -- 리눅스에는 붙는 자산이 없어서(바로 위 test_pick_asset) CI 에서만
    # asset 이 None 이 되고, 캐시와 아무 상관 없는 이유로 이 시험이 깨져 있었습니다.
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")

    calls = []

    def fake(url):
        calls.append(url)
        return {"tag_name": "v9.9.9", "html_url": "h", "body": "메모",
                "assets": list(ASSETS)}

    monkeypatch.setattr(update, "_get_json", fake)
    monkeypatch.setattr(update, "current_version", lambda: "v0.3.2")
    st = update.check(force=True)
    assert st["available"] is True and st["latest"]["tag"] == "v9.9.9"
    assert st["latest"]["asset"] is not None

    # 하루 안의 재확인은 네트워크에 나가지 않습니다.
    update.check()
    assert len(calls) == 1

    # 재시작(메모리 상태 소실) 뒤에도 캐시 파일이 답합니다.
    update._state.update(checked=0.0, latest=None, available=False)
    st = update.check()
    assert len(calls) == 1 and st["available"] is True

    # force 는 간격을 무시합니다.
    update.check(force=True)
    assert len(calls) == 2


def test_check_failure_is_reported_not_raised(monkeypatch):
    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(update, "_get_json", boom)
    st = update.check(force=True)
    assert st["available"] is False and "check failed" in st["error"]


def test_apply_refuses_repo_run():
    # 저장소에서 돌 때는 갈아 끼울 묶음이 없습니다.
    res = update.apply()
    assert "error" in res and "git pull" in res["error"]


def test_download_without_available_refuses():
    assert "error" in update.download()


def test_status_never_touches_network(monkeypatch):
    def boom(url):
        raise AssertionError("status()가 네트워크에 나갔습니다")

    monkeypatch.setattr(update, "_get_json", boom)
    st = update.status()
    assert st["current"] and st["frozen"] is False and st["state"] == "idle"
