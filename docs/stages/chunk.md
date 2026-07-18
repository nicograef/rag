# Stage contract: chunk

> Code: [`src/rag/chunk/`](../../src/rag/chunk/__init__.py) ·
> Roadmap: [Phase 2 — Structure-aware chunking](../roadmap.md) ·
> Theory: [chunking](../theory/chunking.md)

Turns each article's Markdown under `data/corpus/` into one JSONL file of retrieval-ready
chunk records in `data/chunks/`. Third stage of the offline ingestion pipeline: it splits
each article into retrieval units along its Wikipedia section structure, carrying the
metadata later stages need for filtering and citations.

## Invocation

```sh
make chunk                      # wraps:
uv run python -m rag.chunk      # options: --corpus-dir data/corpus --chunks-dir data/chunks
```

## Input

One `data/corpus/<slug>.md` per article, as produced by [convert](convert.md). Chunk parses
this controlled Markdown by hand — no YAML or Markdown library — exactly as convert writes
it:

- **Front matter** (`key: "value"` lines between `---` fences). Chunk consumes `slug`,
  `source_title`, `source_url`, and `fetched_at`.
- **Headings** — the H1 article title is not a unit. Every `##` heading opens a
  **section**, running to the next `##` heading and including any `###`-and-deeper
  subheadings and their text — those are section content, not section boundaries. The
  heading trail above a top-level section is only the article title, so `section_path` is
  always empty for these shallow articles.
- **Body** — the blank-line-separated blocks between a `##` heading and the next one.

Chunk reads only this directory — it never performs network I/O.

## Output

