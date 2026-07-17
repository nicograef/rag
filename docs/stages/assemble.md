# Stage contract: assemble

> Code: [`src/rag/assemble/`](../../src/rag/assemble/__init__.py) ·
> Roadmap: [Phase 4 — Online PoC](../roadmap.md) ·
> Theory: [LLM generation](../theory/llm-generation.md)

Turns a question and retrieve's ranked chunks into the grounded chat `Prompt` the generator
sends to Ollama. Second stage of the online path (**retrieve → assemble → generate**): it
does the *prompt assembly / context packing* — formatting instructions, the numbered
excerpts, and the question into one stable, deterministic prompt. A pure function: no I/O,
no configuration, no CLI. The prompt's size cap is pinned to the generation model's context
window from the [generation-model decision](../roadmap.md#decisions).

## Entry point

A pure function — [`rag.ask`](../../src/rag/ask/__init__.py) composes the online path and
calls it:

```python
from rag.assemble import assemble

prompt = assemble(question, chunks)   # chunks: the ranked list retrieve returned
```

`assemble(question: str, chunks: Sequence[RetrievedChunk]) -> Prompt` returns the `system`
and `user` message strings for the chat API. There is deliberately no standalone command:
assemble reads no files and has nothing to inspect that the `ask` step's own logging does
not already surface.

## Inputs

- **`question`** — the user's question, verbatim.
- **`chunks`** — retrieve's ranked [`RetrievedChunk`](retrieve.md) list, in ascending-distance
  order. Of each chunk assemble consumes exactly two fields: **`citation`** (the excerpt's
  label, e.g. `Arsenal F.C. — History`) and **`text`** (the excerpt body). The other fields
  (`id`, `source_title`, `source_url`, `distance`) are retrieve's contract for the `ask` step's
  source list and logging, not for assemble.

## Output

A frozen [`Prompt`](../../src/rag/assemble/__init__.py) — two strings for the chat API:

- **`system`** — the fixed English `SYSTEM_PROMPT`: the *groundedness* directive (answer only
  from these excerpts, use no other knowledge), the partial-answer directive (answer what the
  excerpts do cover and name what stays open), the abstention directive (say so plainly when
  they contain no answer at all — *hallucination prevention*), a brevity directive (answer
  tokens are the slowest CPU resource), the verbatim citation format `[n] (citation)` — e.g.
  `[1] (Arsenal F.C. — History)` — and a **CC BY-SA attribution** directive: the excerpts are
  Wikipedia text licensed CC BY-SA 4.0, so every fact the answer surfaces must be attributed to
  its source article through that citation. Identical on every call; verified against real
  model behaviour 2026-07-18 (see the [generate contract's verification](generate.md#verification)).
- **`user`** — the numbered excerpts in rank order, the question last:

```
Sources:

[1] {citation}
{text}

[2] {citation}
{text}

Question: {question}
```

Excerpts are numbered `[1]..[n]` in the order given (not re-sorted), blocks are joined by a
blank line, and there is no trailing newline. Everything static — the system prompt and the
`Sources:` prefix — comes first and the per-question part comes last, so the served model can
reuse the computed prefix across questions ([prompt caching / KV
caching](../theory/llm-generation.md) — named here, explained there). Re-ordering the
excerpts against the *lost-in-the-middle* effect stays out of scope (Backlog 7).

## Guarantees

- **Deterministic.** The same question and chunks produce a byte-identical `Prompt` — the
  layout carries no timestamps, set iteration, or other nondeterminism. This is what lets the
  [golden-prompt tests](../../tests/test_assemble.py) compare byte-for-byte against checked-in
  fixtures under `tests/fixtures/prompts/`.
- **Verbatim excerpts.** Chunk `text` enters the prompt exactly as retrieve returned it —
  assemble neither trims, escapes, nor reformats it.

## Context budget guard

The prompt must leave room in the context window for the answer, so assemble caps its size
and — decisively — **never truncates**: an over-budget prompt is a loud error, not a silently
shortened one. The cap is character-based, a conservative stand-in for token-exact counting:

| Constant | Value | Meaning |
| --- | --- | --- |
| `NUM_CTX` | 4096 | the pinned model's context window (imported from generate) |
| `GENERATION_RESERVE_TOKENS` | 512 | `= NUM_PREDICT`; the answer must fit in `num_ctx` too |
| `CHARS_PER_TOKEN_FLOOR` | 2.3 | chars per token, pinned one notch under the measured floor |
| `MAX_PROMPT_CHARS` | 8243 | `int((NUM_CTX − reserve) × floor)`; the cap on `len(system) + len(user)` |

**Why 2.3 (measured 2026-07-18).** The corpus tokenized with the served model's own tokenizer
(granite-4.0-micro) runs the densest chunk at ≈ 2.31 chars/token; pinning the floor one notch
under it (2.3) keeps the character cap a safe under-estimate of the real token count even in
that worst case. A realistic top-5 prompt is ≈ 1,040 tokens — far under the 3,584-token budget
(`NUM_CTX` minus `NUM_PREDICT`) — so the guard trips only on genuinely oversized inputs.
Token-exact counting with the served tokenizer is deliberately Backlog 7 — this guard is the
MVP stand-in, tuned to fail safe rather than to pack the window maximally.

## Failure behaviour

`AssembleError` in two cases, each carrying the actionable hint:

- **No chunks** — `assemble needs at least one retrieved chunk`. A grounded prompt with
  nothing to ground on is meaningless; retrieve returning zero rows already fails upstream
  with its own hint, so this guards direct callers.
- **Over budget** — the message names the actual size, the budget, its derivation, and the
  fix, e.g. `prompt is 9000 characters, over the 8243-character context budget (4096 num_ctx
  minus 512 answer-reserve tokens at 2.3 chars/token) — retry with a smaller --top-k`.

Both are raised before a `Prompt` is returned; assemble has no partial output.

## Prompt-injection boundary

Retrieved chunks enter the user message **verbatim** — a chunk whose text happened to contain
instruction-like phrasing ("ignore the above and …") would be passed through unchanged. Input,
retrieval, and output rails against *prompt injection* are deliberately not built here: they
are Backlog 9. At MVP the corpus is trusted (English Wikipedia article text, fetched read-only
from the MediaWiki API), and the grounding and abstention directives in the system prompt are
the only rail.

## Downstream consumers

**[generate](generate.md)** sends the `Prompt` to Ollama's `/api/chat` endpoint as the
`system` and `user` messages. **[ask](../../src/rag/ask/__init__.py)** — the online-path
composition — runs retrieve → assemble → generate in order and logs the assembled prompt's
size (and, with `--verbose`, the full messages).
