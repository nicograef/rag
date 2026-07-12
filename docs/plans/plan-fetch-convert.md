# Plan: Roadmap Phase 1 — Fetch & convert (corpus acquisition)

> Source PRD: [../prds/prd-rag-playbook.md](../prds/prd-rag-playbook.md) ·
> Roadmap phase: [../roadmap.md](../roadmap.md) "Phase 1 — Fetch & convert"

## Goal

Land the **fetch** and **convert** stages: a config-driven fetcher that downloads official
law XML from gesetze-im-internet.de into `data/raw/`, and a deterministic converter that
turns it into one clean, structure-preserving Markdown file per law under `data/corpus/`.

This is the **first phase under the new definition of done** ([AGENTS.md](../../AGENTS.md)
rule 5), so it also creates the first stage contracts (`docs/stages/`), the first theory
chapter (`docs/theory/`), the dated licensing decision, and the README status update —
setting the template every later phase follows. The work is broken into four **slices**;
all slices together make up roadmap Phase 1 ("Phase" below always means the roadmap phase).

## Architectural decisions

Durable decisions that apply across all slices:

- **Runtime dependencies** (the repo's first two, user-approved 2026-07-11): `httpx` for
  HTTP and `lxml` for XML parsing. Each is declared in `pyproject.toml` in the slice that
  first imports it — `httpx` in Slice 1, `lxml` in Slice 2. Tests use
  `httpx.MockTransport` so the suite never touches the network.
- **Stage = subpackage, invoked via `python -m`**: `src/rag/fetch/` and `src/rag/convert/`,
  each with a `__main__.py` (argparse), run as `uv run python -m rag.fetch` /
  `uv run python -m rag.convert`, wrapped by Makefile targets `make fetch` and
  `make convert`. This invocation shape lands in the README and the stage contracts and
  must not change later.
- **Law config**: `laws.toml` in the project root — a checked-in list of law entries keyed
  by the site's **slug** (e.g. `ao_1977`); parsed with stdlib `tomllib`. Adding a law to
  the corpus means adding one entry. Slugs for the MVP corpus: `ao_1977`, `ustg_1980`,
  `kassensichv`, `gg`.
- **Artifacts** (stage contracts in `docs/stages/fetch.md` / `docs/stages/convert.md`):
  - **fetch**: `laws.toml` → `data/raw/<slug>/` containing the extracted XML file(s) plus
    `fetch.json` with the keys `slug`, `source_url`, `fetched_at` (ISO 8601 UTC), and
    `files` (extracted file names). Guarantee: **idempotence** — a re-run cleanly replaces
    the law's directory; no determinism promise (amended laws legitimately change the
    output).
  - **convert**: `data/raw/<slug>/` → `data/corpus/<slug>.md`; reads the XML plus
    `fetch.json` (for `source_url`/`fetched_at` provenance). Guarantee: **deterministic
    pure transform** — same input files, byte-identical output, asserted exactly in tests.
- **Markdown shape** (the convert contract; the chunk stage builds on these fields):
  - YAML front matter: `slug`, `abbreviation` (= `<amtabk>` if present, else the first
    `<jurabk>` — GG has no `amtabk`), `title` (= the `<langue>` element of the law's
    header norm, the first norm, which has no `enbez`), `source_url`, `fetched_at`,
    `builddate`.
  - H1 = law title; section-structure headings (Buch/Teil/Abschnitt) reconstructed from
    the XML's `gliederungskennzahl` codes; one heading per norm unit from `enbez` —
    `§ N`, GG's `Art N`, and the non-§ units `Präambel`, `Eingangsformel`,
    `Schlussformel`, `Anlage …`/`Anhang …`. Heading text is `enbez` alone when the norm
    has no `<titel>`, otherwise `enbez — titel`. `Inhaltsübersicht` norms (the XML's own
    tables of contents) are the only skipped norm type.
  - Absätze as plain paragraphs keeping their `(1)`-style markers.
- **Normative text only**: convert emits the norm texts; footnotes (`<fussnoten>`) and
  editorial apparatus (`<standangabe>`, status notes) are excluded unless the Slice-1
  licensing verification explicitly covers them (per PRD "Corpus").
