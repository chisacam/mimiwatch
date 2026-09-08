"""The channel glossary: the key, the block, the prompt, the store, the routes."""
import json
import urllib.request

import server
import store
import translate as T


def test_channel_key():
    assert store.channel_key("youtube", "UCabc") == "youtube:UCabc"
    assert store.channel_key("twitch", "login", "ignored") == "twitch:login"
    assert store.channel_key("other", "", "미미") == "manual:미미"
    assert store.channel_key("other", "  ", "미미") == "manual:미미"
    assert store.channel_key("youtube", "", "") == ""
    assert store.channel_key("", "chan") == "other:chan"


def test_glossary_block_empty():
    assert T.glossary_block(None) == ""
    assert T.glossary_block([]) == ""
    # A line with an empty side is not a term; everything skipped means no block.
    assert T.glossary_block([{"from": "", "to": "x"}, [ "a", ""]]) == ""


def test_glossary_block_renders():
    b = T.glossary_block([{"from": "미미", "to": "Mimi"}, ["A", "B"]])
    assert b.startswith("\n")
    assert "미미 → Mimi" in b
    assert "A → B" in b


def test_glossary_block_cap():
    terms = [{"from": f"S{i}", "to": f"T{i}"} for i in range(150)]
    b = T.glossary_block(terms)
    assert f"S{T.GLOSSARY_RENDER_CAP - 1} → T{T.GLOSSARY_RENDER_CAP - 1}" in b
    assert "S149 → T149" not in b


def test_render_prompt_byte_identical_without_glossary():
    p = T.genre_prompt("general")
    plain = T.render_prompt(p, "en", "ko", "hello")
    # No glossary at all and the empty string are the same prompt.
    assert T.render_prompt(p, "en", "ko", "hello", glossary="") == plain
    b = T.glossary_block([{"from": "a", "to": "b"}])
    with_g = T.render_prompt(p, "en", "ko", "hello", glossary=b)
    assert with_g == plain + b


def test_openai_prompt_carries_block(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data
        return FakeResp(json.dumps(
            {"choices": [{"message": {"content": "번역됨"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    tr = T.OpenAICompatible("http://127.0.0.1:1", "m",
                            glossary=T.glossary_block(
                                [{"from": "미미", "to": "Mimi"}]))
    assert tr.translate("미미 안녕", "ko", "en") == "번역됨"
    payload = json.loads(captured["body"])
    content = payload["messages"][0]["content"]
    assert "Mimi" in content
    # And with an empty block the prompt is what it was before.
    plain = T.render_prompt(tr.prompt, "ko", "en", "미미 안녕")
    T.OpenAICompatible("http://127.0.0.1:1", "m").translate("미미 안녕", "ko", "en")
    assert json.loads(captured["body"])["messages"][0]["content"] == plain


def test_build_remote_carries_block():
    tr = T.build({"backend": "openai", "base_url": "http://127.0.0.1:1",
                  "model": "m"},
                 glossary=[{"from": "a", "to": "b"}])
    primary = tr.primary if isinstance(tr, T.WithFallback) else tr
    assert primary.glossary.startswith("\n")


def test_store_roundtrip():
    store.save_glossary("youtube:UCx", "미미",
                        [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}])
    g = store.glossary("youtube:UCx")
    assert g["name"] == "미미"
    assert len(g["terms"]) == 2
    assert [x["channel_key"] for x in store.all_glossaries()] == ["youtube:UCx"]
    # Saving again replaces; an empty term list deletes.
    store.save_glossary("youtube:UCx", "미미", [{"from": "a", "to": "b"}])
    assert len(store.glossary("youtube:UCx")["terms"]) == 1
    store.save_glossary("youtube:UCx", "미미", [])
    assert store.glossary("youtube:UCx") is None
    assert store.save_glossary("", "n", [{"from": "a", "to": "b"}]) is None


def test_routes_registered():
    assert "/api/glossaries" in server.GET_ROUTES
    assert "/api/glossaries" in server.POST_ROUTES


def test_live_session_glossary_sync():
    import live
    s = live.LiveSession("https://www.youtube.com/watch?v=x", None, "ko", "")
    s.site, s.channel = "youtube", "UCx"
    store.save_glossary("youtube:UCx", "미미", [{"from": "a", "to": "b"}])
    s._sync_glossary()
    assert s.channel_key == "youtube:UCx"
    assert s.glossary_name == "미미"
    assert s._glossary_terms == [{"from": "a", "to": "b"}]
    st = s.status()
    assert st["channel_key"] == "youtube:UCx"
    assert st["glossary"] == "미미"
    # No channel means no key and no glossary -- the miss is visible, not silent.
    s2 = live.LiveSession("https://example.com/x", None, "ko", "")
    s2.site, s2.channel = "other", ""
    s2._sync_glossary()
    assert s2.channel_key == "" and s2.glossary_name == ""
