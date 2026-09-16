"""Turns a running transcript into a document with headings.

A subtitle is read once and gone. Everything else here is built around that --
the overlay shows the line that is on screen now, and the script panel keeps
the ones before it in the order they were said. Neither of them answers "what
has this talk been about", which for a seminar or a meeting is the only
question the listener actually has at the end.

**The document is written forward, not rewritten.** The alternative -- hand the
whole transcript over every pass and take a fresh summary -- was rejected
before it was built, for two reasons. The cost is quadratic in a session that
runs for an hour, on a model that shares its lock with live translation
(translate.LocalGemma.generate says what that costs). And the reader loses
their place: a paragraph that is rewritten from scratch every ninety seconds
cannot be read while it is being written. So a pass sees only the section it is
currently writing and the speech that has arrived since the last one, and
everything above that is already fixed.

**The unit of placement is the window, not the line.** A pass is handed a block
of speech and may decide a new topic started somewhere inside it, but not
where -- the model is not asked, because an answer to that would be invented.
So a section that opens during a pass is stamped with the time the window
began. With the cadence in live.py that is accurate to a minute or two, which
is what a heading in a document is worth anyway.

**Nothing here is a translation.** The transcript goes in as it was spoken and
the document comes out in the language the viewer reads, which is one pass, not
two. Summarising an already-translated transcript would stack the summary's
errors on top of the translation's, and the translated text is the one place
the proper nouns have already had a chance to go wrong.
"""
from __future__ import annotations

import re
import time

import translate as mw_translate

# What one pass is allowed to write. Enough for a heading and five bullets in
# Korean or Japanese, where a character is roughly a token.
OUT_TOKENS = 420
# What the instructions below cost, in tokens, near enough. Measured by eye
# rather than by the tokenizer on purpose -- it only has to be an overestimate,
# and loading a tokenizer to size a prompt would load the model.
PROMPT_TOKENS = 280
# The floor. A context this small cannot hold a useful window, but refusing
# outright would be worse than a short one.
MIN_WINDOW_CHARS = 600
# How much of the in-progress section is handed back to the model. Past this
# the section is too long to be one section anyway.
OPEN_SECTION_CHARS = 420

# A document that has run for an hour has more sections than a prompt needs to
# know about. Only the open one is sent; this caps what is kept on the page.
MAX_SECTIONS = 200

LANG_NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese",
              "zh": "Chinese"}

# The model is asked for Markdown rather than JSON. Both were considered; the
# difference is what happens when the answer is a little wrong. A JSON answer
# with one unescaped quote is not a document at all, and a small model writing
# free-form text inside JSON strings produces exactly that. A heading line that
# arrives as "## 1. Title" or "**Title**" is still a heading, and the parser
# below takes it.
PROMPT = """\
You are taking notes on a talk while it is being given.

Below is the section of the notes you are currently writing, followed by the \
speech that has arrived since. Rewrite that section so that it takes in the \
new speech. If the speaker has clearly moved on to a different subject, finish \
that section and start a new one.

Rules:
- Write in {lang}.
- Start every section with "## " and a heading of at most eight words.
- Under each heading write 2 to 5 lines, each starting with "- ".
- A line says something the speaker said -- a claim, a number, a name, a \
decision. Not "the speaker explains X".
- Use nothing that is not in the speech below. If the speech says too little \
to change the section, repeat the section as it is.
- Start at most one new section.
- Answer with the sections only. No preamble, no closing remark.
{glossary}
The section being written:
{open_section}

The speech that has arrived since:
{window}"""

NO_SECTION = "(nothing written yet -- this is the beginning of the talk)"

GLOSSARY_BLOCK = """
Spell these the way they are written here:
{terms}
"""

# The channel glossary is a translation aid: "render this as that". A document
# takes the same list for a different purpose -- these are the proper nouns of
# this talk, and they are exactly the words a transcript gets wrong. Only as
# many as a heading and five bullets could plausibly need.
TERMS_CAP = 40


def terms_block(terms: list | None) -> str:
    rows = []
    for term in (terms or [])[:TERMS_CAP]:
        if not isinstance(term, dict):
            continue
        src = (term.get("from") or "").strip()
        dst = (term.get("to") or "").strip()
        if src and dst:
            rows.append(f"- {src} = {dst}")
        elif src or dst:
            rows.append(f"- {src or dst}")
    return "\n".join(rows)

_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s*|\*\*)\s*(.+?)\s*(?:\*\*)?\s*$")
_BULLET = re.compile(r"^\s{0,4}(?:[-*•]|\d{1,2}[.)])\s+(.*\S)\s*$")


def window_chars(writer) -> int:
    """How much speech one pass may be handed, in characters.

    Derived from the engine's context rather than fixed, because the two
    engines differ by an order of magnitude and the local one is the tight
    case. `n_ctx` is part of the key models.shared() files Gemma under, so a
    roomier context for the document alone would mean a second copy of the
    model in memory -- the window gives way instead. Someone who raises n_ctx
    in backends.json for their own machine gets the longer window for free.

    A character is counted as a token. That is wrong for English by a factor of
    three or four and about right for Korean and Japanese, which is the way
    round it has to be wrong: overrunning the context truncates the speech
    silently, and the languages this is used on most are the tight ones.
    """
    n_ctx = int(getattr(writer, "context_tokens", 0) or 0)
    if not n_ctx:
        # A remote endpoint. Its context is its own business and it is not
        # sharing memory with anything here, so the window is the one a
        # reasonable pass wants rather than the one the machine can bear.
        return 4000
    return max(MIN_WINDOW_CHARS,
               n_ctx - OUT_TOKENS - PROMPT_TOKENS - OPEN_SECTION_CHARS)


