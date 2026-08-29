"""무거운 모델을 프로세스 안에 한 벌만 올려 두는 곳.

예전에는 세션과 작업이 저마다 모델을 올렸습니다. `translate.build()`가 불릴
때마다 Gemma(4.9GB)와 대체용 M2M-100(473MB)을 새로 만들고, 라이브 세션마다
Whisper(845MB)를 새로 올렸습니다. 라이브 세션 하나에 재번역 작업 하나가
겹치면 Gemma가 두 벌이었고, 세션이 끝나면 놓아주었으므로 다음 세션의 첫
자막은 모델을 다시 올리는 몇 초만큼 늦었습니다.

여기서는 (종류, 경로, 장치, …) 열쇠로 한 번만 만들고 돌려 씁니다. 프롬프트나
스레드 수처럼 모델 자체가 아닌 것은 열쇠에 넣지 않습니다 -- 장르가 다른 두
세션이 같은 Gemma를 써야 합니다.

**기본은 상주입니다.** 이 도구의 일이 곧 이 모델들을 돌리는 것이라 상주가
맞고, 세션이 끝날 때마다 놓아주면 위의 지연이 돌아옵니다.

**`MIMIWATCH_MODEL_IDLE_S`를 주면 그만큼 놀린 모델은 놓아줍니다.** 이 기계로
다른 일도 하는 사람을 위한 선택지입니다 -- 방송을 다 보고 두 시간 뒤에도
Gemma가 5GB를 쥐고 있을 이유는 없습니다. "놀린다"는 것은 `shared()`나
`touch()`가 그동안 한 번도 불리지 않았다는 뜻입니다. 쓰는 쪽(전사·번역)이
호출마다 `touch()`를 부르므로, 두 시간 방송이 도는 동안에는 놓아주지 않습니다.
놓아준다는 것은 이 표에서 빼는 것이고, 실제 메모리는 그것을 마지막으로
쥐고 있던 세션·작업이 손을 놓을 때 돌아옵니다. 다음에 청하면 다시 올립니다.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")

# 0이면 놓아주지 않습니다(기본). 초 단위.
IDLE_S = float(os.environ.get("MIMIWATCH_MODEL_IDLE_S") or 0)

_lock = threading.Lock()
_cache: dict[Hashable, object] = {}
_last: dict[Hashable, float] = {}
# 열쇠마다 하나씩. 같은 모델을 두 스레드가 동시에 처음 청하면 한쪽만 만들고
# 다른 쪽은 기다립니다 -- 전체 락을 붙들고 4.9GB를 올리면 그 사이 다른
# 모델을 청한 쪽도 멎습니다.
_building: dict[Hashable, threading.Lock] = {}
_reaper: threading.Thread | None = None


def shared(key: Hashable, factory: Callable[[], T]) -> T:
    """`key`의 모델을 돌려줍니다. 없으면 `factory()`로 한 번 만듭니다."""
    with _lock:
        got = _cache.get(key)
        if got is not None:
            _last[key] = time.time()
            return got
        gate = _building.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            got = _cache.get(key)
            if got is not None:
                _last[key] = time.time()
                return got
        obj = factory()
        with _lock:
            _cache[key] = obj
            _last[key] = time.time()
            _building.pop(key, None)
            _ensure_reaper()
        return obj


def touch(key: Hashable):
    """`key`를 지금 쓰고 있다고 적습니다. 유휴 언로드의 시계를 되돌립니다."""
    if IDLE_S <= 0:
        return
    with _lock:
        if key in _cache:
            _last[key] = time.time()


def reap(now: float | None = None, idle_s: float | None = None) -> list[Hashable]:
    """`idle_s`보다 오래 놀린 모델을 표에서 뺍니다. 뺀 열쇠를 돌려줍니다."""
    idle = IDLE_S if idle_s is None else idle_s
    if idle <= 0:
        return []
    now = time.time() if now is None else now
    with _lock:
        gone = [k for k, t in _last.items() if k in _cache and now - t >= idle]
        for k in gone:
            _cache.pop(k, None)
            _last.pop(k, None)
    for k in gone:
        print(f"[models] {idle:.0f}초 놀려서 놓아줍니다: {' · '.join(str(p) for p in k)}",
              file=sys.stderr, flush=True)
    return gone


def _ensure_reaper():
    """유휴 언로드가 켜져 있으면 감시 스레드를 하나 띄웁니다. `_lock` 안에서 부릅니다."""
    global _reaper
    if IDLE_S <= 0 or (_reaper is not None and _reaper.is_alive()):
        return

    def loop():
        period = max(5.0, min(60.0, IDLE_S / 2))
        while True:
            time.sleep(period)
            reap()

    _reaper = threading.Thread(target=loop, daemon=True, name="models-reaper")
    _reaper.start()


def resident() -> list[str]:
    """지금 올라와 있는 모델의 열쇠. bench/doctor.py가 찍어 봅니다."""
    with _lock:
        return [" · ".join(str(k) for k in key) for key in _cache]


def clear():
    """전부 놓아줍니다. 시험이 서로에게 영향을 주지 않게 하는 용도입니다."""
    with _lock:
        _cache.clear()
        _last.clear()
        _building.clear()