- **Source facts** (verified 2026-07-11 via the QuantLaw same-day archive of the site —
  this planning environment could not reach the live site; Slice 1 re-verifies live):
  download URL pattern `https://www.gesetze-im-internet.de/<slug>/xml.zip`; XML follows
  the **GiI-Norm DTD 1.01** (`/dtd/1.01/gii-norm.dtd`); a `<dokumente>` root holds a
  **flat** list of `<norm>` elements — hierarchy is *not* nested XML but encoded in
  3-digits-per-level `gliederungskennzahl` codes inside `<gliederungseinheit>`; the unit
  identifier is `<enbez>`, its heading `<titel>`; body text is `<textdaten>/<text>/
  <Content>` with `<P>` paragraphs, `<DL>/<DT>/<DD>` lists, and CALS-style tables. All
  tables in the MVP corpus live in `Inhaltsübersicht` or `Anlage`/`Anhang` norms (UStG's
  substantive tables in `Anlage 1`–`Anlage 5`, GG's one table in `Anhang EV`). The four
  laws total ≈ 0.4 MB zipped / 1.8 MB unzipped (mirror-measured 2026-07-11) — the
  first-run download cost is negligible and documented as such.

## Inventory

- `Makefile — db / check targets` — pattern for the new `fetch` and `convert` targets.
- `laws.toml` — does not exist yet; created in Slice 1.
- `src/rag/__init__.py — module docstring` — still names the old stage list
  ("fetch, chunk, embed, load, query"); corrected to the taxonomy in Slice 1.
- `tests/test_smoke.py — test_package_importable()` — prior art: test through the public
  package surface; stage contract tests extend this pattern.
- `.gitignore — data/` — already ignores all pipeline artifacts; fixtures live under
  `tests/` instead (small, checked-in samples per PRD "Testing Decisions").
- `docs/roadmap.md — "Decisions"` — receives the dated licensing decision entry.
- `README.md — status table, quick start, pipeline overview, structure table` — updated in
  Slice 4 (status ✅ with verification date, new commands, first-run costs).
- `AGENTS.md — Commands table` — gains `fetch`/`convert` rows in Slice 4.

## Resolved decisions

Clarified with the maintainer on 2026-07-11:

- Dependencies: `httpx` + `lxml` (production-typical stack) rather than stdlib-only.
- Stage contracts live in `docs/stages/<stage>.md`, one file per stage — linked from the
  README pipeline overview and from the stage's module docstring.
- CLI shape: `python -m rag.<stage>` + Makefile wrappers; no console-script entry point.
- Theory: one chapter, `docs/theory/corpus-and-parsing.md` — why corpus choice and
  licensing matter for RAG, and why lossless structure-aware parsing of official XML beats
  generic layout extraction. Creates `docs/theory/`.

## Open questions / Risks

- **Live-site facts are mirror-verified only.** This planning environment's network blocks
  gesetze-im-internet.de; URL pattern, DTD, and structure were verified against a same-day
  scrape archive (QuantLaw). Slice 1 acceptance requires re-verifying live (real download
  of all four zips) before anything is marked done.
- **Live-network criteria are maintainer-verified.** When the implementing session cannot
  reach the site (the documented cloud-session case), it completes everything
  test-fixture-driven, leaves the live checkboxes unticked, and lists them for the
  maintainer to verify and date — a slice is not complete, and the roadmap phase not ✅,
  until then.
- **Terms of use**: the free-reuse sentence („Die Rechtsnormen in ihrer deutschsprachigen
  Fassung stehen in allen angebotenen Formaten zur freien Nutzung und Weiterverwendung zur
  Verfügung.") is search-index-confirmed but the full `hinweise.html` text was not
  readable from here; the licensing decision entry must quote it from the live page.
- **Non-§ norm units vary by law**: GG uses `Art` and has `Präambel` + `Eingangsformel`;
  KassenSichV has `Eingangsformel` + `Schlussformel`; AO and UStG have `Inhaltsübersicht`
  and `Anlage` norms; AO and GG additionally contain repealed placeholders (`enbez` like
  `(XXXX) §§ 134 bis 136`, `<titel>` "(weggefallen)", empty body). Convert must handle all
  non-`§` `enbez` values and skip only `Inhaltsübersicht`.
- **Zip contents**: the DTD allows attachments (`IMG`, `FILE`); the four MVP laws are
  text-only in the archive, but fetch must cope with (extract) and convert must ignore
  non-XML files, flagging them in its log output.

---

## Slice 1: Fetch stage — config-driven download into `data/raw/`

**User stories**: 1 (run landed stages on a dev machine), 3 (single-responsibility stage
with a documented contract), 6 (dated decisions).

### Context

- `laws.toml` — created here (four MVP slugs).
- `src/rag/fetch/` — new subpackage with `__main__.py`.
- `src/rag/__init__.py — module docstring` — stale stage list fixed in the same commit
  that introduces the first stage subpackage.
- `Makefile` — new `fetch` target.
- `docs/stages/fetch.md` — first stage contract; created here.
- `docs/roadmap.md — "Decisions"` — licensing decision entry.
- `pyproject.toml` — gains `httpx` as the first runtime dependency.

### What to build

Reading `laws.toml`, fetch downloads `https://www.gesetze-im-internet.de/<slug>/xml.zip`
for every configured law (httpx, explicit timeout, fail loudly on HTTP errors), extracts
the archive into `data/raw/<slug>/`, and writes `data/raw/<slug>/fetch.json` with the
schema pinned in the architectural decisions. Re-running replaces each law's directory
cleanly (build into a temporary directory, then swap) — idempotence, not determinism. One
law failing must not corrupt the others' existing artifacts.

Alongside the code: the `docs/stages/fetch.md` contract (input, output artifact layout,
invocation, guarantees, failure behaviour, and which of its outputs downstream stages
consume — convert reads the XML and `fetch.json`), and the **licensing verification** —
read the live `hinweise.html`, confirm the free-reuse statement and the § 5 UrhG basis,
and record a dated decision entry in the roadmap (context, choice, consequences) stating
exactly what the corpus may include; the existing README licensing wording is confirmed or
amended accordingly.

Tests (no network): a small fixture zip under `tests/`; download logic driven through
`httpx.MockTransport`; assertions on the artifact layout, `fetch.json` content,
idempotent re-run, and error behaviour (HTTP error → non-zero exit, other laws intact).

### Acceptance criteria

- [x] `make fetch` downloads all four MVP laws into `data/raw/<slug>/` with `fetch.json`,
      verified **live** (URLs respond, zips contain the expected XML), and the live check
      is recorded with its date in the roadmap decision entry.
- [x] Re-running `make fetch` is idempotent: directories are cleanly replaced, no stale
      files survive, and an interrupted or failed law leaves other laws' artifacts intact.
- [x] Contract tests pass without network access (`httpx.MockTransport` + fixture zip)
      and `make check` is green.
- [x] `docs/stages/fetch.md` documents input, output artifact incl. the `fetch.json`
      schema, invocation, idempotence guarantee (explicitly *not* determinism), and what
      downstream consumes.
- [x] The dated licensing decision entry is in the roadmap: live-verified quote of the
      free-reuse statement, § 5 Abs. 1 UrhG basis, and what convert may include
      (normative text; footnotes/editorial apparatus only if covered).
- [x] `src/rag/__init__.py`'s docstring names the stage taxonomy correctly.
- [x] `httpx` is declared in `pyproject.toml` with the lockfile updated.

---

## Slice 2: Convert stage — one flat law end to end

**User stories**: 1, 3, 4 (contracts state exactly which fields downstream stages require).

### Context

- `src/rag/convert/` — new subpackage with `__main__.py`.
- `data/raw/kassensichv/` — smallest MVP law (~20 KB XML), the walking skeleton's input:
  flat (no `gliederungseinheit`), no tables, norm units `§ 1`–`§ 11` plus
  `Eingangsformel` and `Schlussformel`.
- `tests/` — gains a small checked-in XML fixture (a truncated law in GiI-Norm format).
- `docs/stages/convert.md` — second stage contract; created here.
- `Makefile` — new `convert` target.
- `pyproject.toml` — gains `lxml` (first imported here).

### What to build

The minimal honest converter: parse one law's XML with lxml, walk the flat `<norm>` list,
and emit `data/corpus/<slug>.md` — YAML front matter per the architectural decisions
(provenance fields read from `fetch.json`), H1 from the law title, one heading per norm
unit from `enbez` (`§ N` plus KassenSichV's `Eingangsformel`/`Schlussformel`; heading text
is `enbez` alone when the norm has no `<titel>`, otherwise `enbez — titel`), Absätze as
paragraphs with their `(1)` markers, `<DL>` definition lists as Markdown lists. Hierarchy
reconstruction, GG's `Art` units and `Präambel`, `Anlage`/`Anhang` norms, and tables are
explicitly deferred to Slice 3 — KassenSichV needs none of them.

Determinism is asserted, not assumed: converting the same input twice yields byte-identical
output, and a golden-file test pins the full Markdown for the fixture law.

The `docs/stages/convert.md` contract documents input (XML + `fetch.json`), output,
invocation, the determinism guarantee, the front-matter fields (these are what chunk will
rely on), and the normative-text-only rule from the licensing decision.

### Acceptance criteria

- [x] `make convert` turns `data/raw/kassensichv/` into `data/corpus/kassensichv.md` with
      correct front matter and one section per norm unit (11 §§ plus `Eingangsformel` and
      `Schlussformel`).
- [x] Golden-file test: fixture XML → pinned Markdown, compared byte-exactly; running
      convert twice on the same input is asserted byte-identical.
- [x] Convert reads only `data/raw/` and never performs network I/O.
- [x] `docs/stages/convert.md` documents the contract including every front-matter field
      and the normative-text-only rule.
- [x] `lxml` is declared in `pyproject.toml` with the lockfile updated; `make check` is
      green.

---

## Slice 3: Convert — full corpus structure (hierarchy, Artikel, Anlagen, tables)

**User stories**: 1, 3, 4.

### Context

- `src/rag/convert/` — extended; no new packages.
- Source facts (architectural decisions): hierarchy encoded in `gliederungskennzahl`
  (3 digits per level) inside `<gliederungseinheit>`; heading text from `gliederungsbez` +
  `gliederungstitel`; all MVP-corpus tables live in `Inhaltsübersicht` or
  `Anlage`/`Anhang` norms.
- `data/raw/ao_1977/`, `data/raw/ustg_1980/`, `data/raw/gg/` — the structurally hard cases
  (AO: deep Teil/Abschnitt nesting and repealed placeholders; GG: `Art` units, `Präambel`,
  `Anhang EV`; UStG: substantive tables in `Anlage 1`–`Anlage 5`).

### What to build

Everything the walking skeleton deferred: reconstruct the Buch/Teil/Abschnitt heading tree
from the `gliederungskennzahl` codes (3 digits per level → heading depth), handle GG's
`Art` units and `Präambel`, convert `Anlage`/`Anhang` norms like any other norm unit
(heading from `enbez`, body including their tables — `Inhaltsübersicht` stays the only
skipped norm type), render repealed placeholders as their heading with the "(weggefallen)"
title and no body, render CALS-style tables (Markdown tables where regular, fenced blocks
otherwise — the exact rendering pinned by golden tests), and exclude footnotes and
editorial apparatus per the licensing decision. After this slice, all four MVP laws
convert cleanly and the output is spot-checked against the official site's rendering.

Tests extend the golden-file pattern with small targeted fixtures: one nested-hierarchy
sample, one `Art`-based sample, one `Anlage` table sample.

### Acceptance criteria

- [ ] All four MVP laws convert without errors or silent content loss; a documented
      spot-check (§ 3 AO full text, Art 1 GG, one UStG `Anlage` table) matches the
      official text.
- [ ] Heading nesting in the Markdown mirrors the `gliederungskennzahl` structure; GG
      renders `Präambel`, `Art` units, and `Anhang EV`; UStG's `Anlage` tables are
      present; footnotes and editorial apparatus are absent.
- [ ] Tables render as pinned in the golden-table fixture (Markdown table for the regular
      case, fenced block for the irregular case).
- [ ] Golden-file tests cover hierarchy, `Art` units, and an `Anlage` table via small
      fixtures; `make check` is green.

---

## Slice 4: Theory chapter, README status, phase wrap-up

**User stories**: 5 (theory chapter cross-linked with code), 7 (front door states landed
vs. planned and first-run costs), 11 (pedagogy in the definition of done).

### Context

- `docs/theory/corpus-and-parsing.md` — first theory chapter; creates `docs/theory/`.
- `README.md — status table, quick start, pipeline overview, structure table`.
- `AGENTS.md — Commands table` — gains `fetch`/`convert` rows.
- `docs/roadmap.md — "Phase 1"` — status flips to ✅.
- `src/rag/fetch/`, `src/rag/convert/` — module docstrings gain the theory cross-links.

### What to build

The pedagogy and honesty artifacts that complete the definition of done. The theory
chapter explains why corpus choice and licensing are RAG decisions (garbage in, garbage
out; provenance for citations) and why lossless structure-aware parsing of official XML
beats generic layout extraction — concise, cross-linked both ways (module docstrings and
the README pipeline overview link to the chapter; the chapter links to the code and the
stage contracts).

README updates: Phase 1 status row ✅ with the quick-start re-verification date; `make
fetch` / `make convert` join the quick start with their real first-run cost (≈ 0.4 MB
download for the MVP corpus — re-measured, not copied from this plan); pipeline-overview
rows for fetch and convert link to their contracts and the chapter; the structure table
gains `docs/stages/`, `docs/theory/`, and `laws.toml`. The AGENTS.md Commands table gains
the two new targets. Roadmap Phase 1 flips to ✅.

### Acceptance criteria

- [ ] `docs/theory/corpus-and-parsing.md` exists, is concise, and is cross-linked both
      ways (docstrings → chapter, chapter → code and contracts, README overview →
      chapter); the concept appears nowhere else in the repo except as links.
- [ ] README: Phase 1 row ✅, quick start includes fetch/convert with measured first-run
      cost and a fresh clean-checkout verification date; structure table lists
      `docs/stages/`, `docs/theory/`, `laws.toml`; every relative link resolves.
- [ ] AGENTS.md Commands table lists `make fetch` and `make convert`.
- [ ] Roadmap Phase 1 marked ✅; `make check` green; full pipeline re-run from a clean
      checkout (`make fetch && make convert`) succeeds and is dated in the README.
- [ ] Definition-of-done audit against AGENTS.md rule 5 passes: code + tests + theory
      chapter + two stage contracts + README status with date — all present.
