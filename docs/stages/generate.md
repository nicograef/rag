# Stage contract: generate

> Code: [`src/rag/generate/`](../../src/rag/generate/__init__.py) ·
> Roadmap: [Phase 4 — Online PoC](../roadmap.md) ·
> Theory: [LLM generation](../theory/llm-generation.md)

Sends an assembled [`Prompt`](assemble.md#output) to the local Ollama server and streams
the answer back. Third and last step of the online path (`retrieve → assemble → generate`),
the only one that talks to the LLM. It is a **library function, not a stage with a CLI**:
[`rag.ask`](../../src/rag/ask/__init__.py) composes the three steps and owns the command line. Generation
is grounded only by the prompt — the model reads the retrieved excerpts assemble put in
front of it and nothing else.

## Entry point

```python
from rag.generate import generate, GenerateResult

result: GenerateResult = generate(prompt, on_delta=lambda delta: print(delta, end=""))
```

- `prompt` — the assemble stage's `Prompt` (its `system` and `user` strings become the two
  chat messages).
- `on_delta` — called with each incremental text delta **as it arrives**, so a caller can
  print tokens live; CPU prefill can take minutes before the first delta, which is the
  whole reason the stage streams (see the [theory chapter](../theory/llm-generation.md)).
- `transport` — an optional `httpx.BaseTransport` for tests (`httpx.MockTransport`); by
  default httpx opens a real connection.

Returns a `GenerateResult(answer, stats)`: the full reassembled `answer` and a
`GenerationStats` — `prompt_tokens`, `answer_tokens`, `load_seconds`,
`prompt_eval_seconds`, `eval_seconds`, `total_seconds`, `done_reason`.

## Configuration

