"""The pure logic of the translation layer: the checks, the prompt, the fallback path."""
import translate as T
from conftest import FakeTranslator


def test_looks_broken():
    assert T.looks_broken("", "abc")
    assert T.looks_broken("⁇⁇", "abc")
    assert T.looks_broken("x" * 500, "abc")            # Runaway
    assert not T.looks_broken("안녕", "こんにちは")


def test_render_prompt_with_and_without_context():
    p = T.genre_prompt("general")
    plain = T.render_prompt(p, "ja", "ko", "はい")
    assert "はい" in plain and "Context" not in plain
    ctx = T.render_prompt(p, "ja", "ko", "はい", ["前の行", "もう一つ"])
    assert "前の行" in ctx and "NOT to be translated" in ctx
    assert ctx.index("前の行") < ctx.index("Now translate only this one")
    # A user prompt in the old shape (no {context}) still renders as it is.
    assert T.render_prompt("Translate {src}->{tgt}: {text}", "ja", "ko", "x", ["c"]) == "Translate ja->ko: x"


def test_genre_prompt_falls_back_to_general():
    assert T.genre_prompt("no-such") == T.genre_prompt(None) == T.GENRE_PROMPTS["general"]["prompt"]


class Failing(T.Translator):
    name = "primary"

    def __init__(self):
        self.calls = 0

    def translate(self, text, src, tgt, context=None):
        self.calls += 1
        raise T.TranslationFailed("down")


def test_fallback_trips_after_consecutive_failures_and_probes_later():
    primary, backup = Failing(), FakeTranslator(lambda t, s, g, c: "B:" + t)
    fb = T.WithFallback(primary, backup)
    for i in range(T.WithFallback.TRIP_AFTER):
        assert fb.translate(f"l{i}", "ja", "ko") == f"B:l{i}"
        assert fb.last_used == "backup"
    assert fb.tripped and primary.calls == T.WithFallback.TRIP_AFTER
    # Once tripped, the remote is not called.
    for i in range(T.WithFallback.RETRY_AFTER - 1):
        fb.translate("x", "ja", "ko")
    assert primary.calls == T.WithFallback.TRIP_AFTER
    # It probes once every RETRY_AFTER lines.
    fb.translate("x", "ja", "ko")
    assert primary.calls == T.WithFallback.TRIP_AFTER + 1
    assert fb.failures == T.WithFallback.TRIP_AFTER + 1


def test_fallback_records_primary_when_it_works():
    fb = T.WithFallback(FakeTranslator(), FakeTranslator(lambda t, s, g, c: "B"))
    assert fb.translate("a", "ja", "ko") == "T:a" and fb.last_used == "primary"


def test_lazy_builds_once_and_only_when_used():
    made = []

    def factory():
        made.append(1)
        return FakeTranslator()
    lazy = T.Lazy(factory)
    assert made == []
    assert lazy.translate("a", "ja", "ko") == "T:a"
    lazy.translate("b", "ja", "ko")
    assert made == [1] and lazy.name == "fake"


def test_should_translate_rules():
    t = FakeTranslator()
    assert not t.should_translate("a", "ko", "ko")
    assert not t.should_translate("  ", "ja", "ko")
    t.min_chars = 3
    assert not t.should_translate("はい", "ja", "ko") and t.should_translate("こんにちは", "ja", "ko")
