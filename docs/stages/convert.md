# Stage contract: convert

> Code: [`src/rag/convert/`](../../src/rag/convert/__init__.py) ·
> Roadmap: [Phase 1 — Fetch & convert](../roadmap.md) ·
> Theory: [corpus & parsing](../theory/corpus-and-parsing.md)

Turns each fetched Wikipedia article extract under `data/raw/` into one clean,
section-structured Markdown file in `data/corpus/`. Second stage of the offline
ingestion pipeline; what it may emit is pinned by the
[corpus licensing decision](../roadmap.md#decisions).

## Invocation

```sh
make convert                      # wraps:
uv run python -m rag.convert      # options: --raw-dir data/raw --corpus-dir data/corpus
```

## Input

One `data/raw/<slug>/` directory per article, as produced by [fetch](fetch.md):

- `extract.txt` — the plain-text extract, with `== Heading ==` / `=== Subheading ===`
  wiki-format section markers.
- `fetch.json` — `source_title`, `source_url`, and `fetched_at` feed the provenance
  front matter.

Convert reads only this directory — it never performs network I/O.

## Output

One `data/corpus/<slug>.md` per article. YAML front matter (every value double-quoted):

| Field          | Source       | Meaning                              |
| -------------- | ------------ | --------------------------------------|
| `slug`         | `fetch.json` | config slug, = the file name stem     |
| `source_title` | `fetch.json` | the resolved article title, = the H1  |
| `source_url`   | `fetch.json` | the article's canonical URL           |
| `fetched_at`   | `fetch.json` | fetch time, ISO 8601 UTC              |

Body: H1 = article title, then the article's sections in document order.

- **Lead → Introduction.** The lead paragraphs (the text before the first heading)
  carry no heading of their own in the extract; convert synthesizes an `##
  Introduction` section for them so they are a chunkable unit like every other section.
- **Heading translation.** A wiki heading is 2 or more balanced `=` around the title
  (e.g. `== History ==`); it becomes an ATX heading at the same depth — `==` → `##`,
  `===` → `###`, and so on. A heading past H6 (never valid wiki syntax) raises.
- **Body blocks.** TextExtracts puts every paragraph — and every flattened list item —
  on its own line; each non-blank line becomes one blank-line-separated Markdown block.
- **Apparatus sections dropped** — non-prose apparatus, not article prose:
  References, Notes, Footnotes, Citations,
  Sources, Bibliography, Works cited, Further reading, External links, and See also,
  matched case-insensitively on the level-2 heading text (their subsections go with
  them).
- TextExtracts itself already strips images and flattens tables and lists before
  convert ever sees the extract, so the resulting corpus is **prose only** — an
  accepted property of the source, not a workaround.

## Guarantees

- **Deterministic pure transform.** Same extract → byte-identical output, asserted
  exactly in golden-file tests (`tests/test_convert.py`).
- **Per-article isolation.** A malformed `fetch.json`, a missing `extract.txt`, an
  extract with no renderable content, or a heading past H6 raises `ConversionError` for
  that article only — its output file is not written — and the remaining articles still
  convert.
- **No network.** The stage is a local file transform.

## Current coverage

**Spot-check (2026-07-17):** `make fetch` followed by `make convert` over all 20
configured clubs produced 20 non-empty, section-structured Markdown files — e.g.
`arsenal.md` (≈ 48 KB) and `west-ham.md` (≈ 67 KB) — each with a front-matter block, an
`## Introduction` section built from the lead, and multiple further `##`/`###` sections
(Arsenal alone renders History, Crest, Colours, Stadiums, Players, Honours, …); no
`References`, `External links`, or `See also` apparatus made it into any file.

## Failure behaviour

Per-article isolation, like fetch: an article that cannot be converted is reported on
stderr and produces no output file; the remaining articles still convert. The exit code
is non-zero if any article failed.

## Downstream consumers

**chunk** ([contract](chunk.md)) reads `data/corpus/<slug>.md`.