The server address comes from the environment — the same variables the Compose stack uses,
read by `ollama_base_url()`: `OLLAMA_HOST` (default `localhost`) and `OLLAMA_PORT` (default
`11434`), giving `http://{host}:{port}`. See [`.env.example`](../../.env.example). No other
configuration mechanism exists (this mirrors load's `connection_conninfo()`).

The model tag and decoding parameters are **pinned module constants**, not configuration,
because the model, context length, and decoding parameters are one joint choice — the
[generation-model decision](../roadmap.md#decisions) (`granite4:micro`, decided
2026-07-18):

| Constant       | Value           | Meaning                                             |
| -------------- | --------------- | ---------------------------------------------------- |
| `MODEL_TAG`    | `granite4:micro` | The Ollama model tag                                |
| `NUM_CTX`      | 4096            | Context window sent per request (see the pitfall below) |
| `NUM_PREDICT`  | 512             | Max answer tokens (`num_predict`)                   |
| `TEMPERATURE`  | 0.0             | Sampling temperature — greedy decoding              |
| `TOP_P`        | 1.0             | Nucleus sampling cutoff (inert at temperature 0)    |
| `TOP_K`        | 0               | Top-k sampling cutoff (inert at temperature 0)      |
| `MIN_P`        | 0.0             | Minimum-probability cutoff (inert at temperature 0) |
| `SEED`         | 42              | Sampling seed (breaks the rare tie identically)     |

`MODEL_TAG`, `NUM_CTX`, and `NUM_PREDICT` are env-overridable (`LLM_MODEL_TAG`,
`LLM_NUM_CTX`, `LLM_NUM_PREDICT`) with the pinned value as the default; `make llm-pull`
derives its tag from the same constant instead of duplicating the string.

**Why greedy decoding.** The Granite model card gives no task-specific sampling guidance,
and a grounded, citation-bound task wants the single most-likely, reproducible answer over
sampled variety — not the case for every model family (see the
[generation-model decision](../roadmap.md#decisions) for the full reasoning). Temperature 0
makes `top_p`/`top_k`/`min_p` inert; the seed is pinned so the rare tie breaks the same way
across runs.

## HTTP contract

Verified 2026-07-18 against `ollama/ollama:0.32.1` and the official API docs
(`docs.ollama.com/api`, `github.com/ollama/ollama` `docs/api.md`), re-confirmed by the
empirical run in [Verification](#verification) below. This section is the contract the
client is written against; the [theory chapter](../theory/llm-generation.md) explains the
concepts.

**Endpoint.** `POST http://{host}:{port}/api/chat`.

**Request body.** The two messages and the pinned decoding options, streaming enabled:

```json
{
  "model": "granite4:micro",
  "messages": [
    {"role": "system", "content": "<Prompt.system>"},
    {"role": "user", "content": "<Prompt.user>"}
  ],
  "stream": true,
  "options": {
    "num_ctx": 4096, "num_predict": 512, "temperature": 0.0,
    "top_p": 1.0, "top_k": 0, "min_p": 0.0, "seed": 42
  }
}
```

The pinned model is a plain instruct build (Granite-4.0-Micro emits no reasoning traces),
so no `think` field is sent. Unknown `options` keys are **silently ignored** by the server,
so a typo in an option name fails open (no error, no effect) rather than loudly — worth
knowing when tuning.

**Streaming response.** `application/x-ndjson`: one JSON object per line. Non-final lines
carry an **incremental** content delta (not the cumulative answer):

```json
{"message": {"role": "assistant", "content": "Arsenal "}, "done": false}
```

The client forwards each non-empty `message.content` to `on_delta` and accumulates it. The
final line sets `"done": true` and adds `done_reason` and the run statistics. Durations are
in **nanoseconds**; the client divides by 1e9 for `GenerationStats`:

```json
{"message": {"role": "assistant", "content": ""}, "done": true,
 "done_reason": "stop", "total_duration": 58000000000, "load_duration": 5400000000,
 "prompt_eval_count": 840, "prompt_eval_duration": 51000000000,
 "eval_count": 11, "eval_duration": 1600000000}
```

`done_reason` is `"stop"` on a normal finish and `"length"` when the answer hit the
`num_predict` cap. `prompt_eval_count` is the prompt token count, `eval_count` the answer
token count. A response that only loaded the model (empty prompt) reports `done` without
the eval counters, so the client reads every stats field defensively (missing → 0).

**Error shapes.** An unknown model returns **HTTP 404** with body
`{"error": "model '<name>' not found"}`; a missing `model` field returns **400**
`{"error": "model is required"}`. A failure that happens mid-stream arrives as an
`{"error": ...}` NDJSON line rather than a status code.

## Streaming semantics

`stream: true` because CPU prefill latency is the dominant cost — the server can spend
minutes reading a long prompt before emitting the first answer token. Streaming turns that
into visible progress instead of a silent wait. Accordingly the httpx client sets a short
connect/write timeout (5 s) but **disables the read timeout** (`httpx.Timeout(5.0,
read=None)`): a slow first token is expected, not a failure. `keep_alive` is left at
Ollama's 5-minute default (the model stays warm between questions in an interactive
session).

**The `num_ctx` pitfall.** Ollama's context-window default varies by server version, so
`num_ctx` is sent explicitly on **every** request rather than trusting whatever the server
happens to default to — omitting it would leave the window at the mercy of that default. The
prompt-size budget that keeps a prompt inside this window lives in the
[assemble contract](assemble.md); token-exact counting with the served tokenizer is deferred
(Backlog 7).

## Failure behaviour

Every failure raises `GenerateError` with an actionable hint; nothing is retried.

| Situation                        | Message                                                            |
| --------------------------------- | ------------------------------------------------------------------ |
| Server unreachable               | `Ollama not reachable at {base_url}: {error} — run \`make llm\` first` |
| Model not pulled (HTTP 404)      | `model '{MODEL_TAG}' not found in Ollama — run \`make llm-pull\` first` |
| Other HTTP error                 | `Ollama request failed with HTTP {status}: {body}`                 |
| `{"error": ...}` line mid-stream | the server's error message, verbatim                               |
| Connection lost mid-stream       | `Ollama connection failed mid-request: {error} — check \`docker compose logs ollama\`` |
| Stream ended without a done line | `Ollama stream ended without a final done message`                 |

`httpx.ConnectError` and `httpx.ConnectTimeout` map to the unreachable hint; any other
transport failure — e.g. the server dying under memory pressure while decoding — maps to
the mid-request hint. Deltas already delivered to `on_delta` before a mid-stream failure
stay delivered — the caller decides how to present a partial answer (`rag.ask` finishes the
streamed line before printing the error).

## Verification

End-to-end spot-check (**2026-07-18**), CPU-only on an **8-core / 5.7 GB** machine — a
tighter RAM budget than the 4-core / 8 GB design floor — without swap: `make llm-pull`
pulled `granite4:micro`, then `make ask Q="Which stadium does Arsenal
play at?"` streamed a grounded, cited English answer —

> "Arsenal plays at the Emirates Stadium [3]."

— with an 840-token prompt, prefill ≈ 51 s and total ≈ 58 s (11 answer tokens). An
abstention probe, `make ask Q="What is the capital of France?"`, correctly declined:

> "the excerpts provided do not contain any information about the capital of France …
> impossible to answer this question"

**Honest reading.** Both runs stayed within a RAM budget tighter than the 8 GB floor,
without swap — prefill-bound, not swap-bound; a run on exact 4-core/8 GB hardware was not
performed (the floor is the design target, argued from the ≈ 2.1 GB served footprint). This
is a two-question spot-check, anecdotal by design — no thresholds, no metrics (the RAG triad
and rank metrics are Backlog 1), and a directive is a request, not a guarantee (runtime
groundedness checks are Backlog 9). Per-phase timings and
the full memory picture live in the [generation-model decision](../roadmap.md#decisions).

## Downstream consumers

[`rag.ask`](../../src/rag/ask/__init__.py) is the only caller: it retrieves, assembles, calls
`generate` with an `on_delta` that writes to stdout, then prints the numbered sources block
and a CC BY-SA licence notice for the Wikipedia excerpts, and logs the `GenerationStats` to
stderr.
