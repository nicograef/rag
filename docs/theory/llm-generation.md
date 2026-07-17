# LLM generation

Why producing a grounded German answer on a CPU is shaped by two very different costs, and
why the answer's trustworthiness rests entirely on the prompt — the theory behind the online
path's last two steps, [assemble](../stages/assemble.md)
([`src/rag/assemble/`](../../src/rag/assemble/__init__.py)) and
[generate](../stages/generate.md) ([`src/rag/generate/`](../../src/rag/generate/__init__.py)).
The stage contracts document *what* each stage does; this chapter is the *why*. Every concept
below is explained exactly once, here; the [concept map](../concepts.md) points to this
chapter as its place.

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
prefill cost, so [generate streams](../stages/generate.md) — the NDJSON deltas turn a
minutes-long wait into visible progress instead of an apparently hung tool (the read timeout
is disabled because a slow first token is expected, not a failure). And output length is
expensive by the token (below). Measured prefill and decode throughput on this machine live in
the dated [generation-model decision](../roadmap.md#decisions), not restated here.

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
why `num_ctx` costs memory — a bigger window is a bigger memory bill, one reason the window is
pinned at 8192 rather than maximized (see the [`num_ctx` pitfall](../stages/generate.md)).
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
[ system prompt ][ "Auszüge aus Gesetzestexten:" ]   ← identical every call: reusable prefix
[ [1] … [k] retrieved excerpts ]                      ← per question
[ "Frage: …" ]                                        ← per question, always last
```

At MVP each question retrieves different excerpts, so the reliably shared prefix is mostly the
system prompt — a modest but genuine saving. The layout costs nothing and is the discipline
that lets any prefix reuse happen at all.

## Weight quantization and GGUF: fitting the model in RAM

Model size is set by parameter count times bytes per weight. A 4-billion-parameter model is
roughly 8 GB at fp16 and 16 GB at fp32 — at or over the 16 GB floor once Postgres and a
question embedder must coexist. **LLM weight quantization (GGUF)** is what makes it fit: each
weight is stored at lower precision — the pinned build is 4-bit (Q4_K_M), shrinking the model
to roughly a quarter of its fp16 size. Fewer bits per weight also means fewer bytes to stream
per decode step, so quantization helps the memory-bandwidth wall too, not only the footprint.

**GGUF** is the single-file container llama.cpp (and therefore Ollama) loads: the quantized
weights plus tokenizer and metadata, memory-mappable, one file per model tag. The trade-off is
size and speed against accuracy — too few bits and the answers degrade; Q4_K_M is a common
sweet-spot scheme that keeps the more sensitive weights at higher precision to soften that
loss. The exact pinned tag and the reasoning are in the
[generation-model decision](../roadmap.md#decisions).

This is *not* the vector quantization of the [vector-indexes chapter](vector-indexes.md): same
idea — fewer bits per number — applied to the LLM's weights here, to the stored embedding
vectors there.

## Chain-of-thought, and why the pinned model doesn't

**Chain-of-thought (CoT)** prompts the model to write out its intermediate reasoning before
the final answer. For a small quantized model — which has less spare capacity than a large one
— spelling the steps out (which excerpt says what, how they combine) can genuinely raise
accuracy on multi-step questions over jumping straight to a conclusion.

The cost here is decisive. Reasoning tokens are output tokens, and output tokens are the
slowest resource on this hardware — every one is paid at decode speed. CoT can multiply the
number of generated tokens several-fold, multiplying answer latency in direct proportion. So
the pinned model is **Qwen3-4B-Instruct-2507**, deliberately a *non-thinking* build: it answers
directly, with no hidden reasoning phase — which is why generate sends no `think` field
([HTTP contract](../stages/generate.md)). Reasoning or "thinking" models are the trained
variant of the same idea — CoT baked into training rather than merely prompted — and spend
still more decode time before answering: the wrong trade on a CPU for a grounded lookup over
short legal excerpts.

## Groundedness and hallucination prevention: prompt-level rails

**Groundedness** is the property that an answer's claims are supported solely by the retrieved
excerpts, not the model's parametric memory. **Hallucination prevention** is the set of
techniques that push toward it. At MVP they live entirely in the German `SYSTEM_PROMPT` in
[`src/rag/assemble/`](../../src/rag/assemble/__init__.py), as five directives (wording last
refined against real model behaviour in the
[dated spot-check](../stages/generate.md#verification)):

- **Grounding** — „Beantworte die Frage ausschließlich anhand dieser Auszüge; nutze kein
  anderes Wissen." Answer only from the provided excerpts; use no outside knowledge.
- **Partial answers over blanket refusal** — „Wenn die Auszüge die Antwort nur teilweise
  enthalten, gib die Teilantwort und benenne, was offen bleibt." Added after the spot-check
  showed over-abstention: a strict grounding directive alone can make a small model refuse
  even when the excerpts do answer the question.
- **Abstention** — „Wenn sie die Antwort gar nicht enthalten, sage das klar und erfinde
  nichts." An explicit permission to say *I don't know*, so the model is not cornered into
  inventing an answer.
- **Citation-forcing** — „Belege jede Aussage mit der Nummer und Fundstelle des Auszugs …;
  übernimm die Fundstelle wörtlich …", e.g. `[1] (Art 1 GG)`: every claim carries the excerpt
  it rests on, so an ungrounded claim stands out as an uncited one — and the verbatim rule
  stops the model from re-labelling norms (Art vs §).
- **Answer-in-corpus-language, briefly** — „Antworte auf Deutsch und so knapp, wie es ohne
  Verlust des Wesentlichen möglich ist." The corpus, the § citations, and a small model's
  reliability limits all point one way; and brevity is a performance directive here — every
  answer token is paid at decode speed (above).

The honest limit: at MVP these prompt directives are the *only* rails. A directive is a
request, not a guarantee — a small quantized model can still drift, and nothing yet **checks**
at runtime whether an answer actually stayed grounded (a groundedness output check, CoVe-style,
is Backlog 9) nor **measures** how often it does (the RAG triad and retrieval metrics are
Backlog 1). Both live in the [enhancement backlog](../roadmap.md); until then, grounding
quality is anecdotal — the generate contract's dated spot-check, not a metric.

## Where this leaves the pipeline

The online path is retrieve → assemble → generate: assemble packs a grounded, stable-prefix
prompt, generate streams the answer while the CPU pays prefill once and then decode per token,
and the model's quantized GGUF weights plus its KV cache are what keep the whole serving side
inside the 16 GB floor. What the path deliberately does *not* yet do — verify or measure that
the answer stayed grounded — is the boundary this chapter is honest about, and the reason
Backlog 9 and Backlog 1 exist.
