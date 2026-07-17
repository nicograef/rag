# Corpus & parsing

Why corpus choice, licensing, and parsing are RAG decisions — the theory behind Phase 1's
two stages, [fetch](../stages/fetch.md) ([`src/rag/fetch/`](../../src/rag/fetch/__init__.py))
and [convert](../stages/convert.md) ([`src/rag/convert/`](../../src/rag/convert/__init__.py)).
The stage contracts document *what* the stages produce; this chapter is the *why*. Every
concept below is explained exactly once, here; the [concept map](../concepts.md) points to
this chapter as its place.

## Why corpus acquisition is a RAG topic at all

It is tempting to treat "get the documents" as plumbing and hurry on to embeddings and
vector search. That instinct is where RAG systems quietly fail. Retrieval can only surface
what ingestion put in the store, phrased the way it was stored: if a citation footnote is
glued onto the end of a paragraph, that noise gets embedded, retrieved, and pasted into the
model's prompt as if it were fact — the generator cannot know it was reference cruft.
**Garbage in, garbage out** is the mechanism, not a slogan. Every downstream stage —
chunking, embedding, retrieval, generation — inherits the quality of this one, and none can
recover information the corpus never contained. The corpus is the ceiling on how good
everything after it can get.

Wikipedia articles earn their place here for the same reason: each carries a real heading
hierarchy — article → `== section ==` → `=== subsection ===` — that later stages can
exploit, so structure-aware chunking and citations stay a genuine lesson instead of a
pretend exercise on a flat toy corpus. Be honest about the scale, though: two or three
heading levels of encyclopedic prose is a **lighter** version of that lesson than a deeply
nested corpus (reference works such as legal codes or technical manuals) would teach. It is
real structure, not rich structure — enough to learn why structure beats fixed-size
splitting, and no more.

## Licensing and provenance are settled before the first vector

**Licensing.** A RAG system that may not legally reproduce its sources cannot quote or cite
them — fatal for a system whose answers must point at their sources. Rule 3 in
[AGENTS.md](../../AGENTS.md) admits only public-domain **or properly licensed** text. English
Wikipedia clears that bar on the second clause, not the first: its text is **CC BY-SA 4.0**
(with GFDL dual-listed as a legacy option), **not public domain** (verified live 2026-07-17).
That is a real licensing step-down — the text is copyrighted and reusable only on conditions,
where a public-domain source would carry none — and this chapter does not gloss it. Two
obligations follow, and both are cheap here:

- **Attribution.** Reusing the text requires crediting its authors; a **hyperlink to the
  article** satisfies this, because the article's history page lists every contributor.
  Attribution is therefore the same act as provenance (below) — the source URL we record for
  traceability *is* the credit the licence demands.
- **Share-alike** binds only **distributed adapted text**, not verbatim copies and not the
  surrounding code. Storing article text in the **gitignored, runtime-fetched** database is
  not a distribution event, so no copyleft attaches to this repo. Displaying a retrieved
  excerpt *is* a reproduction, so it needs attribution and a licence notice — satisfied at
  the point of display in the generate/ask stage. Whether an LLM paraphrase counts as
  "adapted material" is legally unsettled; showing the link and licence on every answer
  neutralizes the question rather than betting on it.

