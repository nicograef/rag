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

Body: H1 = law title; one `##` heading per norm unit from `<enbez>` — the heading text
is `enbez` alone when the norm has no `<titel>`, otherwise `enbez — titel`; Absätze as
plain paragraphs keeping their `(1)`-style markers; `<DL>` enumerations as Markdown
ordered lists with the source's own markers; `<BR>` breaks as block breaks.
`Inhaltsübersicht` norms (the XML's own tables of contents) are the only skipped norm
type.

**Normative text only** (per the licensing decision): footnotes (`<fussnoten>`,
`<Footnotes>`, `<FnR>` markers) and editorial apparatus (`<standangabe>`, status notes)
are excluded.

## Guarantees

- **Deterministic pure transform.** Same input files → byte-identical output, asserted
  exactly in golden-file tests (`tests/test_convert.py`).
- **No silent content loss.** Any XML construct the converter cannot render faithfully
  raises an error instead of dropping content; a failed law's output file is not
  written.
- **No network.** The stage is a local file transform.

## Current coverage

Walking skeleton (roadmap Phase 1, Slice 2): flat laws convert fully — KassenSichV end
to end. Section hierarchy (`<gliederungseinheit>`), GG's `Art` units and `Präambel`,
`Anlage`/`Anhang` norms, and tables land in Slice 3; until then, laws needing them fail
loudly per the no-silent-loss guarantee.

## Failure behaviour

Per-law isolation, like fetch: a law that cannot be converted is reported on stderr and
produces no output file; the remaining laws still convert. The exit code is non-zero if
any law failed.

## Downstream consumers

**chunk** (Phase 2) reads `data/corpus/*.md` and relies on the front-matter fields and
the heading-per-norm-unit structure (its contract lands with that phase).
