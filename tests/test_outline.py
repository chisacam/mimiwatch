"""The document view's pure logic: what the model's answer is allowed to look
like, what one pass is handed, and what a pass does to the document.

No model is built here. `advance` asks for nothing but a name, a context size
and a `generate`, which is what makes the fake below an honest stand-in -- a
real writer would be 4.9 GB of Gemma for an answer the test already knows.
"""
import pytest

import outline as O
import translate as T


class _Writer:
    """An engine that answers from a list instead of thinking.

    `context_tokens` is here because `window_chars` reads it off the engine; 0
    is what a remote endpoint reports and what most of these tests want.
    """

    name = "fake-writer"

    def __init__(self, *answers, context_tokens: int = 0):
        self.answers = list(answers)
        self.context_tokens = context_tokens
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    def generate(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return self.answers.pop(0) if self.answers else ""


# ---- parse: the answer is Markdown, and it may be a little wrong -----------

def test_parse_drops_a_preamble_before_the_first_heading():
    # "Answer with the sections only" is in the prompt and models say "Here are
    # the notes:" anyway. It is not a bullet of the first section.
    got = O.parse("Here are the notes so far:\n## Opening\n- one\n")
    assert got == [{"title": "Opening", "bullets": ["one"]}]


def test_parse_takes_every_heading_marker_the_model_might_use():
    got = O.parse("## A\n- a1\n### B\n- b1\n**C**\n- c1\n")
    assert [s["title"] for s in got] == ["A", "B", "C"]


def test_parse_takes_every_bullet_marker_the_model_might_use():
    got = O.parse("## A\n- one\n* two\n• three\n1. four\n2) five\n")
    assert got[0]["bullets"] == ["one", "two", "three", "four", "five"]


def test_parse_drops_a_bullet_that_arrives_before_any_heading():
    # Inventing a heading to hang it on would put a title in the document that
    # the model never wrote.
    got = O.parse("- stray\n## A\n- a1\n")
    assert got == [{"title": "A", "bullets": ["a1"]}]


def test_parse_does_not_open_a_section_on_a_bare_heading_mark():
    # "##" with nothing after it is the model starting a section it then did
    # not write. The bullets under it belong to the section still open.
    got = O.parse("## A\n- a1\n##\n- a2\n")
    assert got == [{"title": "A", "bullets": ["a1", "a2"]}]


def test_parse_takes_an_unmarked_line_under_a_heading_as_a_bullet():
    # The line is speech the model wrote down and forgot to mark. Dropping it
    # loses the content; the only thing in doubt is the dash.
    got = O.parse("## A\nthe speaker gave a number\n- a1\n")
    assert got[0]["bullets"] == ["the speaker gave a number", "a1"]


def test_parse_unwraps_bold_inside_a_bullet():
    # The page styles the document itself, so the model's own emphasis is
    # markup that would be shown literally.
    got = O.parse("## A\n- **Revenue** rose 20%\n")
    assert got[0]["bullets"] == ["Revenue rose 20%"]


def test_parse_of_an_answer_with_no_heading_is_empty():
    assert O.parse("") == [] and O.parse("nothing but prose here") == []


# ---- render_open: what the next pass is shown of the section it is writing --

def test_render_open_keeps_the_heading_and_drops_the_oldest_bullets():
    # A pass handed bullets without the heading they belong under opens a
    # duplicate section, so the heading is the one line that cannot fall off.
    section = {"title": "T" * 20,
               "bullets": [f"b{i} " + "x" * 90 for i in range(6)], "t": 5.0}
    got = O.render_open(section)
    assert len(got) <= O.OPEN_SECTION_CHARS
    assert got.splitlines()[0] == "## " + "T" * 20
    # The newest bullets survive; b0 and b1 are the ones over the budget.
    assert all(line.startswith("- ") for line in got.splitlines()[1:])
    assert "b0" not in got and "b1" not in got and "b5" in got


def test_render_open_of_nothing_says_the_talk_is_only_starting():
    assert O.render_open(None) == O.NO_SECTION


# ---- window_chars: how much speech one pass may be handed ------------------

def test_window_chars_gives_a_remote_engine_the_fixed_window():
    # A remote endpoint reports no context. Its memory is not this machine's,
    # so the window is the one a pass wants rather than the one it can bear.
    assert O.window_chars(_Writer()) == 4000


def test_window_chars_leaves_a_local_engine_room_for_its_own_prompt():
    got = O.window_chars(_Writer(context_tokens=8192))
    assert got == 8192 - O.OUT_TOKENS - O.PROMPT_TOKENS - O.OPEN_SECTION_CHARS
    # A roomier context buys a longer window, which is what makes raising
    # n_ctx in backends.json worth something here.
    assert got > O.window_chars(_Writer(context_tokens=2048))


def test_window_chars_never_goes_below_the_floor():
    # A context this small cannot hold a useful window, but refusing outright
    # would be worse than a short one.
    assert O.window_chars(_Writer(context_tokens=1024)) == O.MIN_WINDOW_CHARS
    assert O.window_chars(_Writer(context_tokens=128)) == O.MIN_WINDOW_CHARS


# ---- advance: one pass ----------------------------------------------------

def _doc(**over) -> dict:
    doc = O.empty("engine-before")
    doc.update({"sections": [{"title": "A", "bullets": ["a1"], "t": 12.0}],
                "lines": 3, "chars": 100, "folded": 7})
    doc.update(over)
    return doc


def test_advance_rewrites_the_open_section_and_keeps_its_time():
    # The heading may change -- the section did not start again, so the time it
    # opened at is the one already on it.
    w = _Writer("## A revised\n- a1\n- a2\n")
    got = O.advance(_doc(), "speech", "ko", w, at=99.0, lines=2)
    assert got["sections"] == [
        {"title": "A revised", "bullets": ["a1", "a2"], "t": 12.0}]


def test_advance_stamps_a_new_section_with_the_windows_time():
    # Where inside the window the topic turned is not asked for, so the window
    # is the unit: the section is stamped with the time it began.
    w = _Writer("## A\n- a1\n## B new\n- b1\n")
    got = O.advance(_doc(), "speech", "ko", w, at=99.0)
    assert [(s["title"], s["t"]) for s in got["sections"]] == [
        ("A", 12.0), ("B new", 99.0)]


def test_advance_stamps_the_very_first_section_too():
    w = _Writer("## Opening\n- one\n")
    got = O.advance(O.empty(), "speech", "ko", w, at=4.5)
    assert got["sections"] == [
        {"title": "Opening", "bullets": ["one"], "t": 4.5}]


def test_advance_carries_the_counts_forward():
    w = _Writer("## A\n- a1\n")
    got = O.advance(_doc(), "some speech", "ko", w, at=99.0, lines=2)
    assert got["lines"] == 5                      # 3 before + 2 in this window
    assert got["chars"] == 100 + len("some speech")
    # `folded` is the caller's bookmark and no business of a pass -- it rides
    # in the document so that a reopened session knows where to carry on from.
    assert got["folded"] == 7
    assert got["engine"] == "fake-writer" and got["error"] == ""
    assert got["updated"] > 0


def test_advance_raises_when_the_answer_holds_no_section():
    # There is no source text to fall back on the way a translation has, so the
    # caller has to be told rather than handed an empty document.
    w = _Writer("I am sorry, I cannot summarise that.")
    with pytest.raises(T.GenerationFailed):
        O.advance(_doc(), "speech", "ko", w)


def test_advance_on_an_empty_window_spends_no_generation():
    w = _Writer("## A\n- a1\n")
    doc = _doc()
    assert O.advance(doc, "   \n  ", "ko", w) is doc
    assert w.prompts == []


def test_advance_shows_the_writer_the_open_section_and_the_glossary():
    w = _Writer("## A\n- a1\n")
    O.advance(_doc(), "무슨 말을 했다", "ko", w,
              terms=[{"from": "mimiwatch", "to": "미미와치"}])
    prompt = w.prompts[0]
    assert "Korean" in prompt                     # the viewer's language
    assert "## A" in prompt and "- a1" in prompt   # the section being rewritten
    assert "mimiwatch = 미미와치" in prompt
    assert "무슨 말을 했다" in prompt
    assert w.max_tokens == [O.OUT_TOKENS]


def test_terms_block_takes_a_one_sided_term_and_stops_at_the_cap():
    terms = [{"from": "a", "to": "가"}, {"from": "b", "to": ""}, "not a dict"]
    assert O.terms_block(terms) == "- a = 가\n- b"
    assert O.terms_block(None) == ""
    many = [{"from": f"t{i}", "to": f"ㅌ{i}"} for i in range(O.TERMS_CAP + 10)]
    assert len(O.terms_block(many).splitlines()) == O.TERMS_CAP


# ---- to_markdown: the document as a file ----------------------------------

def test_to_markdown_writes_the_headings_bullets_and_the_clock():
    doc = O.empty()
    doc["sections"] = [{"title": "Opening", "bullets": ["one", "two"], "t": 12.0},
                       {"title": "Later", "bullets": ["three"], "t": 3699.0}]
    got = O.to_markdown(doc, "A talk")
    assert got.startswith("# A talk")
    assert "## Opening" in got and "## Later" in got
    assert "- one\n- two" in got and "- three" in got
    # The timestamp is the only way back from a heading to the recording.
    assert "<sub>00:12</sub>" in got and "<sub>01:01:39</sub>" in got
    assert got.endswith("\n")


def test_to_markdown_of_a_section_with_no_time_writes_no_clock():
    doc = O.empty()
    doc["sections"] = [{"title": "Opening", "bullets": ["one"], "t": 0.0}]
    got = O.to_markdown(doc)
    assert "<sub>" not in got and got.startswith("## Opening")


# ---- who is allowed to write ----------------------------------------------

def test_only_a_prompt_backend_can_write_a_document():
    # The rule the whole feature rests on. M2M-100 renders any string into the
    # target language, so handed a summary prompt it answers with that prompt
    # in Korean -- no exception, nothing amiss, a document quietly filling with
    # translated instructions.
    assert T.can_write({"backend": "gemma"}) is True
    assert T.can_write({"backend": "openai"}) is True
    assert T.can_write({"backend": "local"}) is False
    assert T.can_write({"backend": "something-new"}) is False
    assert T.can_write(None) is False


def test_build_writer_returns_nothing_for_a_backend_that_cannot_write():
    assert T.build_writer({"backend": "local"}) is None
    assert T.build_writer({"backend": "something-new"}) is None
    assert T.build_writer(None) is None


def test_build_writer_builds_the_prompt_backends():
    # Neither line loads a model: LocalGemma registers a holder and llama.cpp
    # is reached only on the first generate().
    remote = T.build_writer({"backend": "openai", "base_url": "http://x",
                             "model": "m", "api_key": ""})
    assert isinstance(remote, T.OpenAICompatible) and remote.can_generate
    local = T.build_writer({"backend": "gemma", "n_ctx": 4096})
    assert isinstance(local, T.LocalGemma) and local.can_generate
    # The context is published so that window_chars can size the window; it is
    # the n_ctx the engine was built with, not a second number kept in step.
    assert local.context_tokens == 4096
    assert O.window_chars(local) == 4096 - O.OUT_TOKENS - O.PROMPT_TOKENS - O.OPEN_SECTION_CHARS


def test_a_backend_that_cannot_generate_says_so_instead_of_answering():
    # Read off the class -- constructing M2M-100 loads the model.
    assert T.LocalM2M.can_generate is False
    assert T.Translator.can_generate is False
    with pytest.raises(T.GenerationFailed):
        T.Translator().generate("summarise this")
