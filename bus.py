"""화면에 밀어 주는 전역 변화 알림.

라이브 자막은 세션마다 SSE(`/api/live/events/<id>`)로 흘러가지만, 그 밖의
것 -- 새 세션이 생겼다, 세션이 끝났다, 전사가 끝나 목록에 영상이 늘었다,
다른 창에서 재번역이 돌고 있다 -- 은 화면이 새로고침해야 알았습니다. 확장에서
시작한 방송이 mimiwatch 탭에 보이지 않았고, 목록의 「받는 중」이 방송이 끝난
뒤에도 그대로였습니다.

여기 한 줄기(`/api/events`)로 그런 변화를 전부 내보냅니다. 내용은 세 가지입니다.

    {"type": "session", ...세션 status..., "deleted"?: true}
    {"type": "video",   "id": <영상id>, "reason": "saved"|"translated"|"deleted"}
    {"type": "job",     ...작업 레코드...}
    {"type": "model",   ...modelhub.status()...}   내려받기 진행과 완료
    {"type": "multiview", "id": <묶음id>, "focus": <세션id>, "members": [세션id...], "deleted"?: true}
                        멀티뷰 묶음이 생기거나 초점·멤버가 바뀌거나 없어졌다(live.py 멀티뷰 절)

받는 쪽은 그 부분만 갱신합니다(`web/app/bus.js`). 세션 알림은 자막 한 줄마다
오므로 화면은 목록의 그 줄만 제자리에서 고치고, 모르는 세션이 나타났을 때만
목록을 다시 읽습니다.

구독자 큐가 가득 차면(화면이 멎어 읽어 가지 않음) 그 구독자의 알림은 버립니다.
놓친 알림은 다시 붙을 때 목록을 한 번 새로 읽어 메우므로 잃는 것이 없습니다.
"""
from __future__ import annotations

import json
import queue
import threading

_lock = threading.Lock()
_subs: list[queue.Queue] = []
QUEUE_MAX = 1000


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q: queue.Queue):
    with _lock:
        if q in _subs:
            _subs.remove(q)


def publish(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _lock:
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait(data)
        except queue.Full:
            pass                      # 읽어 가지 않는 구독자. 다시 붙을 때 메웁니다.


def subscribers() -> int:
    with _lock:
        return len(_subs)
