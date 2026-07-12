# Plan: Roadmap Phase 2 — Structure-aware chunking

> Source PRD: [../prds/prd-rag-playbook.md](../prds/prd-rag-playbook.md) ·
> Roadmap phase: [../roadmap.md](../roadmap.md) "Phase 2 — Structure-aware chunking"

## Goal

Land the **chunk** stage: split the Markdown corpus (`data/corpus/<slug>.md`, produced by
[convert](../stages/convert.md)) into retrieval units without destroying legal structure.
Chunk by **§ (Paragraph)** as the natural semantic unit; split oversized §§ by **Absatz**
with **overlap**; **merge** tiny units. Each chunk carries the metadata later stages need for
filtering and citations (law, unit, section path, source URL, fetch date) and a stable
identity Phase 3's idempotent upsert can key on. Output is one inspectable JSONL artifact per
law under `data/chunks/`.

Per [AGENTS.md](../../AGENTS.md) rule 5, the phase is done only with all five deliverables:
code + tests + theory chapter (`docs/theory/chunking.md`) + stage contract
(`docs/stages/chunk.md`) + README status with a verification date. The work is broken into
four **slices** ("Phase" always means the roadmap phase).

## Architectural decisions

Durable decisions that apply across all slices:

- **Stage = subpackage, invoked via `python -m`** (same shape as fetch/convert): `src/rag/chunk/`
  with a `__main__.py` (argparse), run as `uv run python -m rag.chunk`, wrapped by Makefile
  target `make chunk`. Options: `--corpus-dir data/corpus` (input) and `--chunks-dir data/chunks`
  (output). This invocation shape lands in the README and the stage contract and must not change.

- **No new runtime dependency.** Chunk reads convert's own controlled Markdown — YAML front
  matter written as `key: "value"` lines, ATX headings (`#`–`######`), paragraphs, plain-text
  lists, and pipe/fenced tables — and parses it by hand, mirroring how convert *writes* that
  format by hand. Output is emitted with stdlib `json`. No YAML or Markdown library is added
  (consistent with the "no frameworks, plain libraries, simple over clever" rule).

- **Output artifact**: `data/chunks/<slug>.jsonl` — one JSON chunk-record per line, one file
  per law (mirrors `data/corpus/<slug>.md`, keeping the per-law isolation fetch and convert
  use). Guarantees, mirroring convert:
  - **Deterministic pure transform** — same corpus input → byte-identical JSONL, asserted in
    golden-file tests.
  - **No silent normative-text loss** — every character of a unit's normative body lands in at
    least one chunk; the only intentional duplication is Absatz **overlap**. Any construct the
    chunker cannot place raises `ChunkError` instead of dropping content.
  - **No network** — a local file transform.

- **Chunk-record schema** (the interface Phase 3 consumes; corpus-neutral field names per the
  PRD's chunk-record contract — *source identifier, section path, citation label* — with
  German-law values as the first instantiation):

  | Field         | Type            | Meaning                                                                 |
  | ------------- | --------------- | ----------------------------------------------------------------------- |
  | `id`          | string          | Stable structural identity (see below)                                  |
  | `text`        | string          | The chunk's retrievable text: the unit's own heading line + its body    |
  | `slug`        | string          | Source identifier — the site slug (e.g. `ao_1977`), from front matter   |
  | `law`         | string          | Law abbreviation (e.g. `AO`, `GG`), from front matter                    |
  | `unit`        | string          | Norm-unit identifier — the heading's `enbez` (e.g. `§ 3`, `Art 1`, `Anlage 2`) |
  | `section_path`| list of strings | Ancestor section headings (Buch/Teil/Abschnitt), outermost first        |
  | `citation`    | string          | Human citation label (e.g. `§ 3 AO`, `Art 1 GG`)                        |
  | `source_url`  | string          | From front matter — provenance / data lineage                           |
  | `fetched_at`  | string          | From front matter — provenance / data lineage                           |
  | `part`        | object or null  | `{ "index": n, "total": m }` when the unit was split; `null` otherwise  |

