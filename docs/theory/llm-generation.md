# LLM generation

Why producing a grounded English answer on a CPU is shaped by two very different costs, and
why the answer's trustworthiness rests entirely on the prompt — the theory behind the online
path's last two steps, [assemble](../stages/assemble.md)
([`src/rag/assemble/`](../../src/rag/assemble/__init__.py)) and
[generate](../stages/generate.md) ([`src/rag/generate/`](../../src/rag/generate/__init__.py)),
wired together by [`rag.ask`](../../src/rag/ask/__init__.py). The stage contracts document
*what* each stage does; this chapter is the *why*. Every concept below is explained exactly
once, here; the [concept map](../concepts.md) points to this chapter as its place.

## Prefill vs decode: two speeds on one CPU

Generating an answer is two phases with completely different performance, and confusing them
is the fastest way to misread the latency.

**Prefill** reads the prompt. All its tokens are processed in one parallel batch pass: a
single forward sweep over the whole model produces the attention state for every prompt token
at once, so the model's weights are loaded from RAM once and amortized across thousands of
tokens. That makes prefill compute-heavy but bandwidth-efficient — and it is where the *first*
token is born. A several-thousand-token prompt means a several-thousand-token forward pass
before a single character of answer exists, which on a CPU is the dominant wait: the
time-to-first-token.

**Decode** produces the answer, one token at a time. Each new token is its own forward pass,
and because it computes just one token, the entire set of model weights must stream from RAM
for almost no arithmetic in return — decode is strictly **memory-bandwidth-bound**, capped by
how fast the CPU can read the weights, not by how fast it can multiply. Every answer token is
paid at this slow, steady rate.