def empty(engine: str = "") -> dict:
    # `folded` is the caller's bookmark -- the highest cue id already written
    # in. It rides in the document rather than beside it so that a session
    # reopened after a restart knows where to carry on from, instead of putting
    # the whole transcript through again and writing the talk twice.
    return {"sections": [], "lines": 0, "updated": 0.0, "engine": engine,
            "error": "", "chars": 0, "folded": 0}


def _clean(s: str) -> str:
    # Models like to close a heading with the same hashes they opened it with,
    # and to bold the first two words of a bullet. Neither survives into the
    # document; the page styles it.
    s = s.strip().strip("#").strip()
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" :·-")


def parse(answer: str) -> list[dict]:
    """Reads the model's answer as a list of sections.

    Tolerant on purpose. Everything before the first heading is dropped (that
    is where a preamble lands when the model writes one anyway), and a bullet
    arriving before any heading is dropped with it rather than inventing a
    heading to hang it on.
    """
    out: list[dict] = []
    for raw in (answer or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        bullet = _BULLET.match(line)
        if bullet and out:
            text = _clean(bullet.group(1))
            if text:
                out[-1]["bullets"].append(text)
            continue
        if bullet and not out:
            continue
        head = _HEADING.match(line)
        if head:
            title = _clean(head.group(1))
            # A bare "##" with nothing after it is not a section.
            if title:
                out.append({"title": title, "bullets": []})
            continue
        # A plain line under a heading is taken as a bullet the model forgot to
        # mark. Under no heading it is a preamble and goes nowhere.
        if out:
            text = _clean(line)
            if text:
                out[-1]["bullets"].append(text)
    return [s for s in out if s["bullets"] or s["title"]]


def render_open(section: dict | None) -> str:
    if not section:
        return NO_SECTION
    lines = [f"## {section.get('title', '')}"]
    for b in section.get("bullets", []):
        lines.append(f"- {b}")
    # The heading always goes, and then as many of the newest bullets as fit.
    # The oldest ones fall off rather than the heading, because a pass handed
    # bullets without the heading they belong under opens a duplicate section.
    head, kept, room = lines[0], [], OPEN_SECTION_CHARS - len(lines[0])
    for b in reversed(lines[1:]):
        if len(b) + 1 > room:
            break
        kept.insert(0, b)
        room -= len(b) + 1
    return "\n".join([head, *kept])


def build_prompt(outline: dict, window: str, viewer_lang: str,
                 terms: list | None = None) -> str:
    sections = outline.get("sections") or []
    rows = terms_block(terms)
    block = GLOSSARY_BLOCK.format(terms=rows) if rows else ""
    return PROMPT.format(
        lang=LANG_NAMES.get(viewer_lang, viewer_lang or "English"),
        glossary=block,
        open_section=render_open(sections[-1] if sections else None),
        window=window.strip())


def advance(outline: dict, window: str, viewer_lang: str, writer,
            terms: list | None = None, at: float = 0.0,
            lines: int = 0) -> dict:
    """One pass. Returns the document with the window folded into it.

    Raises `translate.GenerationFailed` when the engine could not answer. The
    caller keeps the document it already had -- a summary has no source text to
    fall back on the way a translation does, so the only honest recovery is to
    leave the last good one standing and say so.
    """
    if not window.strip():
        return outline
    answer = writer.generate(build_prompt(outline, window, viewer_lang, terms),
                             max_tokens=OUT_TOKENS)
    fresh = parse(answer)
    if not fresh:
        raise mw_translate.GenerationFailed(
            f"the answer held no sections: {answer[:80]!r}")
    sections = list(outline.get("sections") or [])
    # The first section the model wrote is the one it was given, rewritten. It
    # replaces the open section and keeps that section's time -- the heading may
    # have changed, but the section did not start again.
    if sections:
        fresh[0]["t"] = sections[-1].get("t", 0.0)
        sections[-1] = fresh[0]
    else:
        fresh[0]["t"] = at
        sections.append(fresh[0])
    for extra in fresh[1:]:
        # Opened somewhere inside this window. Which line it was is not asked
        # for; see the module docstring.
        extra["t"] = at
        sections.append(extra)
    del sections[:-MAX_SECTIONS]
    return {"sections": sections,
            "lines": int(outline.get("lines", 0)) + int(lines),
            "chars": int(outline.get("chars", 0)) + len(window),
            "folded": int(outline.get("folded", 0)),
            "updated": time.time(),
            "engine": getattr(writer, "name", ""),
            "error": ""}


def to_markdown(outline: dict, title: str = "") -> str:
    """The document as a file. Timestamps are kept -- they are the only way
    back from a heading to the part of the recording it came from."""
    parts = []
    if title:
        parts.append(f"# {title}\n")
    for s in outline.get("sections") or []:
        stamp = _clock(s.get("t", 0.0))
        parts.append(f"## {s.get('title', '')}"
                     + (f"  \n<sub>{stamp}</sub>\n" if stamp else "\n"))
        for b in s.get("bullets", []):
            parts.append(f"- {b}")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def _clock(seconds: float) -> str:
    if not seconds or seconds < 0:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
