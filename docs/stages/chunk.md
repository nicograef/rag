# Stage contract: chunk

> Code: [`src/rag/chunk/`](../../src/rag/chunk/__init__.py) ·
> Roadmap: [Phase 2 — Structure-aware chunking](../roadmap.md)

Turns each law's Markdown under `data/corpus/` into one JSONL file of retrieval-ready
chunk records in `data/chunks/`. Third stage of the offline ingestion pipeline: it splits
the corpus into retrieval units without destroying legal structure, carrying the metadata
later stages need for filtering and citations.

## Invocation

```sh
make chunk                      # wraps:
uv run python -m rag.chunk      # options: --corpus-dir data/corpus --chunks-dir data/chunks
```

## Input

One `data/corpus/<slug>.md` per law, as produced by [convert](convert.md). Chunk parses
this controlled Markdown by hand — no YAML or Markdown library — exactly as convert writes
it:

- **Front matter** (`key: "value"` lines between `---` fences). Chunk consumes `slug`,
  `abbreviation` (→ the `law` field), `source_url`, and `fetched_at`; it ignores `title`
  and `builddate`.
- **Headings** — the H1 law title is skipped; every deeper ATX heading is either a
  **section** (Buch/Teil/Abschnitt, empty-bodied) or a **leaf norm unit** (§, Art,
  Präambel, Eingangsformel, Schlussformel, Anlage/Anhang). A heading is a section iff the
  *next* heading in document order is deeper; otherwise it is a leaf norm unit. Heading
  text is `enbez` alone or `enbez — titel` (separator ` — `).
- **Body** — the blank-line-separated blocks between a leaf heading and the next heading.

Chunk reads only this directory — it never performs network I/O.

## Output