- **Chunk identity** (`id`): `<slug>#<unit>` for a whole unit (e.g. `ao_1977#§ 3`); split parts
  append `#<n>` (1-based, e.g. `ao_1977#§ 3#1`, `ao_1977#§ 3#2`); a merged chunk keys on its
  **first** covered unit (e.g. `strukturg#§ 4`). `id`s are asserted unique within a law — a
  collision raises rather than silently overwrites. This is the **final** identity scheme, not a
  placeholder: Phase 3 upserts `ON CONFLICT (id)`; Backlog 13 (incremental ingestion) later adds
  a content-hash column in its own migration, computed from the chunk text at load time, without
  reworking this stage. (No `content_hash` is emitted here — its first consumer is Backlog 13, so
  emitting it now would be premature generality.)

- **Size policy** (character counts — the embedding tokenizer arrives in Phase 3; German legal
  text runs ~4–6 chars/token, so these are a deliberate proxy): **max 2000 chars** (a unit whose
  `text` exceeds this is split) and a **merge floor of 500 chars** (units below it are candidates
  to merge). 2000 chars ≈ 400–500 tokens — safe against a 512-token embedding model (e.g. the
  multilingual-e5 candidate) with margin, and matches the AGENTS.md code-style example
  (`max_chars: int = 2000`).

- **Split policy** (oversized units): split into **Absatz-groups**, each ≤ max, with
  **one-Absatz overlap** — each subsequent chunk repeats the previous chunk's final Absatz, so
  boundary context survives on whole-Absatz lines with no mid-sentence cuts (the *Sliding window /
  overlap* concept). A single Absatz that alone exceeds max falls back to **recursive character
  splitting** (an ordered separator list: paragraph break → sentence → word) with character-level
  overlap. A **table is atomic** — never split inside; a table that alone exceeds max stays a
  single oversized chunk, flagged in the log (splitting a table corrupts it).

- **Merge policy** (tiny units): consecutive units below the merge floor that share the **same
  `section_path`** combine into one chunk until the accumulated text reaches the floor; merging
  **never crosses a section boundary**, never absorbs a split part, and never pushes a chunk over
  the max. The merged chunk records every covered unit (its `citation` lists them; `unit` is the
  covered range).

- **Non-§ unit handling**: GG's `Art` units are treated exactly like `§`; `Präambel`,
  `Eingangsformel`, `Schlussformel`, and `Anlage`/`Anhang` units are chunked as normal units under
  the same split/merge rules; **`(weggefallen)` repealed norms with an empty body produce no
  chunk** (a norm is skipped only when its *entire* body is empty — an individual `(7) (weggefallen)`
  Absatz inside an otherwise-populated § is ordinary content and is kept).

- **Text vs metadata boundary** (keeps Phase 2 distinct from Backlog 6): the chunk `text` is the
  unit's **own** heading line (plain text, `#` markers stripped) followed by its body. The
  `section_path` (Buch/Abschnitt) and `law` name stay in **metadata only** and are **not**
  prepended to `text` — prepending ancestor/law context is *contextual enrichment*, deferred to
  Backlog 6.

## Key models

- **`Chunk`** — a frozen dataclass holding the record above; serialized one-per-line to JSONL.
- **`NormUnit`** — the intermediate parse record for one leaf heading: its `enbez`, its
  `section_path`, and its body text. A norm unit is a **leaf ATX heading** (one with no
  deeper-level child heading before the next same-or-shallower heading); its ancestor headings form
  the `section_path`. This leaf rule is what distinguishes GG's `## Eingangsformel` (a leaf → a
  unit) from `## I. — Die Grundrechte` (has `###` children → a section), which sit at the same
  heading depth.

## Inventory

- `src/rag/convert/__init__.py` — the upstream stage; `render_markdown()` defines the exact
  heading and body shapes chunk parses (front matter, section vs norm-unit headings, Absatz
  paragraphs, list/table blocks). `_normalize()` shows the whitespace convention the output uses.
- `docs/stages/convert.md — "Output" / "Downstream consumers"` — the input contract: front-matter
  fields and heading-per-norm-unit structure chunk relies on.
- `docs/plans/plan-fetch-convert.md` — the slice shape, contract style, and definition-of-done
  pattern this plan follows.
- `Makefile — fetch / convert targets` — pattern for the new `chunk` target (Pipeline section).
- `tests/test_convert.py — golden-file + determinism tests` — the testing pattern chunk mirrors
  (parametrized golden fixtures, byte-exact comparison, determinism assertion).
- `tests/fixtures/corpus/*.md` — existing convert **outputs** (`kassensichv`, `strukturg`, `artg`,
  `tabelleng`); reusable as chunk **inputs** where they exercise a case, augmented by chunk-specific
  fixtures for oversized-split and merge.
