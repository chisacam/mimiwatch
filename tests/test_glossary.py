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
    assert "/api/glossaries/term" in server.POST_ROUTES


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


def test_add_glossary_term_merges_into_what_is_already_there():
    """One term at a time, without the caller having to send the whole list."""
    store.save_glossary("youtube:UCx", "미미", [{"from": "a", "to": "b"}])
    out = store.add_glossary_term("youtube:UCx", "ignored", "c", "d")
    assert out["terms"] == [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
    # An existing glossary keeps its name. Adding a word off a subtitle is not
    # a rename, and the name is what the settings list is read by.
    assert out["name"] == "미미"
    # The same source term is answered once, not twice -- two answers for one
    # term in the prompt is worse than the old answer.
    out = store.add_glossary_term("youtube:UCx", "", "a", "B2")
    assert out["terms"] == [{"from": "a", "to": "B2"}, {"from": "c", "to": "d"}]
    assert store.glossary("youtube:UCx")["terms"] == out["terms"]
    # A channel with no glossary yet gets one, named by what was sent.
    made = store.add_glossary_term("twitch:zzz", "젯", "x", "y")
    assert made["name"] == "젯" and made["terms"] == [{"from": "x", "to": "y"}]
    # All three are needed. A blank saves nothing rather than an empty term.
    assert store.add_glossary_term("", "n", "a", "b") is None
    assert store.add_glossary_term("youtube:UCx", "n", "", "b") is None
    assert store.add_glossary_term("youtube:UCx", "n", "a", " ") is None
    assert store.glossary("youtube:UCx")["terms"] == out["terms"]


def _session(channel, backend_id):
    import live
    s = live.LiveSession(f"https://www.youtube.com/watch?v={channel}", None, "ko",
                         backend_id)
    s.site, s.channel = "youtube", channel
    s._sync_glossary()
    return s


def test_reload_glossary_reaches_the_running_session_only(fake_translate):
    """A term added mid-stream applies to the next line, not the next reconnect."""
    import config
    import live
    tr_id = config.PROTECTED["tr"]
    on_channel = _session("UCx", tr_id)
    on_channel._tr = object()                 # stands for "the engines are built"
    elsewhere = _session("UCother", tr_id)
    elsewhere._tr = untouched = object()
    not_started = _session("UCx", tr_id)      # focus never reached it
    not_started._tr = None
    no_such_backend = _session("UCx", "no-such-backend")
    no_such_backend._tr = also_untouched = object()
    for s in (on_channel, elsewhere, not_started, no_such_backend):
        live._sessions[s.id] = s
    try:
        store.add_glossary_term("youtube:UCx", "미미", "커맨더", "지휘관")
        assert live.reload_glossary("youtube:UCx") == [on_channel.id]
        # The terms are on the session and the translator was built again with them.
        assert on_channel._glossary_terms == [{"from": "커맨더", "to": "지휘관"}]
        assert fake_translate[-1].spec["id"] == tr_id
        # Another channel is left alone, and so is a session whose engines were
        # never built -- it reads the glossary when it builds them.
        assert elsewhere._tr is untouched
        assert not_started._tr is None
        # A session whose backend is gone keeps its translator. Building on a
        # missing spec would drop it to M2M-100 without saying so.
        assert no_such_backend._tr is also_untouched
        assert live.reload_glossary("") == []
        assert live.reload_glossary("youtube:nobody") == []
    finally:
        live._sessions.clear()

