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
single point. A chunk covering five unrelated subsections embeds to a point close to no
query in particular, so precise questions retrieve it weakly or not at all. Oversized chunks
also waste the generator's fixed token budget: a handful of them fills the prompt, crowding
out other relevant sections, and the model reads a long context worst at its **middle** (the
*lost in the middle* effect, its own concept — Backlog 7). Retrieving less, more precisely,
beats retrieving more.

**Too small fragments.** Cut below the natural unit of meaning and each chunk loses the
context that makes it answerable. A single subsection retrieved without its section heading —
or a sentence severed from the paragraph it belongs to — is technically a match and
practically useless: the generator cannot tell which article or section it came from.
Precision bought by shrinking chunks is paid for in recall and in citations that no longer
point at a whole thought.

The target is one chunk per coherent idea, with enough surrounding context to stand alone.
For Wikipedia that unit is the **section** (`##`) — a whole level-2 section including its
`###` subsections and their prose — bounded by what the embedder can actually read in one
pass, which is the next section's lesson.

## Characters versus tokens: why the size cap is load-bearing now

The chunker counts **characters**; the embedding model counts **tokens**. A tokenizer splits
text into subword units, and English prose runs several characters per token — so a
character budget is only a proxy for the token budget that actually constrains the model. The
two do not track each other cleanly: dense, punctuation-heavy, or unusual text tokenizes into
more tokens per character than plain prose, so the same character count can land at very
different token counts.

That gap is why `max_chars` is now **load-bearing**, not slack. Phase 3 embeds with
**bge-small-en-v1.5**, whose context window is **512 tokens** — down from the earlier
model's 8192. A chunk longer than 512 tokens does not error on its own; the model simply
**truncates** it and embeds the surviving head, silently dropping the chunk's tail from the
vector. Under an 8192-token window a generous character cap left comfortable headroom; under
512 it does not, so the cap has to be chosen to guarantee every chunk fits.

`max_chars` is therefore pinned to **1200 characters**, and the number is **validated, not
guessed**. Measured over the fetched corpus with bge-small-en-v1.5's *own* tokenizer, the
densest chunk text runs ≈ 2.44 chars/token, so 1200 characters stays ≤ ~492 tokens even in
the worst case — under 512 — while the observed maximum across the corpus is only 375 tokens.
The discipline is the general lesson: a token cap can only be honoured by measuring the real
text through the real tokenizer, because chars-per-token is a property of the corpus, not a
constant. The **embed token-guard** is the backstop — if a future, denser article ever
crossed 512 tokens, embed **fails loud** rather than truncating quietly, so the guarantee is
enforced, never assumed.

## Fixed-size and recursive character splitting: the baselines

The tutorial default is **fixed-size chunking**: cut the text every *N* characters (or
tokens), regardless of content. It is trivial and reproducible, and it is wrong on every
boundary that matters — it slices mid-sentence, mid-word, mid-heading, splitting a claim from
the fact it rests on because the character counter happened to land there. It treats a
document as an undifferentiated string.

**Recursive character splitting** is the standard improvement, and the one most RAG
libraries ship as their default. It tries an ordered list of separators — paragraph breaks
first, then line breaks, then sentence ends, then spaces — cutting on the coarsest one that
keeps every piece under the limit, and recursing to a finer separator only when a piece is
still too big. This respects *some* structure: it prefers to break between paragraphs, and
falls to breaking between words only as a last resort. But the structure it respects is
inferred from punctuation and whitespace — a heuristic guess at where meaning divides — and
it still has no idea that one run of text is one section and the next run is another.

Both are the right tool when the source arrives as a flat wall of text with no declared
structure. This corpus is not that — but, as below, the recursive baseline still earns a job
as the escape hatch of last resort.

## Structure-aware chunking: the document already declares its units

The MediaWiki API hands over the editor-declared heading tree — article → `== section ==` →
`=== subsection ===` — and [convert](../stages/convert.md) preserved it as Markdown ATX
headings and blank-line-separated blocks (why parsing it faithfully matters is the
[corpus & parsing chapter](corpus-and-parsing.md)). **Structure-aware chunking** uses that
gift: it splits a document along its *own* structural elements instead of at arbitrary
offsets a character counter or a punctuation heuristic invents. When the author already told
you where one idea ends and the next begins, guessing is strictly worse.

Be honest about the scale, matching the corpus chapter: two or three heading levels of
encyclopedic prose is **lighter** structure than a deeply nested reference work (a legal code
or a technical manual) would offer. It is real, editor-declared structure — enough for the
lesson "structure beats fixed-size" to be genuine rather than a pretend exercise on a flat
toy corpus — and no more. Phase 2 spends that structure like this:

- **Chunk by section (the natural semantic unit).** Each level-2 `##` section — heading, its
  `###` subsections, and their prose — becomes one chunk, so a retrieved chunk is a complete,
  citable section. This is the default case, and for most of the corpus the only case: a
  section that fits the size budget is emitted whole.
- **Split oversized sections by subsection.** A section too long for the token window is
  divided at its `###` subsection boundaries — the structure the author *already* used to
  separate its topics — never mid-subsection. Each part re-leads with the `##` section
  heading, so it still names itself.