Two consequences the pipeline is built around. A long prompt front-loads a large, silent
prefill cost, so [generate streams](../stages/generate.md) — the NDJSON deltas turn a long
silent wait into visible progress instead of an apparently hung tool (the read timeout is
disabled because a slow first token is expected, not a failure). And output length is
expensive by the token (below). As an order of magnitude on this hardware, a ≈ 840-token
prompt spent ≈ 51 s in prefill before the first token, then ≈ 1.6 s decoding a short football
answer — the prompt sweep, not the answer, dominated the wait. The full dated throughput lives
in the [generation-model decision](../roadmap.md#decisions), not restated here.

## KV caching: why decode doesn't re-read the prompt every token

Attention lets every token look back at all earlier tokens. Naively, generating token *N*
would recompute the keys and values of all *N−1* tokens before it — the prefix re-read from
scratch on every output token. **KV caching** removes that: the key and value tensors of every
token already processed (the whole prompt, then each token as it is emitted) are stored, and a
new token computes only its own query and attends against the cached keys and values. Prefill's
real job, then, is to fill the KV cache for the entire prompt in one batch pass; decode only
appends one entry per step — which is exactly why prefill is a one-time cost and decode is
cheap per token.

The cache is not free: it grows linearly with the number of tokens in context (per layer, per
attention head), so reserving a context window reserves KV-cache RAM for all of it. That is
one reason `num_ctx` is kept modest rather than maximized (the context budget below).
llama.cpp inside Ollama manages the whole KV cache: there is nothing to build here, only to
understand.

## Prompt caching: reusing a prefix across questions

**Prompt caching** on a metered cloud API is a billing feature — you mark a static prompt
prefix and pay less (and wait less) whenever a request reuses it. That variant is inapplicable
here: this playbook serves its own model, and nothing is metered. The local analogue is real,
though: llama.cpp keeps the KV state of the previous request and, when the next request begins
with the *same* tokens, reuses that computed state instead of re-prefilling it. A cache can
only reuse a prefix up to the first token that differs — one changed token near the front
invalidates everything after it.

That is the whole reason the [assemble stage](../stages/assemble.md) puts everything static
first and the per-question parts last:

```text
[ system prompt ][ "Sources:" ]        ← identical every call: reusable prefix
[ [1] … [k] retrieved excerpts ]       ← per question
[ "Question: …" ]                      ← per question, always last
```

At MVP each question retrieves different excerpts, so the reliably shared prefix is mostly the
system prompt — a modest but genuine saving. The layout costs nothing and is the discipline
that lets any prefix reuse happen at all.

## Weight quantization and GGUF: fitting the model in RAM

Model size is set by parameter count times bytes per weight. granite4:micro's ≈ 3 billion
parameters are roughly 6 GB at fp16 — already too much to serve alongside Postgres and a
question embedder inside the 4-core/8 GB floor. **LLM weight quantization (GGUF)** is what
makes it fit: each weight is stored at lower precision — the pinned build is 4-bit (Q4_K_M),
shrinking the model to ≈ 2.1 GB, roughly a third of its fp16 size. Fewer bits per weight also
means fewer bytes to stream per decode step, so quantization helps the memory-bandwidth wall
too, not only the footprint.

At ≈ 2.1 GB served, granite4:micro fits the 4-core/8 GB floor **without swap**, with room for
Postgres and the embedder beside it — the win over a heavier model is swap-avoidance, not raw
speed: a served size over the RAM budget only runs through swap, and floor-bound throughput
follows from exactly that.

**GGUF** is the single-file container llama.cpp (and therefore Ollama) loads: the quantized
weights plus tokenizer and metadata, memory-mappable, one file per model tag. The trade-off is
size and speed against accuracy — too few bits and the answers degrade; Q4_K_M is a common
sweet-spot scheme that keeps the more sensitive weights at higher precision to soften that
loss. The exact pinned tag, sizes, and measured throughput are in the
[generation-model decision](../roadmap.md#decisions).

This is *not* the vector quantization of the [vector-indexes chapter](vector-indexes.md): same
idea — fewer bits per number — applied to the LLM's weights here, to the stored embedding
vectors there.

## The context budget: fitting prompt and answer in one window

`num_ctx` is a single budget the prompt **and** the answer share: the window must hold every
prompt token *and* leave room for every token the model will generate. Assemble enforces that
split before generate ever runs. It caps the prompt at a character budget derived from the
window — `num_ctx` minus a reserve equal to `num_predict` (the answer's token cap), converted
to characters by a conservative chars-per-token floor — and, decisively, **never truncates**:
an over-budget prompt is a loud `AssembleError`, not a silently shortened one.

Characters stand in for tokens because the prompt is built from characters while the model
reasons in tokens, and exact token counting with the served tokenizer is deferred (Backlog 7).
The floor is measured, not guessed — the same characters-versus-tokens argument the
[chunking chapter](chunking.md#characters-versus-tokens-why-the-size-cap-is-load-bearing-now)
makes for the chunk-size cap, applied again to the prompt cap. The pinned floor and the derived character cap live in the
[assemble contract](../stages/assemble.md) and
[`src/rag/assemble/`](../../src/rag/assemble/__init__.py).

The served window is pinned to **`num_ctx` 4096**.
granite4:micro's native context is 128 K tokens, so 4096 is a deliberate CPU budget, not a
model limit: a small window keeps the KV cache cheap and the prefill sweep short, and the
football prompts it must hold are short — five retrieved sections and a question, with no
oversized table to accommodate. `num_ctx` is sent explicitly on every request to pin the
window regardless of the server's default, which varies by Ollama version (the `num_ctx` note
in the [generate contract](../stages/generate.md)).

## Decoding: greedy over sampling

Every output token is chosen from a probability distribution the model produces over its whole
vocabulary. **Greedy decoding** takes the single highest-probability token at each step —
deterministic and reproducible. **Sampling** instead draws from that distribution, tuned by
temperature (how much to flatten it), top_p (nucleus: keep the smallest set of tokens summing
to *p*), and top_k (keep the *k* most likely) — the knobs that buy a chatbot's variety and
creativity at a small risk of drawing a worse token.

A grounded, citation-bound lookup wants neither variety nor creativity: it wants the one
most-likely answer, and it wants the same answer twice for the same excerpts. So generate pins
**temperature 0** — greedy decoding. The Granite model card offers no task-specific sampling
profile to prefer, which makes greedy the safe default rather than a guessed one. At
temperature 0 the top_p and top_k knobs are inert (there is nothing to sample from), and a
pinned seed makes the rare probability tie break the same way run to run. The decoding
constants live in [`src/rag/generate/`](../../src/rag/generate/__init__.py); the reasoning is
in the [generation-model decision](../roadmap.md#decisions).

## Chain-of-thought, and why the pinned model answers directly

**Chain-of-thought (CoT)** prompts the model to write out its intermediate reasoning before
the final answer. For a small quantized model — which has less spare capacity than a large one
— spelling the steps out (which excerpt says what, how they combine) can genuinely raise
accuracy on multi-step questions over jumping straight to a conclusion.

The cost here is decisive. Reasoning tokens are output tokens, and output tokens are the
slowest resource on this hardware — every one is paid at decode speed. CoT can multiply the
number of generated tokens several-fold, multiplying answer latency in direct proportion. So
the pinned model is a **plain instruct build** with no hidden reasoning phase (no `<think>`
traces), which is why generate sends no `think` field ([HTTP contract](../stages/generate.md))
and the assemble prompt asks for a direct, cited answer rather than a worked-through one.
Reasoning or "thinking" models bake CoT into training and spend still more decode time before
answering: the wrong trade on a CPU for a grounded lookup over short Wikipedia excerpts.

## Groundedness and hallucination prevention: prompt-level rails

**Groundedness** is the property that an answer's claims are supported solely by the retrieved
excerpts, not the model's parametric memory. **Hallucination prevention** is the set of
techniques that push toward it. At MVP they live entirely in the English `SYSTEM_PROMPT` in
[`src/rag/assemble/`](../../src/rag/assemble/__init__.py), as five directives:

- **Grounding** — "Answer strictly from these excerpts; do not use any other knowledge."
  Answer only from the provided excerpts; draw on no parametric memory.
- **Partial answers over blanket refusal** — "If the excerpts answer the question only in
  part, give the partial answer and say what is missing." A strict grounding directive alone
  can make a small model over-abstain — refuse even when the excerpts *do* answer the question
  — so this directive licenses the partial answer.
- **Abstention** — "If they do not contain the answer at all, say so plainly and invent
  nothing." An explicit permission to say *I don't know*, so the model is not cornered into
  inventing. Worked example: ask *"What is the capital of France?"* against a corpus of
  football-club excerpts and the model correctly declines — it states the excerpts do not
  answer the question rather than replying "Paris" from memory. The model plainly *knows* the
  answer; the rail is that it must not use knowledge the excerpts do not supply.
- **Citation-forcing** — "Support every statement with the number and citation of the excerpt
  it rests on, copying the citation verbatim — for example: [1] (Arsenal F.C. — History)."
  Every claim carries the excerpt it rests on, so an ungrounded claim stands out as an uncited
  one — and the verbatim rule stops the model from re-labelling its source article.
- **Answer-in-corpus-language, briefly** — "Answer in English, as concisely as possible
  without losing what matters." The corpus and its audience are English; and brevity is a
  performance directive here — every answer token is paid at decode speed (above).

The honest limit: at MVP these prompt directives are the *only* rails. A directive is a
request, not a guarantee — a small quantized model can still drift, and nothing yet **checks**
at runtime whether an answer actually stayed grounded (a groundedness output check, CoVe-style,
is Backlog 9) nor **measures** how often it does (the RAG triad and retrieval metrics are
Backlog 1). Both live in the [enhancement backlog](../roadmap.md); until then, grounding
quality is anecdotal — the generate contract's dated
[spot-check](../stages/generate.md#verification), not a metric.

## Citation as attribution

The citation the model must copy is not a bare index — it is the excerpt's article-and-section
label, e.g. `Arsenal F.C. — History`. That label does double duty. As a grounding device it is
the anti-hallucination rail above: a claim without a citation is a claim without a source. As
an **attribution** it discharges the corpus licence. The corpus is English Wikipedia, whose
text is **CC BY-SA 4.0**, and surfacing a retrieved excerpt in an answer is a reproduction the
licence requires be credited to its source. The system prompt's closing directive makes that
explicit — "attribute each fact to its source article through that citation" — so the in-answer
citation already names the article every fact came from.

The obligation is then closed at the point of display: after the streamed answer,
[`rag.ask`](../../src/rag/ask/__init__.py) prints a numbered `Sources:` block pairing each
citation with its article URL, followed by a licence notice ("Excerpts from English Wikipedia,
licensed CC BY-SA 4.0."). Attribution lives where the reader sees it, not only inside the
prompt.

## Where this leaves the pipeline

The online path is retrieve → assemble → generate: assemble packs a grounded, stable-prefix
prompt, generate streams the answer while the CPU pays prefill once and then decode per token,
and the model's quantized GGUF weights plus its KV cache are what keep the whole serving side
inside the 4-core/8 GB floor without swap. What the path deliberately does *not* yet do —
verify or measure that the answer stayed grounded — is the boundary this chapter is honest
about, and the reason Backlog 9 and Backlog 1 exist.
