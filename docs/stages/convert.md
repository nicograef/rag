# Stage contract: convert

> Code: [`src/rag/convert/`](../../src/rag/convert/__init__.py) ·
> Roadmap: [Phase 1 — Fetch & convert](../roadmap.md)

Turns each fetched law under `data/raw/` into one clean, structure-preserving Markdown
file in `data/corpus/`. Second stage of the offline ingestion pipeline; what it may emit
is pinned by the [corpus licensing decision](../roadmap.md#decisions).

## Invocation

```sh
make convert                      # wraps:
uv run python -m rag.convert      # options: --raw-dir data/raw --corpus-dir data/corpus
```

## Input

One `data/raw/<slug>/` directory per law, as produced by [fetch](fetch.md):

- exactly one GiI-Norm XML file (named in `fetch.json`'s `files`; non-XML attachments
  are ignored and flagged in the log output),
- `fetch.json` — `source_url` and `fetched_at` feed the provenance front matter.

Convert reads only this directory — it never performs network I/O.

## Output

One `data/corpus/<slug>.md` per law. YAML front matter (all values double-quoted):

| Field          | Source                               | Meaning                                  |
| -------------- | ------------------------------------ | ---------------------------------------- |
| `slug`         | `fetch.json`                         | site slug, = the file name stem          |
| `abbreviation` | `<amtabk>`, else the first `<jurabk>`| official abbreviation (GG has no amtabk) |
| `title`        | header norm's `<langue>`             | official long title, = the H1            |
| `source_url`   | `fetch.json`                         | exact download URL                       |
| `fetched_at`   | `fetch.json`                         | download time, ISO 8601 UTC              |
| `builddate`    | `<dokumente builddate>` attribute    | the site's XML build stamp, verbatim     |

These fields plus the heading structure are the interface the chunk stage builds on.

Body: H1 = law title, then the flat `<norm>` list in document order.

- **Section headings** — `<gliederungseinheit>` norms (always empty-bodied in the
  source; a non-empty body raises) become headings at depth
  `1 + len(gliederungskennzahl) / 3` (3 digits per level): AO's Teil → Abschnitt →
  Unterabschnitt → roman sub-level spans H2–H5. Heading text is
  `gliederungsbez — gliederungstitel`, or the `bez` alone without a titel.
- **Norm-unit headings** — one heading per `<enbez>` norm, one level below the current
  section (H2 without one; AO's deepest §§ reach H6, past H6 raises). Units whose
  `enbez` starts with `Anlage`/`Anhang` sit outside the hierarchy and render at H2.
  Heading text is `enbez` alone when the norm has no `<titel>`, otherwise
  `enbez — titel`. Repealed norms keep their `(weggefallen)` titel and have no body.
- **Absätze** as plain paragraphs keeping their `(1)`-style markers; `<BR>` as block
  breaks; `<Title>` template sub-headings (AO Anlage 1) as `**bold**` paragraphs, never
  headings.
- **Enumerations** (`<DL>`) as plain-text lists with the source's own markers verbatim
  (`1.`, `a)`, `-`, `*)`, even empty); within an item, continuation prose and nested
  lists are indented 4 spaces below the marker line.
- **Tables** (CALS): regular tables (every row has all columns, no spans) become
  Markdown pipe tables — header from `<thead>`, or an empty header row when the source
  has none; irregular tables (`morerows`/`namest` spans, e.g. UStG Anlage 2) become
  fenced ` ```table ` blocks, one source row per line, cells joined with ` | `. Cell
  content is flattened to one line.
- **Inline markup**: `<B>` → `**bold**`; `<SP>` (Sperrschrift) and `<NB>` unwrap to
  plain text; `<noindex>` wrappers are transparent.

`Inhaltsübersicht` norms (the XML's own tables of contents) are the only skipped norm
type.

**Normative text only** (per the licensing decision): footnotes (`<fussnoten>`,
`<Footnotes>`, `<FnR>` markers) and editorial apparatus (`<standangabe>`, status notes,
`<kommentar>` Fundstelle references) are excluded.

## Guarantees

- **Deterministic pure transform.** Same input files → byte-identical output, asserted
  exactly in golden-file tests (`tests/test_convert.py`).
- **No silent content loss.** Any XML construct the converter cannot render faithfully
  raises an error instead of dropping content; a failed law's output file is not
  written.
- **No network.** The stage is a local file transform.

## Current coverage

Full corpus structure (roadmap Phase 1, Slice 3): all four MVP laws convert end to end —
section hierarchy, GG's `Art` units and `Präambel`, `Anlage`/`Anhang` norms, and tables
included. Spot-checked against the official site's rendering on 2026-07-12: § 3 AO is
character-identical to <https://www.gesetze-im-internet.de/ao_1977/__3.html> (the page's
only extra text is the editorial `+++ Zur Anwendung +++` note, excluded per the licensing
decision), Art 1 GG matches verbatim, and UStG Anlage 2's rows, sub-items, and Zolltarif
values match the official table.

## Failure behaviour

Per-law isolation, like fetch: a law that cannot be converted is reported on stderr and
produces no output file; the remaining laws still convert. The exit code is non-zero if
any law failed.

## Downstream consumers

**chunk** (Phase 2) reads `data/corpus/*.md` and relies on the front-matter fields and
the heading-per-norm-unit structure (its contract lands with that phase).
