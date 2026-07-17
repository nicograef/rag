# Chunking

Why splitting a document into retrieval units is a design decision, not a formatting step —
the theory behind Phase 2's one stage, [chunk](../stages/chunk.md)
([`src/rag/chunk/`](../../src/rag/chunk/__init__.py)). The stage contract documents *what*
the chunker produces; this chapter is the *why*. Every concept below is explained exactly
once, here; the [concept map](../concepts.md) points to this chapter as its place.

## Why chunk size is the decision the whole retriever inherits

A chunk is the unit retrieval returns and the generator reads — you cannot retrieve half a
chunk, and you cannot cite less than one. So the boundary you draw is the resolution of the
whole system, fixed before a single vector exists. Two forces pull against each other, and
the chunk size is where they are balanced.

**Too big dilutes.** An embedding is one fixed-length vector per chunk (the *why* of that
lands with the embed stage in Phase 3); it compresses everything the chunk says into a
single point. A chunk covering five unrelated Absätze embeds to a point
close to no query in particular, so precise questions retrieve it weakly or not
at all. Oversized chunks also waste the generator's fixed token budget: a handful of them
fills the prompt, crowding out other relevant §§, and the model reads a long context
worst at its **middle** (the *lost in the middle* effect, its own concept — Backlog 7).
Retrieving less, more precisely, beats retrieving more.

**Too small fragments.** Cut below the natural unit of meaning and each chunk loses the
context that makes it answerable. A single Absatz retrieved without its § heading — or a
sentence severed from the enumeration it introduces — is technically a match and practically
useless: the generator cannot tell which norm it belongs to. Precision bought by shrinking
chunks is paid for in recall and in citations that no longer point at a whole thought.

The target is one chunk per coherent idea, with enough surrounding context to stand alone.
For law texts that unit is not a character count — it is the **§**.

## Fixed-size and recursive character splitting: the baselines

The tutorial default is **fixed-size chunking**: cut the text every *N* characters (or
tokens), regardless of content. It is trivial and reproducible, and it is wrong on every
boundary that matters — it slices mid-sentence, mid-word, mid-table, splitting a definition
from the term it defines because the character counter happened to land there. It treats a
document as an undifferentiated string.

**Recursive character splitting** is the standard improvement, and the one most RAG
libraries ship as their default. It tries an ordered list of separators — paragraph breaks
first, then line breaks, then sentence ends, then spaces — cutting on the coarsest one that
keeps every piece under the limit, and recursing to a finer separator only when a piece is
still too big. This respects *some* structure: it prefers to break between paragraphs, and
falls to breaking between words only as a last resort. But the structure it respects is
inferred from punctuation and whitespace — a heuristic guess at where meaning divides —
and it still has no idea that a run of text is one § and the next run is another.

Both are the right tool when the source arrives as a flat wall of text with no declared
structure. This corpus is not that.

## Structure-aware chunking: the document already declares its units

The law XML hands over its structure outright — law → Teil/Abschnitt → § → Absatz — and
[convert](../stages/convert.md) preserved every level of it as Markdown headings and
blank-line-separated blocks (why parsing it losslessly matters is the
[corpus & parsing chapter](corpus-and-parsing.md)). **Structure-aware chunking** uses that
gift: it splits a document along its *own* structural elements instead of at arbitrary
offsets a character counter or a punctuation heuristic invents. When the author already told
you where one idea ends and the next begins, guessing is strictly worse. This is the whole
of Phase 2, and it is why the chunker parses convert's headings rather than counting
characters:

- **Chunk by § (the natural semantic unit).** Each leaf norm unit — a §, an `Art`, a
  `Präambel`, an `Anlage` — becomes one chunk, heading included, so a retrieved chunk is a
  complete, citable norm. This is the default case, and for most of the corpus it is the
  only case: a § that fits the size budget is emitted whole.
- **Split oversized units by Absatz.** A § too long to embed well is divided at its Absatz
  boundaries — the structure the author *already* used to separate its provisions — never
  mid-provision. Each part still leads with the § heading, so it names itself.