One `data/chunks/<slug>.jsonl` per article: one JSON chunk record per line
(`json.dumps(..., ensure_ascii=False)`), the file ending with a trailing newline. A section
whose `text` fits `max_chars` (default 1200) becomes exactly one chunk; a section over the
max is **split** into ordered parts (see
[Splitting oversized sections](#splitting-oversized-sections)); consecutive tiny sections
are **merged** into one chunk (see [Merging tiny sections](#merging-tiny-sections)); an
empty-body section produces no chunk. The record schema — **the interface the embed stage
consumes** — is:

| Field          | Type            | Value                                                            |
| -------------- | --------------- | ------------------------------------------------------------------------ |
| `id`           | string          | `<slug>#<section>` for a whole section; `<slug>#<section>#<n>` (1-based) for a split part; a **merged** chunk keys on its **first** covered section (`<slug>#<firstsection>`) — the chunk's stable structural identity; unique within an article (a collision raises) |
| `text`         | string          | The section's own heading line (plain, `#` stripped), then a blank line, then its body (including any subsections); just the heading if the body is empty. A **merged** chunk joins its covered sections' texts with a blank line |
| `slug`         | string          | Source identifier — the article slug, from front matter          |
| `source_title` | string          | The article title, from front matter                             |
| `section`         | string          | Section identifier — the `##` heading text (e.g. `History`); a **merged** chunk lists its covered sections joined `, ` |
| `section_path` | list of strings | Ancestor section headings, outermost first; always `[]` — `##` sections sit directly under the H1 in these shallow articles |
| `citation`     | string          | Human citation label — `<source_title> — <section>` (e.g. `Arsenal F.C. — History`); a **merged** chunk lists all covered sections |
| `source_url`   | string          | From front matter — provenance / data lineage                    |
| `fetched_at`   | string          | From front matter — provenance / data lineage                    |
| `part`         | object or null  | `null` for a whole section (incl. a merged chunk); `{ "index": n, "total": m }` for part `n` of `m` of a split section |

The `text` carries the section's own heading only; `source_title` and `section_path` stay
in metadata and are not prepended.

## Splitting oversized sections

A section whose `text` exceeds `max_chars` is divided into ordered parts; every part's
`text` still leads with the section's plain heading line, so a retrieved part is
self-identifying. Size is measured on the final `text` (heading + overlap + content).

The body is segmented into **blocks** (maximal runs of non-blank lines) grouped into
**segments**: a new segment opens at a block whose first line is a `###`-or-deeper
subheading; blocks before the first subheading (the section's own intro) form the first
segment. A section with no subheading yields one segment holding every block.

- **Segment groups with one-segment overlap.** Whole segments are greedily accumulated
  into a part while heading + overlap + content stays `≤ max_chars`. Each non-first part
  repeats the previous part's **final segment** as overlap, so boundary context survives
  with no mid-sentence cut. The size invariant wins over overlap: if heading + overlap +
  the first content segment would exceed the max, the overlap is dropped for that part;
  each part always carries at least one content segment (progress guaranteed).
- **Recursive-character fallback.** A single segment that alone (with the heading) exceeds
  the max is split on an ordered separator list — `"\n\n"` (paragraph) → `"\n"` (line) →
  `". "` (sentence) → `" "` (word) — into pieces `≤ max_chars`, separators preserved so the
  pieces concatenate back to the segment verbatim. Consecutive pieces overlap by a fixed
  character window of `max_chars // 10` characters (120 at the default max). Each piece
  still leads with the heading.

A split part is keyed `id = <slug>#<section>#<n>` (1-based) with `part = { "index": n, "total":
m }`. **No silent text loss:** concatenating a split section's parts minus the duplicated
overlap reproduces the section's full body verbatim — asserted in the tests for both the
segment-group and recursive-character cases.

## Merging tiny sections

After the per-section chunking above, a second pass merges consecutive tiny sections so
retrieval gets fewer, fuller chunks. A section is a **merge candidate** iff it is **whole**
(its `text` fits `max_chars` — it was not split) **and** shorter than `merge_floor` (default
400). Merge candidates are gathered into one open group in document order and combined into
a single chunk:

- A section **flushes** the open group when it is an **empty-body (skipped) section**, a
  **split** section, or an **above-floor** whole section.
- After a candidate joins, the group flushes once its combined `text` reaches
  `merge_floor` (a fresh group starts at the next candidate). A candidate that would push
  the merged `text` over `max_chars` flushes the group first — **the max rule wins over the
  floor** even when `floor > max`.

Flushing a group of **one** emits a normal single whole chunk (`id = <slug>#<section>`, `section`,
`citation = <source_title> — <section>`, `part = null`) — byte-identical to the pre-merge
output, so a lone sub-floor section with no eligible neighbour is unchanged. Flushing a
group of **two or more** emits one merged chunk: its `text` joins the covered sections'
texts with a blank line, its `id` keys on the **first** covered section, `section` lists the
covered sections joined `, `, `citation` lists the same sections after `<source_title> — `,
and `part` is `null`.

## Guarantees

- **Deterministic pure transform.** Same corpus input → byte-identical JSONL, asserted in
  golden-file tests (`tests/test_chunk.py`).
- **No silent text loss.** Every character of a section's body lands in at least one
  chunk; the only intentional duplication is split **overlap** (one repeated segment, or a
  fixed character window). Concatenating a split section's parts minus that overlap
  reproduces the section's full body verbatim. Merging only concatenates whole sections
  (each section's full text is preserved, none dropped or reordered). Any construct the
  chunker cannot place raises `ChunkError` instead of dropping content.
- **Markdown-only structure.** Section and subsection boundaries come from ATX heading
  depth alone — the chunker carries no domain-specific citation parsing.
- **No network.** The stage is a local file transform.

## Failure behaviour

Per-article isolation, like convert: an article that cannot be chunked is reported on
stderr (`✗ <slug>: <error>`) and produces no output file; the remaining articles still
chunk. `ChunkError` covers a duplicate chunk id within an article, malformed front matter,
and a body not starting with an H1 article title. The exit code is non-zero if any article
failed. When `--corpus-dir` is missing or empty, chunk exits non-zero with a hint to run
`make convert` first.

## Verification

**Size pinning (2026-07-17):** `max_chars` is pinned to **1200** and `merge_floor` to
**400**, validated against `bge-small-en-v1.5`'s own tokenizer (the embed stage's model)
over the fetched 20-club corpus: the densest chunk text runs ≈ **2.44 chars/token**, so
1200 chars is ≤ ~492 tokens even in the worst case — a hard guarantee below the model's
**512-token** window. The observed maximum across the whole corpus was **375 tokens**.
`make chunk` over all 20 clubs produced **1333 chunks**, none over 512 tokens. The embed
token-guard is the hard backstop if a future article is denser still.

## Downstream consumers

**embed** ([contract](embed.md)) reads `data/chunks/*.jsonl`: `id` and `text` are the
fields it consumes to build each chunk's embedding record.