- `docs/concepts.md — "Chunking" rows` — Fixed-size, Recursive character splitting, Sliding
  window / overlap, Structure-aware: their `Place` column references the chunking chapter and is
  hyperlinked to it once it exists (Slice 4).
- `README.md — status table, quick start, pipeline overview, structure table` — updated in Slice 4.
- `AGENTS.md — Commands table` — gains a `make chunk` row in Slice 4.
- `docs/roadmap.md — "Phase 2"` heading — status flips to ✅ in Slice 4.

## Resolved decisions

Clarified with the maintainer during planning:

- **Chunk identity**: structural key only — `id = <slug>#<unit>[#<n>]`. No content hash in the
  Phase 2 record; Backlog 13 adds it at its own boundary, computed from the chunk text.
- **Size thresholds**: max 2000 chars (split above), merge floor 500 chars — chosen to stay safe
  against a 512-token embedding model and to keep most §§ whole.
- **Overlap**: one-Absatz overlap between split chunks; character-level overlap for the
  single-oversized-Absatz fallback.
- **Output**: JSONL, one record per line, one file per law — `data/chunks/<slug>.jsonl`.
- **Non-§ units**: `Art` like `§`; Präambel/Eingangsformel/Schlussformel and Anlage/Anhang chunked
  as units; empty-body `(weggefallen)` norms skipped; tables atomic.

## Open questions / Risks

- **Char thresholds are a token proxy.** They are re-validated against the chosen model's actual
  tokenizer in Phase 3 and adjusted there if needed (recorded as a dated decision) — this is the
  expected hand-off, not rework debt, because chunk owns only the character-level policy.