- **Recursive-character splitting as the fallback, not the default.** A single Absatz that
  alone exceeds the budget (real legal enumerations run to tens of thousands of characters
  as one blank-line-free block) has no finer declared structure to exploit, so here — and
  only here — the chunker falls back to the character-splitting baseline above, cutting on
  paragraph → line → sentence → word. The baseline earns its place as the escape hatch for
  the one case structure runs out.
- **Tables are atomic.** A pipe or `table` block is never cut internally — splitting a table
  shears rows from their header and corrupts it. A table larger than the budget stays one
  oversized chunk (logged), the single deliberate exception to the size limit, because a
  corrupted table is worse than a large one.
- **Merge tiny units.** The mirror image of splitting: consecutive sub-floor units under the
  same section are combined into one chunk, so a two-line § does not become its own thin,
  contextless vector. Merging stops at a section boundary — units under different headings
  are about different things — and never crosses a repealed norm or exceeds the size budget.

The section path (Buch/Teil/Abschnitt) and the law name are kept as **metadata**, not
prepended to the chunk text — folding ancestor context into the text is *contextual
enrichment*, deliberately deferred (below). Phase 2 draws the boundary at the unit's own
heading plus body.

## Sliding window / overlap: context across the cut

Splitting a § reintroduces the very problem chunking tries to avoid — a provision whose
setup is in one chunk and whose consequence is in the next, so neither retrieves on the
whole thought. The countermeasure is **sliding window / overlap**: adjacent chunks share
some text, so information sitting on a boundary appears in both and cannot fall between them.

The design choice is *what* to overlap. A blind character window (repeat the last 200
characters) reintroduces the mid-sentence cut structure-aware chunking just removed. So the
overlap is structure-aware too: each split part after the first repeats the previous part's
**final whole Absatz**. Boundary context survives on clean provision lines with no severed
sentence, at the cost of one duplicated Absatz — a trade the *no silent loss* guarantee
accounts for by reconstructing the original body from the parts *minus* the overlap. The
character-splitting fallback, having no Absätze to repeat, falls back consistently to a
fixed character-window overlap between its pieces — the same reasoning, one structural level
down.

A toy split makes the overlap concrete — a § whose two Absätze together overflow the size
budget, cut into two parts:

```text
§ 5 Beispielnorm         both Absätze together exceed the toy budget, so the § splits by
(1) Erster Absatz.       Absatz; each part re-leads with the § heading, and each part after
(2) Zweiter Absatz.      the first repeats the previous part's final whole Absatz:

  part 1                   part 2
  § 5 Beispielnorm         § 5 Beispielnorm     ← heading repeated on both parts
  (1) Erster Absatz.       (1) Erster Absatz.    ← repeated overlap (part 1's last Absatz)
                           (2) Zweiter Absatz.
```

## Deliberately deferred to Backlog 6

Phase 2 is the honest structure-aware baseline; four advanced strategies build on it and are
scoped to Backlog 6, each measured against this baseline via the evaluate harness rather than
assumed better. They are named here and explained in Backlog 6's own chapter, not this one:

- **Semantic chunking** — split where embedding similarity between adjacent sentences drops,
  rather than on declared structure.
- **Hierarchical (parent-child) chunking** — retrieve small Absatz-level children for
  precision, assemble their larger § parents for context.
- **Contextual enrichment** — prepend the heading path or an LLM-written situating summary to
  each chunk before embedding, so it is retrievable in isolation (Phase 2's metadata fields
  are the raw material).
- **Late chunking** — embed a long window first, then pool token embeddings into per-chunk
  vectors that retain whole-document context (conditional on the Phase 3 model exposing token
  embeddings).

## Where this leaves the pipeline

The chunker turns convert's Markdown into `data/chunks/<slug>.jsonl` — one structure-aware,
self-identifying, citable record per retrieval unit, each carrying the metadata later stages
filter and cite on. The exact record schema, size thresholds, split/merge rules, and
guarantees are the [chunk stage contract](../stages/chunk.md); the code that implements them
is [`src/rag/chunk/`](../../src/rag/chunk/__init__.py). Phase 3 embeds these records and
loads them into the vector store — where the resolution chosen here becomes the resolution
retrieval can ever achieve.
