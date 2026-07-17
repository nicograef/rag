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
[generation-model decision](../roadmap.md#decisions) (`qwen3:4b-instruct`, decided
2026-07-17):

| Constant       | Value             | Meaning                                             |
| -------------- | ----------------- | --------------------------------------------------- |
| `MODEL_TAG`    | `qwen3:4b-instruct` | The Ollama model tag                              |
| `NUM_CTX`      | 8192              | Context window sent per request (see the pitfall below) |
| `NUM_PREDICT`  | 1024              | Max answer tokens (`num_predict`)                   |
| `TEMPERATURE`  | 0.7               | Sampling temperature                                |
| `TOP_P`        | 0.8               | Nucleus sampling cutoff                             |
| `TOP_K`        | 20                | Top-k sampling cutoff                               |
| `MIN_P`        | 0.0               | Minimum-probability cutoff                          |
| `SEED`         | 42                | Sampling seed (repeatable, not bitwise-deterministic) |

## HTTP contract

Verified 2026-07-17 against `ollama/ollama:0.32.1` and the official API docs
(`docs.ollama.com/api`, `github.com/ollama/ollama` `docs/api.md`). This section is the
contract the client is written against; the [theory chapter](../theory/llm-generation.md)
explains the concepts.

**Endpoint.** `POST http://{host}:{port}/api/chat`.

**Request body.** The two messages and the pinned decoding options, streaming enabled:

```json
{
  "model": "qwen3:4b-instruct",
  "messages": [
    {"role": "system", "content": "<Prompt.system>"},
    {"role": "user", "content": "<Prompt.user>"}
  ],
  "stream": true,
  "options": {
    "num_ctx": 8192, "num_predict": 1024, "temperature": 0.7,
    "top_p": 0.8, "top_k": 20, "min_p": 0.0, "seed": 42
  }
}
```

The pinned model is non-thinking by design (Qwen3-4B-Instruct-2507), so no `think` field is
sent. Unknown `options` keys are **silently ignored** by the server, so a typo in an option
name fails open (no error, no effect) rather than loudly — worth knowing when tuning.

**Streaming response.** `application/x-ndjson`: one JSON object per line. Non-final lines
carry an **incremental** content delta (not the cumulative answer):

```json
{"message": {"role": "assistant", "content": "Nach "}, "done": false}
```

The client forwards each non-empty `message.content` to `on_delta` and accumulates it. The
final line sets `"done": true` and adds `done_reason` and the run statistics. Durations are
in **nanoseconds**; the client divides by 1e9 for `GenerationStats`:

```json
{"message": {"role": "assistant", "content": ""}, "done": true,
 "done_reason": "stop", "total_duration": 8710000000, "load_duration": 540000000,
 "prompt_eval_count": 352, "prompt_eval_duration": 5100000000,
 "eval_count": 74, "eval_duration": 3020000000}
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

**The `num_ctx` pitfall.** Ollama's default context on CPU is 4096 (a VRAM-tiered default),
below this prompt's budget. `num_ctx` is therefore sent on **every** request — omitting it
would silently clip the context. The prompt-size budget that keeps a prompt inside this
window lives in the [assemble contract](assemble.md); token-exact counting with the served
tokenizer is deferred (Backlog 7).

## Failure behaviour

Every failure raises `GenerateError` with an actionable hint; nothing is retried.

| Situation                        | Message                                                            |
| -------------------------------- | ------------------------------------------------------------------ |
| Server unreachable               | `Ollama not reachable at {base_url}: {error} — run \`make llm\` first` |
| Model not pulled (HTTP 404)      | `model '{MODEL_TAG}' not found in Ollama — run \`make llm-pull\` first` |
| Other HTTP error                 | `Ollama request failed with HTTP {status}: {body}`                 |
| `{"error": ...}` line mid-stream | the server's error message, verbatim                               |
| Connection lost mid-stream       | `Ollama connection failed mid-request: {error} — check \`docker compose logs ollama\`` |
| Stream ended without a done line | `Ollama stream ended without a final done message`                 |

