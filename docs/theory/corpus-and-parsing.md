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
what ingestion put in the store, phrased the way it was stored: if a footnote is glued onto
the end of a paragraph, that noise gets embedded, retrieved, and pasted into the model's
prompt as if it were law — the generator cannot know it was editorial cruft. **Garbage in,
garbage out** is the mechanism, not a slogan. Every downstream stage — chunking, embedding,
retrieval, generation — inherits the quality of this one, and none can recover information
the corpus never contained. The corpus is the ceiling on how good everything after it can get.

That is why German federal law is a deliberate choice, not a convenient one: it has genuine
structure (law → Teil/Abschnitt → § → Absatz) that later stages can exploit, so
structure-aware chunking and citations become a real lesson instead of a pretend exercise
on a toy corpus.

## Licensing and provenance are settled before the first vector

**Licensing.** A RAG system that may not legally reproduce its sources cannot quote or cite
them — fatal for a system whose answers must point at §§. So rule 3 in
[AGENTS.md](../../AGENTS.md) admits only public-domain or properly licensed text into the
corpus. The law texts from gesetze-im-internet.de are amtliche Werke under § 5 UrhG and
carry no copyright; the live-verified facts — the site's free-reuse statement, the
statutory basis, and exactly what may enter the corpus — are pinned in the dated
[corpus licensing decision](../roadmap.md#decisions). This chapter does not restate them;
it depends on them.

**Provenance.** An answer a user cannot trace to a source is an assertion, not a citation —
and traceability must be built in at ingestion, because no later stage can reconstruct it.
This is **data lineage**: recording where each indexed piece came from and through which
transformations. Concretely: fetch records `source_url` and `fetched_at` in each law's
`fetch.json`; convert copies both into the Markdown front matter (next to the site's own
`builddate` stamp); Phase 2 carries them into per-chunk metadata, Phase 3 into database
rows. When Phase 4 answers "according to § 3 AO", that citation is a live pointer to a
dated download of an official URL — a thread that stays unbroken only because it was tied
here.

## Lossless structure-aware parsing beats generic layout extraction

The generic answer to "turn documents into text" — the one most RAG tutorials reach for,
because most sources are PDFs — is **document layout analysis (DLA)**: detecting a page's
visual structure (headings, columns, paragraphs, tables, footnotes) so each region can be
treated according to its role. DLA is real and sometimes unavoidable, but it is inference
from visual evidence: it reconstructs, with an error rate, structure the author knew and
the page format threw away. A heading in an odd font is misread; a two-column layout
scrambles reading order; a footnote merges into the paragraph above it.

The official law XML (GiI-Norm DTD 1.01) has thrown nothing away — the structure DLA would
have to guess is declared outright:

- a **flat list** of `<norm>` elements in document order — reading order is given, not
  reconstructed;
- the section hierarchy as `gliederungskennzahl` codes, three digits per level — the
  Teil/Abschnitt tree is data (the code's length *is* the heading depth), not layout;
- each norm unit named by its `<enbez>` (`§ 3`, GG's `Art 1`, `Anlage 2`) — the citation
  anchor, handed over verbatim.

So convert parses the XML directly and maps declared structure deterministically to
Markdown. One norm maps straight through — `<enbez>` becomes the heading, each `<P>` a
paragraph that keeps its own `(1)`-style marker:

```xml
<norm><metadaten><enbez>§ 3</enbez><titel format="XML">Steuern</titel></metadaten>
  <textdaten><text format="XML"><Content>
    <P>(1) Steuern sind Geldleistungen …</P></Content></text></textdaten></norm>
```

```markdown
## § 3 — Steuern

(1) Steuern sind Geldleistungen …
```

The transform is **lossless** and **deterministic** (same input files →
byte-identical output, asserted by golden-file tests per the
[convert contract](../stages/convert.md)). This is also why Phase 1 uses no
document-parsing library: reaching for one here would add the very approximation the XML
lets us avoid — and writing the parser by hand *is* the document-parsing lesson, taken in
the one case where it can be done exactly.

**Text cleaning & normalization** is the counterpart: dropping what should not be kept, so
only meaningful text reaches chunking. For scraped web pages that means boilerplate and
cookie banners; here it is the licensing decision made concrete. Footnotes (`<fussnoten>`,
inline `<FnR>` markers) and editorial apparatus (`<standangabe>` status notes, `<kommentar>`
Fundstelle references) are the Dokumentationsstelle's editorial additions, not normative
text, so convert excludes them — a licensing act and a quality act at once, since they are
exactly the noise that would otherwise be embedded and retrieved as if it were law. The
only whole norm type skipped is `Inhaltsübersicht`, the XML's own table of contents.

## No silent content loss: fail loud

Lossless only means something if the parser is honest about what it cannot handle. The
tolerant alternative — skip the unfamiliar element, emit plausible-looking Markdown — is
how corpora rot invisibly: the output is quietly missing a table or a subsection, and no
downstream stage can notice text that simply isn't there. That is garbage in, garbage out
created by the tool itself.

Convert therefore refuses best effort: any construct it cannot render faithfully — an
unknown element, stray text where none is allowed, a missing `builddate`, a heading nested
past H6 — raises `ConversionError`, and the failed law's output file is not written
([code](../../src/rag/convert/__init__.py), [contract](../stages/convert.md)). The parser
is only allowed to claim losslessness because it is not allowed to fail quietly.

## Where generic document parsing enters later

None of this makes DLA wrong — it is the wrong tool when the source already carries its
structure. Messy PDFs, scanned pages, and tables that exist only as visual grids are real,
and they enter this repo deliberately as Backlog 12 (the Docling ingestion path in the
[roadmap](../roadmap.md)), a second connector proving the same stage interfaces against a
genuinely unstructured source. This chapter is the contrast that motivates it: here the
structure was declared and we used it; there it must be inferred — and measured.
