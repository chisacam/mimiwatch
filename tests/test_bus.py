"""The global change feed: who publishes what, and when."""
import json
import queue

import bus
import jobs
import live
import store


def _drain(q):
    out = []
    while True:
        try:
            out.append(json.loads(q.get_nowait()))
        except queue.Empty:
            return out


def test_publish_reaches_every_subscriber_and_drops_when_full():
    a, b = bus.subscribe(), bus.subscribe()
    try:
        bus.publish({"type": "x"})
        assert _drain(a) == [{"type": "x"}] and _drain(b) == [{"type": "x"}]
        for _ in range(bus.QUEUE_MAX + 5):
            bus.publish({"type": "flood"})
        assert a.qsize() == bus.QUEUE_MAX          # What overflows is dropped. Nothing blocks.
        _drain(a)
    finally:
        bus.unsubscribe(a)
        bus.unsubscribe(b)
    bus.publish({"type": "after"})
    assert _drain(a) == []                          # After unsubscribing, nothing arrives
    assert bus.subscribers() == 0


def test_session_changes_are_announced(session):
    q = bus.subscribe()
    try:
        session._persist()
        got = _drain(q)
        assert got and got[-1]["type"] == "session" and got[-1]["id"] == session.id
        assert "state" in got[-1] and "lines" in got[-1]
        store.save_session(session.status())
        live.set_title(session.id, "새 이름")
        got = _drain(q)
        assert got[-1]["title"] == "새 이름"
        live.delete(session.id)
        assert _drain(q)[-1] == {"type": "session", "id": session.id, "deleted": True}
    finally:
        bus.unsubscribe(q)


def test_video_and_job_changes_are_announced(fake_translate):
    store.save_doc("v", {"id": "v", "source_lang": "ja", "viewer_lang": "ko"})
    store.replace_cues("v", [{"start": 0, "end": 1, "text": "a", "lang": "ja", "translations": {}}])
    q = bus.subscribe()
    try:
        from conftest import wait_job
        r = jobs.start_retranslate("v", "local-m2m100", None, None)
        wait_job(r["id"])
        got = _drain(q)
        kinds = [(g["type"], g.get("state"), g.get("reason")) for g in got]
        assert ("job", "running", None) in kinds
        assert ("video", None, "translated") in kinds
        assert kinds[-1][0] == "job" and kinds[-1][1] == "done"
        jobs.delete_video("v")
        assert _drain(q)[-1] == {"type": "video", "id": "v", "reason": "deleted"}
    finally:
        bus.unsubscribe(q)