- **The recursive-character fallback is rarely exercised by the MVP corpus** — most oversized §§
  have several Absätze, so Absatz-splitting suffices. It is still implemented and tested with a
  synthetic single-oversized-Absatz fixture so the guarantee ("no unit is ever emitted over max
  unless it is one atomic table") holds for any future corpus.
- **A table larger than max stays one oversized chunk** (atomic). Acceptable and logged; the
  alternative (cutting a table) would corrupt it. The MVP corpus's largest table (UStG Anlage 2) is
  checked to confirm whether this case actually arises.

---

## Slice 1: Chunk skeleton — parse + one chunk per norm unit

**User stories**: 1 (run landed stages on a dev machine), 3 (single-responsibility stage with a
documented contract), 4 (contracts state exactly which fields downstream stages require).

### Context

- `src/rag/chunk/` — new subpackage with `__main__.py`; created here.
- `docs/stages/convert.md — "Output"` — the front-matter and heading shapes to parse.
- `data/corpus/kassensichv.md` — the walking-skeleton input: flat (no section headings), small
  units, includes the non-§ units `Eingangsformel`/`Schlussformel`, nothing over the max size.
- `tests/fixtures/corpus/kassensichv.md` — reusable as the Slice-1 golden input.
- `docs/stages/chunk.md` — the stage contract; created here (record schema + by-unit behavior),
  extended as later slices land split/merge.
- `Makefile` — new `chunk` target.

### What to build

The minimal honest chunker end to end: parse one law's corpus Markdown into its front matter and
its ordered `NormUnit` list (leaf heading + `section_path` + body), then emit one `Chunk` per unit
to `data/chunks/<slug>.jsonl` with the full record schema. `text` is the unit's own heading line
(plain) plus its body; `slug`/`law`/`source_url`/`fetched_at` come from the front matter; `unit`,
`section_path`, and `citation` come from the heading structure; `id = <slug>#<unit>`; `part` is
`null`. Empty-body `(weggefallen)` units emit no chunk. Split and merge are **not** built yet —
every unit becomes exactly one chunk regardless of size (a unit over the max passes through whole,
to be split in Slice 2). `id` uniqueness within a law is asserted (collision raises).

The `docs/stages/chunk.md` contract documents invocation, input (corpus Markdown + which
front-matter fields it consumes), the output JSONL artifact and the full record schema, the
deterministic-pure-transform and no-silent-loss guarantees, failure behaviour (per-law isolation
like convert), and what downstream (embed/load) consumes.

Tests mirror convert's golden-file pattern: `tests/fixtures/corpus/kassensichv.md` → a pinned
`tests/fixtures/chunks/kassensichv.jsonl`, compared byte-exactly; running chunk twice on the same
input is asserted byte-identical.

### Acceptance criteria

- [ ] `make chunk` turns `data/corpus/kassensichv.md` into `data/chunks/kassensichv.jsonl` with one
      record per norm unit (§ 1–§ 11 plus `Eingangsformel` and `Schlussformel`), each record
      carrying every schema field with correct values.
- [ ] `section_path` is `[]` for KassenSichV's flat units; `id` is `kassensichv#<unit>`; `citation`
      is `<unit> KassenSichV`; `part` is `null`.
- [ ] Golden-file test: fixture corpus → pinned JSONL, byte-exact; chunking twice on the same input
      is asserted byte-identical. Chunk reads only `data/corpus/` and performs no network I/O.
- [ ] `docs/stages/chunk.md` documents invocation, input, the output artifact and full record
      schema, guarantees, failure behaviour, and downstream consumers.
- [ ] `make check` is green; no new runtime dependency is added.

---

## Slice 2: Split oversized units by Absatz with one-Absatz overlap

**User stories**: 1, 3, 4.

### Context

- `src/rag/chunk/` — extended; no new packages.
- Architectural decisions — Size policy, Split policy: max 2000 chars; Absatz-groups with
  one-Absatz overlap; recursive-character fallback for a single oversized Absatz; tables atomic.
- `data/corpus/ao_1977.md`, `data/corpus/ustg_1980.md` — real oversized §§ (e.g. AO's long §§ with
  many Absätze) and substantive tables.
- `docs/stages/chunk.md` — extended with the split-and-overlap behaviour and the `part` field.

### What to build

The splitter. A unit whose `text` exceeds the max is divided into Absatz-groups, each ≤ max, where
each group after the first repeats the previous group's final Absatz as overlap. A single Absatz
that alone exceeds max falls back to recursive character splitting (paragraph → sentence → word)
with character-level overlap. Tables are treated as atomic blocks — never split inside; a table
that alone exceeds max stays one oversized chunk and is logged. Split parts get
`id = <slug>#<unit>#<n>` (1-based) and `part = {index, total}`; each part's `text` still leads with
the unit's heading line so a retrieved part is self-identifying. The no-silent-loss guarantee is
tightened: concatenating a unit's parts (minus the duplicated overlap) reproduces the unit's full
normative text.

Tests extend the golden pattern with targeted fixtures: one corpus file with a multi-Absatz
oversized § (asserts Absatz-group boundaries, the one-Absatz overlap, and `part` numbering) and one
with a single oversized Absatz (asserts the recursive-character fallback).

### Acceptance criteria

- [ ] A unit over 2000 chars is split into Absatz-groups each ≤ 2000, with each non-first chunk
      repeating the prior chunk's final Absatz; `part` and `#<n>` ids reflect the split; a unit at
      or under the max is untouched (still one chunk).
- [ ] A single Absatz over the max is split by the recursive-character fallback with character
      overlap; no chunk except an atomic oversized table ever exceeds the max.
- [ ] A table is never split internally; an oversized table is emitted whole and logged.
- [ ] Concatenating a split unit's parts minus overlap reproduces the unit's full body (no dropped
      or reordered text), asserted in tests; golden fixtures for both split cases pass; `make check`
      is green.

---

## Slice 3: Merge tiny units + full four-law corpus

**User stories**: 1, 3, 4.

### Context

- `src/rag/chunk/` — extended; no new packages.
- Architectural decisions — Merge policy, Non-§ unit handling.
- `data/corpus/gg.md` — `Art` units, `Präambel`, `Eingangsformel`, `Anhang EV`; `data/corpus/ao_1977.md`
  and `tests/fixtures/corpus/strukturg.md` — consecutive tiny units and empty-body `(weggefallen)`
  placeholders; `data/corpus/ustg_1980.md` and `tests/fixtures/corpus/tabelleng.md` — `Anlage`
  units with tables.
- `docs/stages/chunk.md` — extended with the merge behaviour; the contract is now complete.

### What to build

The merge pass and full-corpus coverage. Consecutive units below the 500-char floor that share the
same `section_path` combine into one chunk until the accumulated text reaches the floor; merging
never crosses a section boundary, never absorbs a split part, and never exceeds the max. A merged
chunk keys on its first covered unit, and its `citation` lists every covered unit. With split (Slice
2) and merge in place, all four MVP laws chunk end to end: GG's `Art` units chunk like §§, its
`Präambel`/`Eingangsformel` as units, `Anhang EV` as a unit; `Anlage`/`Anhang` units (incl. UStG's
table-bearing Anlagen) chunk with their tables atomic; empty-body `(weggefallen)` norms are skipped.
A documented spot-check confirms a sample chunk's text and metadata against the official source for
one representative unit per law.

Tests add fixtures for the merge case (several consecutive sub-floor units under one section → one
merged chunk; a section boundary blocks the merge) and confirm `Art`-based and `Anlage`-table laws
chunk through the existing golden fixtures (`strukturg`, `artg`, `tabelleng`) with pinned JSONL.

### Acceptance criteria

- [ ] Consecutive sub-floor units under the same `section_path` merge into one chunk (id keys on the
      first unit; `citation` lists all covered units); a section boundary or a split part blocks the
      merge; no merged chunk exceeds the max.
- [ ] All four MVP laws chunk without errors or silent loss: GG renders `Art`/`Präambel`/`Eingangsformel`/`Anhang EV`
      chunks, UStG's `Anlage` tables are present and atomic, and `(weggefallen)` empty-body norms
      produce no chunk. A dated spot-check (one unit per law) matches the official text.
- [ ] Golden-file tests cover merge, `Art` units, and an `Anlage` table via small fixtures; `make
      check` is green.

---

## Slice 4: Theory chapter, contract finalization, README status, phase wrap-up

**User stories**: 5 (theory chapter cross-linked with code), 7 (front door states landed vs. planned),
11 (pedagogy in the definition of done).

### Context

- `docs/theory/chunking.md` — the phase's theory chapter; created here.
- `docs/stages/chunk.md` — confirmed complete against the shipped behaviour.
- `docs/concepts.md — "Chunking" rows` — Fixed-size, Recursive character splitting, Sliding window /
  overlap, Structure-aware: link their `Place` column to the now-existing chapter.
- `README.md — status table, quick start, pipeline overview, structure table`.
- `AGENTS.md — Commands table` — gains a `make chunk` row.
- `docs/roadmap.md — "Phase 2"` heading and status table row.
- `src/rag/chunk/__init__.py — module docstring` — gains the theory + contract cross-links.

### What to build

The pedagogy and honesty artifacts that complete the definition of done. The theory chapter
(`docs/theory/chunking.md`) explains why chunk size matters (the retrieval precision/recall and
context-budget trade-off), presents **fixed-size chunking** and **recursive character splitting** as
the baselines that **structure-aware chunking** beats for law texts, explains the **sliding window /
overlap** rationale, and names what is deliberately deferred to Backlog 6 (semantic, hierarchical
parent-child, contextual enrichment, late chunking) — concise, each concept explained exactly once,
cross-linked both ways (module docstring ↔ chapter; chapter ↔ code and the stage contract). The
concept map's four chunking rows are hyperlinked to the chapter now that it exists.

README updates: Phase 2 status row → ✅ with a fresh clean-checkout verification date; `make chunk`
joins the quick start after `make convert`; the pipeline-overview `chunk` row links to
`docs/stages/chunk.md` and the chapter; the structure table gains `data/chunks/`. The AGENTS.md
Commands table gains `make chunk`. Roadmap Phase 2 heading and the README status table flip to ✅.

### Acceptance criteria

- [ ] `docs/theory/chunking.md` exists, is concise, covers fixed-size / recursive-character /
      structure-aware / overlap and names the Backlog-6 deferrals, and is cross-linked both ways
      (docstring → chapter, chapter → code + `docs/stages/chunk.md`); each concept appears once.
- [ ] `docs/concepts.md`'s Fixed-size, Recursive character splitting, Sliding window / overlap, and
      Structure-aware rows link to `theory/chunking.md`; no concept is added, moved, or dropped.
- [ ] README: Phase 2 row ✅ with a clean-checkout verification date; quick start includes `make
      chunk`; the `chunk` pipeline-overview row links its contract and chapter; the structure table
      lists `data/chunks/`; every relative link resolves.
- [ ] AGENTS.md Commands table lists `make chunk`; roadmap Phase 2 heading marked ✅.
- [ ] Definition-of-done audit against AGENTS.md rule 5 passes — code + tests + theory chapter +
      stage contract + README status with date all present; `make check` green; a full clean-checkout
      run (`make fetch && make convert && make chunk`) succeeds and is dated in the README.
