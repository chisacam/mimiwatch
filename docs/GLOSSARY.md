# Channel glossary

Status: Accepted | Tier: local (user override) | Date: 2026-09-08

*[Korean](GLOSSARY.ko.md)*

## Context

The genre prompt (`GENRE_PROMPTS` in `translate.py`) picks the *character* of the
speech — live chatter and a tutorial want different registers — but it cannot
pin *proper nouns*. A streamer's name, a brand, a catchphrase get a different
translation on every line, because the model is never told what they are and
there is no list to consult. Each line is translated on its own, with a little
context from the neighbours, and a name that was "김의 방송" on line 10 is
"키 야크 채널" on line 90.

The glossary is the list. It is a set of `source → target` pairs, and it is
naturally **per channel**: a channel's proper nouns are stable across its
streams, and the unit the server already parses out of a URL is the channel
(`live.site_of`: YouTube `channel_id`, Twitch login name, empty elsewhere).

## Decision

1. **Storage.** A new `glossaries` table in `store.py`: `channel_key` (primary
   key), `name` (what the UI shows), `terms` (a JSON list of `{"from", "to"}`
   pairs), `updated_at`. A `channel_key` is `"{site}:{id}"` for an extracted
   channel (`youtube:UC…`, `twitch:login`) and `"manual:{name}"` for a named
   one. Glossaries do not go into `backends.json` (that file is the exclusive
   domain of `config.py`); they go into `data/mimiwatch.db` like all other user
   data.

2. **Association.** `/api/probe` already returns the channel it took from
   `live.site_of`, but the front end does not yet carry it into the job
   metadata — it will. The add flow comes to store the extracted channel (or a
   name the user typed when the extraction was empty — local files, other
   sites) in the job/session metadata. When translation starts, the loop looks
   the glossary up by that channel key. A job whose channel resolves to nothing matches nothing, and
   that is shown, not silent.

3. **Delivery: prompt only.** When terms exist, the translator appends a
   rendered block to the prompt, e.g. "Translate using this glossary as-is:
   A → B, C → D" (the final wording is a constant in `translate.py`, not a UI
   string). A backend that uses no prompt (M2M-100) ignores the block
   entirely. Today nothing in the UI says so — the genre hint describes what a
   prompt *does*, and the M2M fact is a code comment; the warning the engine
   hint and the glossary screen carry is new work (below, §Consequences). No
   post-hoc string replacement in v1 (considered and deferred; see below).

4. **Scope: future lines only.** Editing a glossary never re-scans lines that
   are already translated. The existing whole/partial re-translation job is
   the tool for applying the change — that path re-reads the current terms.

5. **UI.** The Engines dialog gets a "Glossary" button. It opens one screen:
   a list of channels (name, term count) and an editor — one `A → B` per
   line, the arrow typed or inserted by the UI, split on the first `→`.
   Saving publishes a `glossary` event on the bus, so windows already open
   refresh the list.

6. **API.** Two routes in the `server.py` routing table: `GET /api/glossaries`
   (list) and `PUT /api/glossaries` (body: `channel_key`, `name`, `terms`;
   creates or updates — an empty `terms` list deletes the row).

## Options considered

- **A — channel as a first-class entity (chosen).** A store table keyed by the
  extracted channel, matched to jobs automatically. What it gives up: a new
  table, two routes, one UI screen, and prompt tokens on every line while a
  glossary is attached.
- **B — per-video terms, "save as" channel.** The terms would live in the job
  metadata like the genre, with a copy action into the channel store.
  Rejected: the saving is a labour per video, which guts the goal — the whole
  point is that the second stream of a channel costs nothing.
- **C — the A store with manual association only.** Rejected: "pick the
  channel every time" removes the from-the-second-time property that makes the
  feature worth its size.
