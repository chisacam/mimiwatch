"""무거운 모델을 프로세스 안에 한 벌만 올려 두는 곳.

예전에는 세션과 작업이 저마다 모델을 올렸습니다. `translate.build()`가 불릴
때마다 Gemma(4.9GB)와 대체용 M2M-100(473MB)을 새로 만들고, 라이브 세션마다
Whisper(845MB)를 새로 올렸습니다. 라이브 세션 하나에 재번역 작업 하나가
겹치면 Gemma가 두 벌이었고, 세션이 끝나면 놓아주었으므로 다음 세션의 첫
자막은 모델을 다시 올리는 몇 초만큼 늦었습니다.

여기서는 (종류, 경로, 장치, …) 열쇠로 한 번만 만들고 돌려 씁니다. 프롬프트나
스레드 수처럼 모델 자체가 아닌 것은 열쇠에 넣지 않습니다 -- 장르가 다른 두
세션이 같은 Gemma를 써야 합니다.

**놓아주지 않습니다.** 이 도구의 일이 곧 이 모델들을 돌리는 것이라 상주가
맞고, 세션이 끝날 때마다 놓아주면 위의 지연이 돌아옵니다. 한참 놀면 놓아주는
정책(`MIMIWATCH_MODEL_IDLE_S` 같은)은 필요해지면 그때 여기 한 곳에 넣으면
됩니다. 시험은 `clear()`로 비웁니다.
"""
from __future__ import annotations

import threading
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_cache: dict[Hashable, object] = {}
# 열쇠마다 하나씩. 같은 모델을 두 스레드가 동시에 처음 청하면 한쪽만 만들고
# 다른 쪽은 기다립니다 -- 전체 락을 붙들고 4.9GB를 올리면 그 사이 다른
# 모델을 청한 쪽도 멎습니다.
_building: dict[Hashable, threading.Lock] = {}


def shared(key: Hashable, factory: Callable[[], T]) -> T:
    """`key`의 모델을 돌려줍니다. 없으면 `factory()`로 한 번 만듭니다."""
    with _lock:
        got = _cache.get(key)
        if got is not None:
            return got
        gate = _building.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            got = _cache.get(key)
            if got is not None:
                return got
        obj = factory()
        with _lock:
            _cache[key] = obj
            _building.pop(key, None)
        return obj


def resident() -> list[str]:
    """지금 올라와 있는 모델의 열쇠. bench/doctor.py가 찍어 봅니다."""
    with _lock:
        return [" · ".join(str(k) for k in key) for key in _cache]


def clear():
    """전부 놓아줍니다. 시험이 서로에게 영향을 주지 않게 하는 용도입니다."""
    with _lock:
        _cache.clear()
        _building.clear()