This chapter explains the reasoning; the live-verified facts and exactly what may enter the
corpus are pinned in the dated [corpus licensing decision](../roadmap.md#decisions), which
this chapter depends on rather than restates.

**Provenance.** An answer a user cannot trace to a source is an assertion, not a citation —
and traceability must be built in at ingestion, because no later stage can reconstruct it.
This is **data lineage**: recording where each indexed piece came from and through which
transformations. Concretely: fetch records the article's `source_title`, `source_url`,
`fetched_at`, and the exact `page_id` and `revision_id` it read, in each article's
`fetch.json`; convert copies the slug plus `source_title`, `source_url`, and `fetched_at`
into the Markdown front matter; Phase 2 carries them into per-chunk metadata, Phase 3 into
database rows. When the online path answers a question about Arsenal, that citation is a live
pointer to a dated fetch of a real article URL — a thread that stays unbroken only because it
was tied here, and which doubles as the CC BY-SA credit.

## Use declared structure, don't infer layout

The generic answer to "turn documents into text" — the one most RAG tutorials reach for,
because most sources are PDFs — is **document layout analysis (DLA)**: detecting a page's
visual structure (headings, columns, paragraphs, tables, footnotes) so each region can be
treated according to its role. DLA is real and sometimes unavoidable, but it is inference
from visual evidence: it reconstructs, with an error rate, structure the author knew and the
page format threw away. A heading in an odd font is misread; a two-column layout scrambles
reading order; a footnote merges into the paragraph above it.

Wikipedia never makes us guess. The MediaWiki Action API's **TextExtracts** extension
(`prop=extracts&explaintext=1&exsectionformat=wiki`) hands the article over as plain text
*with the editor-declared section headings kept as `== Heading ==` markers*. The heading tree
is data the editors wrote, not layout to be recovered from pixels — so convert maps it
deterministically to Markdown ATX headings: `==` → `##`, `===` → `###`, and so on down to
H6. That is the anti-DLA lesson intact: use the structure the source declares instead of
inferring it from a rendered page.

Be honest about what that convenience costs. Unlike a parse of the full source markup,
TextExtracts is **not lossless**: it deliberately strips images, flattens tables and lists to
plain lines, and drops the reference apparatus. The corpus that reaches chunking is therefore
**prose only** — accepted and documented, not an accident. We trade fidelity to the whole
page for text that is clean by construction.

**Text cleaning & normalization** is the counterpart: dropping what should not be kept, so
only meaningful text reaches chunking. For scraped web pages that means boilerplate and
cookie banners; here it is one more deliberate exclusion. Convert drops the non-prose
apparatus sections — *References*, *Notes*, *Further reading*, *External links*, *See also*,
and their kin — matched case-insensitively on the level-2 heading, subsections included.
These are citation machinery, not article prose; embedding them would put footnote-list noise
in front of the retriever exactly as glued-on footnotes would. TextExtracts already empties
most of them; convert removes the rest so the rule is explicit rather than incidental. The
lead paragraphs — the text before the first heading — become a synthetic `## Introduction`
section, so they are a chunkable unit like every other section instead of an orphan.

A worked slice — the extract convert reads:

```text
Arsenal Football Club is a professional football club based in Islington, London, England.

== History ==
=== Foundation and early years ===
Arsenal was founded in 1886 in Woolwich …
```

and the Markdown it emits (front matter abbreviated):

```markdown
---
slug: "arsenal"
source_title: "Arsenal F.C."
source_url: "https://en.wikipedia.org/wiki/Arsenal_F.C."
fetched_at: "2026-07-17T12:00:00+00:00"
---

# Arsenal F.C.

## Introduction

Arsenal Football Club is a professional football club based in Islington, London, England.

## History

### Foundation and early years

Arsenal was founded in 1886 in Woolwich …
```

The lead becomes `## Introduction`, `== History ==` becomes `## History`, `=== … ===`
becomes `### …`, and the provenance recorded by fetch rides along in the front matter. The
transform is **deterministic** — the same extract yields byte-identical Markdown, asserted by
golden-file tests per the [convert contract](../stages/convert.md). (Fetch, by contrast,
promises only idempotence, not determinism: Wikipedia is a living corpus, so re-fetching
legitimately changes the text.) This is also why Phase 1 uses no document-parsing library:
the API already hands over declared structure, so reaching for a layout model here would
reintroduce the very approximation we are avoiding.

## No silent content loss: fail loud

The faithfulness claim is weaker than a full-markup parse could make — convert is faithful to
the *prose the API returns*, not to the whole page. Within that scope, though, it still
refuses best effort. The tolerant alternative — skip the unfamiliar construct, emit
plausible-looking Markdown — is how corpora rot invisibly: the output is quietly missing a
subsection, and no downstream stage can notice text that simply isn't there. That is garbage
in, garbage out created by the tool itself.

Convert therefore fails loud: malformed provenance in `fetch.json`, a missing extract file,
an extract with no renderable content, or a heading nested past H6 each raise
`ConversionError`, and the failed article's Markdown file is not written
([code](../../src/rag/convert/__init__.py), [contract](../stages/convert.md)). Fetch guards
its own end: an article whose extract comes back empty fails the stage rather than entering
the corpus as a blank. The pipeline is only allowed to claim faithfulness because it is not
allowed to fail quietly.

## Where generic document parsing enters later

None of this makes DLA wrong — it is the wrong tool when the source already hands over its
structure, as the API does here. Messy PDFs, scanned pages, and tables that exist only as
visual grids are real, and they enter this repo deliberately as Backlog 12 (the Docling
ingestion path in the [roadmap](../roadmap.md)), a second connector proving the same stage
interfaces against a genuinely unstructured source. This chapter is the contrast that
motivates it: here the structure was declared and we used it; there it must be inferred — and
measured.