- **The delivery mechanism** (the original fork): prompt injection only
  (chosen), post-hoc string replacement, or both. Replacement would work on
  M2M-100, but it is weak against inflection and against CJK notation
  variants, and it is a second mechanism to keep consistent. The deferral
  costs nothing: the terms are stored as pairs, so a replacement pass can be
  added later without a data change.

## Consequences

- **Prompt tokens grow with the glossary.** Every line carries the block. The
  rendered block is capped at the first 100 terms (guardrail below); a
  glossary beyond the cap is still *saved* in full, only the rendered part is
  cut.
- **The M2M-100 user can open the glossary screen and it does nothing for
  them.** The warning must sit where the engine is chosen, next to the genre
  hint that already says prompts are prompt-backends only.
- **Channel-id stability is unmeasured.** Whether a YouTube video URL and the
  same stream re-broadcast resolve to the same `channel_id` has not been
  checked. The guardrail — the job progress line prints the applied glossary
  (name, term count) or "none" — is what makes a miss visible instead of
  silent.
- **Live sessions get it too, and it is not free.** Live cues are translated
  in `live.py`'s own per-utterance loop (`_translate_loop`), not in
  `jobs._translate_rows` — that loop's "three paths" are the three VOD paths,
  and `live.py` cannot import `jobs` (the import would be circular). Delivering
  the block to live means changing `live._translate`; the channel is known when
  the session starts, so the change is small. v1 covers both.

## Constraints for implementation

**Required**

- One translate loop stays for the VOD paths (`jobs._translate_rows`): the
  glossary is resolved there and passed to `mw_translate.build(spec, genre,
  glossary)`. The live path is a separate loop in `live.py`; the block reaches
  it through the same translator objects. No *new* translation path — the
  glossary is data the existing paths render.
- The add flow carries the channel (extracted, or the manual name) into the
  job/session metadata and into the `/api/transcribe` body.
- The new UI labels enter the string table, and the labels quoted in this doc
  are added to the curated list in `bench/docs_check.py`.
- New routes go into the `server.py` routing table (exact dict + prefix
  list), not an if-chain.
- UI strings go through the string table with English and Korean entries.
- The store follows `store.py` patterns (`_write`/`_rows`, migration in
  `init()`).
- Tests: no models, `isolated` fixture. Cover at least: empty glossary
  produces byte-identical prompts to today's; the block renders when terms
  exist; channel-key derivation from `live.site_of` output (youtube, twitch,
  file, other) and from a manual name; store round-trip; the two routes
  registered; the live cue prompt carries the block when terms exist; the
  transcribe request body carries the channel key.
- A `docs/GLOSSARY.ko.md` companion, edited with this file.
- No `ext/` changes in v1 — the extension does not read or write glossaries.

**Forbidden**

- Post-hoc string replacement of translation output in v1.
- Glossary storage in `backends.json` or through `config.py`.
- Modifying the existing genre prompt wording (the glossary is appended, not
  woven in).
- Re-scanning already-translated lines when a glossary changes.

**Guardrails**

- The empty-glossary path must produce byte-identical prompt bytes to the
  pre-change behaviour — that comparison is the regression test.
- The rendered block is capped at the first 100 terms.
- The job progress shows the applied glossary (name, term count) or "none".
- Saving or deleting a glossary publishes a `glossary` event on the bus.

## Verification

- **Unit (no models).** Store round-trip; key derivation for a YouTube video
  URL, a Twitch URL, a local file (manual name) and an unknown site; rendering
  — block present with terms, byte-identical without; `build` on an M2M-style
  spec ignores the terms.
- **Mock integration.** An OpenAI-compatible mock records its requests; a VOD
  job with a three-term glossary carries the block in every prompt; a
  re-translation job after an edit carries the new terms.
- **Manual.** A real stream with a five-term glossary: one proper noun stays
  stable across 50+ lines. An M2M-100 run shows the does-not-apply warning.
  Editing a term and re-translating applies it.
- **Docs.** The new UI labels quoted in this doc are in the string table and
  in the curated list of `bench/docs_check.py`, so the gate checks the pair.