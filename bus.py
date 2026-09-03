"""Global change notifications pushed to the screen.

Live subtitles flow per session over SSE (`/api/live/events/<id>`), but
everything else -- a new session appeared, a session ended, a transcription
finished and the list gained a video, a retranslation is running in another
window -- was only known once the screen refreshed. A stream started from the
extension did not appear in the mimiwatch tab, and "downloading" in the list
stayed there after the stream had ended.

This one stream (`/api/events`) sends out every such change. There are five
kinds of content.

    {"type": "session", ...session status..., "deleted"?: true}
    {"type": "video",   "id": <video id>, "reason": "saved"|"translated"|"deleted"}
    {"type": "job",     ...job record...}
    {"type": "model",   ...modelhub.status()...}   download progress and completion
    {"type": "multiview", "id": <group id>, "focus": <session id>, "members": [session id...],
                          "deleted"?: true}
                        a multiview group appeared, its focus or members changed, or it
                        went away (live.py, the multiview section)

The receiving side refreshes only that part (`web/app/bus.js`). Session
notifications arrive for every subtitle line, so the screen fixes just that row
of the list in place, and re-reads the list only when a session it does not know
about appears.

When a subscriber's queue fills up (the screen is stuck and not reading), that
subscriber's notifications are thrown away. Nothing is lost, because the missed
notifications are filled in by re-reading the list once when it reattaches.
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
            pass                      # A subscriber that is not reading. It fills in on reattach.


def subscribers() -> int:
    with _lock:
        return len(_subs)
