"""Check the docs against the UI string table.

The docs quote screen labels in double quotes ("Models & Tools", "⚙ Engines
› Update", "Restart and apply"). Nothing else looks at those quotes. A label
renamed in the string table leaves the docs stale with no warning, and the
English and the Korean companion can drift apart the same way -- a pair that
disagrees is a bug (AGENTS.md). This check is the guard.

Two things are checked.

  [1] The table still holds the labels the docs quote. A short curated list
      (key, quoted form) -- when a label is renamed in `web/app/strings/` or
      `web/strings-ext.js`, its entry fails here and says what the table now
      says. The quoted form may be a prefix of the table value ("Manage" for
      "Manage ▾"), because the docs quote the words a person reads.
  [2] The pair agrees. For every entry of the string table, an English doc
      that quotes the English value must have its `.ko.md` carry the Korean
      value. One English value can sit in several entries with different
      Korean values ("Download" is 내려받기 in the export dialog and 받기 on
      the update strip), so any one of them counts. Spaces are ignored on the
      comparison -- the Korean docs attach a particle to what precedes it
      with no space, and "모델 · 도구" and "모델·도구" are the same label.

    .venv/bin/python bench/docs_check.py

It is a `*_check.py`, so `bench/check_all.py` runs it. It reads no model and
goes nowhere.
"""
from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAIL = []

# The string-table files. The page side is the source; ext/strings-ext.js is a
# byte-identical copy of web/strings-ext.js (ext_check.py guards that), so the
# page side alone is the ground.
STRING_FILES = [os.path.join(HERE, "web", "app", "strings", n)
                for n in ("core.js", "engines.js", "panel.js", "player.js")]
STRING_FILES.append(os.path.join(HERE, "web", "strings-ext.js"))

DOCS = [os.path.join(HERE, "README.md")] + sorted(glob.glob(os.path.join(HERE, "docs", "*.md")))
DOCS = [d for d in DOCS if not d.endswith(".ko.md")]

# The labels the docs quote. (string-table key, the form the docs write down.)
# Add one here when a doc starts quoting a label that is not quoted yet.
QUOTED = [
    ("header.manage", "Manage"),
    ("manage.engines", "⚙ Engines"),
    ("settings.models.title", "Models & Tools"),
    ("settings.update.title", "Update"),
    ("settings.update.check", "Check for updates"),
    ("update.download", "Download"),
    ("update.apply", "Restart and apply"),
    ("header.live.stop", "Stop"),
    ("live.resume.button", "Resume"),
    ("add.title", "Add video"),
    ("add.start", "Start"),
    ("setup.notice.start", "First-time setup"),
    ("library.add", "＋ Add"),
    ("capture.tabAudio", "Tab audio"),
    ("settings.cookies.title", "YouTube login cookies"),
    ("settings.cookies.delete", "Delete"),
    ("popup.pick", "What to overlay"),
    ("popup.panel", "Subtitle log in the chat column"),
    ("content.askKeep", "Keep"),
    ("popup.hide", "Hide from page"),
    ("popup.stop", "■ Stop transcribing"),
    ("popup.show", "Put back on page"),
    ("add.source", "Audio source"),
    ("add.source.tab", "Sound from another tab in this browser"),
    ("panel.tr.all", "All"),
    ("panel.tr.none", "None"),
]


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def load_table() -> dict:
    """key -> (en, ko). One entry per key; a key that appears twice is a bug in
    the table itself and the last one wins, which is what the screen sees."""
    table = {}
    for path in STRING_FILES:
        src = open(path, encoding="utf-8").read()
        for m in re.finditer(r'"([a-zA-Z0-9._]+)"\s*:\s*\{([^}]*)\}', src):
            key, body = m.group(1), m.group(2)
            en = re.search(r'en:\s*"((?:[^"\\]|\\.)*)"', body)
            ko = re.search(r'ko:\s*"((?:[^"\\]|\\.)*)"', body)
            if en and ko:
                table[key] = (en.group(1), ko.group(1))
    return table


def norm(s: str) -> str:
    """The comparison form: no spaces. See the module docstring, section [2]."""
    return re.sub(r"\s+", "", s)


def main() -> int:
    table = load_table()
    print(f"[table] {len(table)} entries from {len(STRING_FILES)} files")

    print("\n[1] the table still holds the labels the docs quote")
    docs_text = {d: open(d, encoding="utf-8").read() for d in DOCS}
    for key, quoted in QUOTED:
        if key not in table:
            check(False, f"{key} is still in the string table (the docs quote {quoted!r})")
            continue
        en, _ = table[key]
        if en != quoted and not en.startswith(quoted):
            check(False, f"{key} still reads {quoted!r} (the table now says {en!r})")
            continue
        if not any(quoted in t for t in docs_text.values()):
            check(False, f"{quoted!r} is still quoted in a doc (the list is stale)")
            continue
        check(True, f"{key} reads {en!r}")

    print("\n[2] the pair agrees where the English doc quotes a label")
    # One English value can sit in several entries with different Korean values.
    by_en: dict[str, set[str]] = {}
    for en, ko in table.values():
        by_en.setdefault(en, set()).add(ko)

    for d in DOCS:
        ko_doc = d[:-3] + ".ko.md"
        if not os.path.exists(ko_doc):
            check(False, f"{os.path.relpath(ko_doc, HERE)} exists beside {os.path.relpath(d, HERE)}")
            continue
        en_text = docs_text[d]
        ko_norm = norm(open(ko_doc, encoding="utf-8").read())
        rel = os.path.relpath(d, HERE)
        drifted = []
        for en, kos in sorted(by_en.items()):
            if f'"{en}"' in en_text and not any(norm(k) in ko_norm for k in kos):
                drifted.append(f"{en!r} → {sorted(kos)!r}")
        check(not drifted, f"{rel} / {os.path.basename(ko_doc)} agree"
              + ("" if not drifted else "  (missing: " + "; ".join(drifted) + ")"))

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