`httpx.ConnectError` and `httpx.ConnectTimeout` map to the unreachable hint; any other
transport failure — e.g. the server dying under memory pressure while decoding — maps to
the mid-request hint. Deltas
already delivered to `on_delta` before a mid-stream failure stay delivered — the caller
decides how to present a partial answer (`rag.ask` finishes the streamed line before
printing the error).

## Verification

End-to-end spot-check (**2026-07-17**): with the full corpus loaded (1,225 chunks — the
same-day pipeline re-run recorded in the README status) and the pinned model served by the
Compose service, five hand-written questions ran through `make ask` on an 8-core / 5.7 GB
CPU-only machine — below the 16 GB floor; throughput and memory numbers live in the
[generation-model decision](../roadmap.md#decisions). Four questions with known expected
§§, one abstention probe (Mietrecht — the corpus holds no BGB):

| Question | Expected | Rank-1 hit (distance) | Answer behaviour |
| --- | --- | --- | --- |
| „Wie müssen elektronische Kassen vor Manipulation geschützt werden?" | AO § 146a | **§ 146a AO** (0.4013) | Grounded summary of § 146a's requirements, cited `[1] (§ 146a AO)` |
| „Wann entsteht die Umsatzsteuer?" | UStG § 13 | **§ 13 UStG** (0.3185) | Grounded per-case enumeration citing `[1]` § 13, `[2]` § 13b, `[5]` § 21 UStG — hit the `num_predict` cap (`done_reason: length`) |
| „Ist die Würde des Menschen antastbar?" | GG Art 1 | **Art 1, Art 2 GG** (0.4350) | Correct — but the first round re-labelled the norm as „§ 1 Absatz 1 GG" |
| „Wer ist zum Vorsteuerabzug berechtigt?" | UStG § 15 | **§ 15 UStG** (0.3795; four of five hits are § 15) | First round over-abstained: refused although § 15 Abs. 1 answers the question |
| „Wann darf mein Vermieter die Miete erhöhen?" (probe) | — not in corpus | § 24 UStG (0.4931 — distant noise) | **Clean abstention**: states the excerpts contain no Mietrecht rules and answers nothing |

**Prompt refinement (same day).** The first round confirmed grounding and abstention but
exposed two failure modes — over-abstention and re-labelled citations (Art → §) — plus
costly over-enumeration. The `SYSTEM_PROMPT` gained the partial-answer,
verbatim-Fundstelle, and brevity directives (assemble's golden files re-pinned in the same
change; the [theory chapter](../theory/llm-generation.md) explains each), and the affected
questions re-ran:

- Würde: „Nein, die Würde des Menschen ist nicht antastbar. [1] (Art 1 GG)" — verbatim
  label, one sentence (25 answer tokens, previously 71).
- Vorsteuerabzug: a partial answer — the Unternehmer with a §§ 14, 14a invoice per
  `[1] (§ 15 UStG)`, the § 18 UStG constraint for businesses outside the
  Gemeinschaftsgebiet, and an explicit statement of what the excerpts leave open.
- Miete probe: still a clean abstention under the softened wording.

**Honest reading.** Retrieval put the expected norm at rank 1 in all four answerable
cases; the answers are grounded and cited; the probe abstains. This is anecdotal by
design — no thresholds, no metrics (the RAG triad and rank metrics are Backlog 1), and a
directive is a request, not a guarantee (runtime groundedness checks are Backlog 9).
Answer latency on this machine is minutes per question — per-phase numbers and the
coexistence-memory picture are in the decision entry.

## Downstream consumers

[`rag.ask`](../../src/rag/ask/__init__.py) is the only caller: it retrieves, assembles, calls `generate`
with an `on_delta` that writes to stdout, prints the sources block, and logs the
`GenerationStats` to stderr.