- **Recursive-character splitting as the fallback, not the default.** A single segment — a
  subsection with its paragraphs, or a section that has no subsections at all — that *alone*
  exceeds the budget has no finer declared structure to exploit, so here, and only here, the
  chunker falls back to the character-splitting baseline above, cutting on paragraph → line →
  sentence → word. Encyclopedic prose reaches this case far less often than a legal
  enumeration would, which is exactly why it is the fallback and not the primary strategy: the
  baseline earns its place as the escape hatch for when declared structure runs out.
- **Merge tiny sections.** The mirror image of splitting: consecutive whole sections shorter
  than the merge floor (400 characters) are combined into one chunk, so a two-sentence
  section does not become its own thin, contextless vector that floods retrieval with
  fragments. A split section or an above-floor section ends the run, and a merge never crosses
  the size budget.

The article title and section heading are kept as **metadata** — and folded into each chunk's
citation — not prepended to the chunk text. Folding ancestor context into the text itself is
*contextual enrichment*, deliberately deferred (below). Phase 2 draws the boundary at the
section's own heading plus body.

## Sliding window / overlap: context across the cut

Splitting a section reintroduces the very problem chunking tries to avoid — a topic whose
setup is in one part and whose payoff is in the next, so neither retrieves on the whole
thought. The countermeasure is **sliding window / overlap**: adjacent parts share some text,
so information sitting on a boundary appears in both and cannot fall between them.

The design choice is *what* to overlap, and Phase 2 uses two flavours matched to how the
part was cut:

- **A whole repeated segment.** When a section splits by subsection, each part after the
  first repeats the previous part's **final whole segment** (a `###` subsection with its
  paragraphs). Boundary context survives on clean structural lines with no severed sentence,
  at the cost of one duplicated segment.
- **A trailing character window.** The recursive-character fallback has no subsections to
  repeat, so each of its pieces after the first repeats the last ~120 characters (a tenth of
  the budget) of the previous piece — the same reasoning, one structural level down, where the
  cut itself is already character-based.

Either way the *no silent loss* guarantee accounts for the duplication by reconstructing the
original section body from the parts *minus* their overlap.

A toy split makes the whole-segment overlap concrete — a `## History` section whose two
subsections together overflow the size budget, cut into two parts:

```text
## History               both subsections together exceed the toy budget, so the section
### Foundation           splits by subsection; each part re-leads with the `## History`
Founded in 1886 …        heading, and each part after the first repeats the previous
### Modern era           part's final whole subsection as overlap:
Since 2000 …

  part 1                   part 2
  ## History               ## History          ← heading repeated on both parts
  ### Foundation           ### Foundation       ← repeated overlap (part 1's last segment)
  Founded in 1886 …        Founded in 1886 …
                           ### Modern era
                           Since 2000 …
```

## No silent loss: the machine-checked invariant

The overlap trade is only safe because it is *checked*, not trusted. A split section's parts,
with their duplicated overlap removed, must reconstruct the section body **verbatim** — an
invariant the tests exercise directly (the parts carry the bookkeeping needed to invert the
split). Nothing the chunker cannot place is dropped: any construct it fails to parse raises
`ChunkError` instead of silently vanishing, and the transform is **deterministic** — the same
corpus input yields byte-identical JSONL, asserted by golden-file tests
([code](../../src/rag/chunk/__init__.py), [contract](../stages/chunk.md)). Determinism plus
the reconstruction check is what lets Phase 2 claim faithfulness rather than best effort.

## Deliberately deferred to Backlog 6

Phase 2 is the honest structure-aware baseline; four advanced strategies build on it and are
scoped to Backlog 6, each measured against this baseline via the evaluate harness rather than
assumed better. They are named here and explained in Backlog 6's own chapter, not this one:

- **Semantic chunking** — split where embedding similarity between adjacent sentences drops,
  rather than on declared structure.
- **Hierarchical (parent-child) chunking** — retrieve small subsection-level children for
  precision, assemble their larger section parents for context.
- **Contextual enrichment** — prepend the article and section context, or an LLM-written
  situating summary, to each chunk before embedding, so it is retrievable in isolation
  (Phase 2's metadata fields are the raw material).
- **Late chunking** — embed a long window first, then pool token embeddings into per-chunk
  vectors that retain whole-document context. This needs a long-context model that exposes
  token embeddings — which the pinned 512-token bge-small-en-v1.5 is not — so it would also
  require a different embedder, not just new chunking code.

## Where this leaves the pipeline

The chunker turns convert's Markdown into `data/chunks/<slug>.jsonl` — one structure-aware,
self-identifying, citable record per retrieval unit, each carrying the metadata later stages
filter and cite on. The exact record schema, size thresholds, split/merge rules, and
guarantees are the [chunk stage contract](../stages/chunk.md); the code that implements them
is [`src/rag/chunk/`](../../src/rag/chunk/__init__.py). Phase 3 embeds these records and loads
them into the vector store — where the resolution chosen here becomes the resolution retrieval
can ever achieve.