One `data/chunks/<slug>.jsonl` per law: one JSON chunk record per line
(`json.dumps(..., ensure_ascii=False)`, so German glyphs like `§`, `ä` stay readable),
the file ending with a trailing newline. A norm unit whose `text` fits `max_chars` (default
2000) becomes exactly one chunk; a unit over the max is **split** into ordered parts (see
[Splitting oversized units](#splitting-oversized-units)); consecutive tiny units under the
same section are **merged** into one chunk (see [Merging tiny units](#merging-tiny-units));
empty-body `(weggefallen)` units produce no chunk. The record schema — **the interface the
Phase 3 embed/load stages consume** — is:

| Field          | Type            | Value                                                            |
| -------------- | --------------- | ---------------------------------------------------------------- |
| `id`           | string          | `<slug>#<unit>` for a whole unit; `<slug>#<unit>#<n>` (1-based) for a split part; a **merged** chunk keys on its **first** covered unit (`<slug>#<firstunit>`) — the chunk's stable structural identity; unique within a law (a collision raises) |
| `text`         | string          | The unit's own heading line (plain, `#` stripped), then a blank line, then its body; just the heading if the body is empty. A **merged** chunk joins its covered units' texts with a blank line |
| `slug`         | string          | Source identifier — the site slug, from front matter             |
| `law`          | string          | Law abbreviation (e.g. `KassenSichV`), from front-matter `abbreviation` |
| `unit`         | string          | Norm-unit identifier — the heading's `enbez` (e.g. `§ 1`, `Art 1`, `Anlage 2`, `Eingangsformel`); a **merged** chunk lists its covered units joined `, ` (e.g. `§ 1, § 2`) |
| `section_path` | list of strings | Ancestor section headings (Buch/Teil/Abschnitt), outermost first; `[]` for flat or appendix units. A merged chunk's covered units all share this path |
| `citation`     | string          | Human citation label — `<unit> <law>` (e.g. `§ 1 KassenSichV`); a **merged** chunk lists all covered units (e.g. `§ 1, § 2 StrukturG`) |
| `source_url`   | string          | From front matter — provenance / data lineage                    |
| `fetched_at`   | string          | From front matter — provenance / data lineage                    |
| `part`         | object or null  | `null` for a whole unit (incl. a lone atomic oversized table and a merged chunk); `{ "index": n, "total": m }` for part `n` of `m` of a split unit |

The `text` carries the unit's own heading only; the `section_path` and `law` name stay in
metadata and are **not** prepended (contextual enrichment is deferred to Backlog 6).

## Splitting oversized units

A unit whose `text` exceeds `max_chars` is divided into ordered parts; every part's `text`
still leads with the unit's plain heading line, so a retrieved part is self-identifying.
Size is measured on the final `text` (heading + overlap + content).

The body is segmented into **blocks** (maximal runs of non-blank lines) grouped into
**Absätze**: a new Absatz opens at a block whose first line matches the paragraph marker
`(1)`, `(2a)`, … (regex `^\(\d+[a-z]?\)`); unmarked and leading blocks attach to the current
Absatz. A body with no marker yields one Absatz per block. A block is an **atomic table**
(never split internally) when every line is a pipe row (`| … |`) or it is a fenced
```` ```table ```` block.

- **Absatz groups with one-Absatz overlap.** Whole Absätze are greedily accumulated into a
  part while heading + overlap + content stays `≤ max_chars`. Each non-first part repeats the
  previous part's **final Absatz** as overlap, so boundary context survives on whole-Absatz
  lines with no mid-sentence cut. The size invariant wins over overlap: if heading + overlap
  + the first content Absatz would exceed the max, the overlap is dropped for that part; each
  part always carries at least one content Absatz (progress guaranteed).
- **Recursive-character fallback.** A single Absatz that alone (with the heading) exceeds the
  max is split on an ordered separator list — `"\n\n"` (paragraph) → `"\n"` (line) → `". "`
  (sentence) → `" "` (word) — into pieces `≤ max_chars`, separators preserved so the pieces
  concatenate back to the Absatz verbatim. Consecutive pieces overlap by a fixed character
  window of `max_chars // 10` characters (e.g. 200 at the default max). Each piece still leads
  with the heading. (Real legal enumerations are one blank-line-free block, so this splits
  them at line boundaries.)
- **Atomic oversized table.** A single table Absatz that alone exceeds the max is emitted
  **whole** — the one chunk allowed over the max — and a warning is logged to stdout
  (`  ! oversized table in <unit>: <n> chars (kept whole)`). Splitting a table would corrupt
  it. (Real case: UStG `Anlage 2`, ~13k chars.)

A split part is keyed `id = <slug>#<unit>#<n>` (1-based) with `part = { "index": n,
"total": m }`. **No silent normative-text loss (tightened):** concatenating a split unit's
parts minus the duplicated overlap reproduces the unit's full body verbatim — asserted in the
tests for both the Absatz-group and recursive-character cases.

## Merging tiny units

After the per-unit chunking above, a second pass merges consecutive tiny units so retrieval
gets fewer, fuller chunks. A unit is a **merge candidate** iff it is **whole** (its `text`
fits `max_chars` — it was not split) **and** shorter than `merge_floor` (default 500). Merge
candidates are gathered into one open group in document order and combined into a single
chunk:

- A unit **flushes** the open group when it is an **empty-body (skipped) unit**, a **split**
  unit, an **above-floor** whole unit, or a candidate whose **`section_path` differs** from
  the group's. Merging never crosses a section boundary, and a **skipped `(weggefallen)`
  unit is a boundary** — units separated by a repealed norm are not adjacent (so GG's `Art 1`
  and `Art 3` do not merge across a repealed `Art 2`).
- After a candidate joins, the group flushes once its combined `text` reaches `merge_floor`
  (a fresh group starts at the next candidate). A candidate that would push the merged `text`
  over `max_chars` flushes the group first — **the max rule wins over the floor** even when
  `floor > max`.

Flushing a group of **one** emits a normal single whole chunk (`id = <slug>#<unit>`, `unit`,
`citation = <unit> <law>`, `part = null`) — byte-identical to the pre-merge output, so a lone
sub-floor unit with no eligible neighbour is unchanged. Flushing a group of **two or more**
emits one merged chunk: its `text` joins the covered units' texts with a blank line, its
`id` keys on the **first** covered unit, `unit` lists the covered units joined `, `,
`citation` is that joined unit plus the law, and `part` is `null`. (Real case: GG's `Art 1`
and `Art 2` merge to `gg#Art 1`, `unit = "Art 1, Art 2"`.)

## Guarantees

- **Deterministic pure transform.** Same corpus input → byte-identical JSONL, asserted in
  golden-file tests (`tests/test_chunk.py`).
- **No silent normative-text loss.** Every character of a unit's body lands in at least one
  chunk; the only intentional duplication is split **overlap** (one repeated Absatz, or a
  fixed character window). Concatenating a split unit's parts minus that overlap reproduces
  the unit's full body verbatim. Merging only concatenates whole units (each unit's full text
  is preserved, none dropped or reordered). Any construct the chunker cannot place raises
  `ChunkError` instead of dropping content (e.g. a section heading that carries a non-empty
  body).
- **No network.** The stage is a local file transform.

## Current coverage

The chunk stage is complete (roadmap Phase 2, Slices 1–3): parse plus one chunk per norm
unit, the **splitter** (Absatz groups with one-Absatz overlap, a recursive-character fallback
for a single overlong Absatz, an atomic oversized table logged and kept whole), and the
**merge pass** (consecutive sub-`merge_floor` same-section whole units combined). All four MVP
laws chunk end to end: AO and UStG (long §§ split, UStG's `Anlage 2`/`3`/`4` tables kept
atomic), GG (198 `Art` units plus `Präambel`, `Eingangsformel`, `Anhang EV`; small adjacent
`Art`s merge, e.g. `Art 1, Art 2`), and KassenSichV. Empty-body `(weggefallen)` units are
skipped. Golden-file verified against `kassensichv` (flat, all units under the max), the split
fixtures `splitg`/`absatzg`/`tableg` (pinned at a small `max_chars`), and the merge fixtures
`strukturg` (nested sections, §§ merge, deeper-section unit stands alone), `artg` (`Art` units
plus `Präambel`/`Eingangsformel`/`Anhang EV`, a repealed `Art` as a boundary), and `tabelleng`
(two `Anlage` tables merged, an above-floor `Anlage` alone) at the default thresholds.

**Spot-check (2026-07-12)** — one representative unit per law, emitted chunk vs the official
gesetze-im-internet.de rendering:

- **§ 3 AO** (`ao_1977#§ 3#1`, split part 1/2) — Absätze (1) and (2) are character-identical to
  <https://www.gesetze-im-internet.de/ao_1977/__3.html>. This is also the unit convert
  spot-checked (see [convert.md → Current coverage](convert.md#current-coverage)), so the whole
  convert → chunk path is anchored on it.
- **Art 1 GG** (`gg#Art 1`, merged `Art 1, Art 2`) — the `Art 1` portion (paragraphs (1)–(3)) is
  verbatim against <https://www.gesetze-im-internet.de/gg/art_1.html>; the merge appends `Art 2`
  after a blank line without altering `Art 1`.
- **§ 1 KassenSichV** (`kassensichv#§ 1`, whole) — Absätze (1)/(2) and the numbered list 1.–6.
  match <https://www.gesetze-im-internet.de/kassensichv/__1.html> verbatim.
- **Anlage 2 UStG** (`ustg_1980#Anlage 2`, atomic oversized table, 13011 chars, `part = null`) —
  the `Lfd. Nr.` header and entry 1's sub-items (a) (weggefallen), b) Maultiere `aus Position
  0101`, c) Hausrinder `aus Position 0102`, …) match
  <https://www.gesetze-im-internet.de/ustg_1980/anlage_2.html>; the table is kept whole.

## Failure behaviour

Per-law isolation, like convert: a law that cannot be chunked is reported on stderr
(`✗ <slug>: <error>`) and produces no output file; the remaining laws still chunk. The
exit code is non-zero if any law failed. When `--corpus-dir` is missing or empty, chunk
exits non-zero with a hint to run `make convert` first.

## Downstream consumers

**embed** and **load** (Phase 3) read `data/chunks/*.jsonl`. The record schema above is the
interface they consume: `text` is embedded, `id` keys the idempotent upsert, and
`slug`/`law`/`unit`/`section_path`/`citation`/`source_url`/`fetched_at` become the row's
filter and citation metadata.
