"""Generate stage — send an assembled prompt to Ollama and stream the answer back.

The last step of the online path: takes the assemble stage's ``Prompt``, calls the local
Ollama server's ``/api/chat`` endpoint with streaming enabled, and reassembles the answer
from the NDJSON delta stream while forwarding each delta to an ``on_delta`` callback — so a
CLI can print tokens as the CPU produces them (prefill alone can take minutes). Returns the
full answer plus the run's ``GenerationStats`` (token counts and phase timings the server
reports on the final line). No standalone CLI: ``rag.ask`` composes retrieve → assemble →
generate.

The model tag, context length, and decoding parameters are pinned as module constants
because they are one joint decision (the values live here, the reasoning lives in the
decision entry); ``OLLAMA_HOST``/``OLLAMA_PORT`` are runtime configuration read from the
environment, exactly like ``rag.load.connection_conninfo()`` reads the database settings.

Stage contract: docs/stages/generate.md
Theory: docs/theory/llm-generation.md
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    # Type-only import: generate reads prompt.system/.user by duck typing and never
    # constructs a Prompt, so it needs the type only for annotations. Keeping it out of the
    # runtime import graph breaks the cycle with rag.assemble, which imports this module's
    # budget constants (NUM_CTX, NUM_PREDICT) at load time.
    from rag.assemble import Prompt

# Pinned by the dated LLM decision in docs/roadmap.md ("Generation model", 2026-07-18):
# model, context length, and decoding parameters form one context budget and are chosen
# together — the reasoning lives in the decision entry, the values live here. The model tag
# and context/answer budget are env-overridable with the pinned value as the default.
MODEL_TAG = os.environ.get("LLM_MODEL_TAG", "granite4:micro")
NUM_CTX = int(os.environ.get("LLM_NUM_CTX", "4096"))
NUM_PREDICT = int(os.environ.get("LLM_NUM_PREDICT", "512"))
# Greedy decoding: the Granite card gives no task-specific sampling guidance, and a grounded
# citation task wants the single most-likely, reproducible answer — not sampled variety. At
# temperature 0 top_p/top_k are inert; the seed is pinned so the rare tie breaks the same way.
TEMPERATURE = 0.0
TOP_P = 1.0
TOP_K = 0
MIN_P = 0.0
SEED = 42

# CPU prefill can take minutes before the first token, so the read timeout is disabled;
# connect/write/pool keep a short bound so an unreachable server fails fast.
CONNECT_TIMEOUT_SECONDS = 5.0

# The server reports durations in nanoseconds; the stats dataclass exposes seconds.
NANOSECONDS_PER_SECOND = 1e9


class GenerateError(Exception):
    """Raised when Ollama cannot produce an answer; the message carries the actionable hint."""


@dataclass(frozen=True)
class GenerationStats:
    """The final line's accounting: token counts and per-phase timings of one generation."""

    prompt_tokens: int
    answer_tokens: int
    load_seconds: float
    prompt_eval_seconds: float
    eval_seconds: float
    total_seconds: float
    done_reason: str


@dataclass(frozen=True)
class GenerateResult:
    """Generate's output: the full answer and the run's stats."""

    answer: str
    stats: GenerationStats


def ollama_base_url() -> str:
    """``http://{OLLAMA_HOST:-localhost}:{OLLAMA_PORT:-11434}`` — mirrors connection_conninfo()."""
    host = os.environ.get("OLLAMA_HOST", "localhost")
    port = os.environ.get("OLLAMA_PORT", "11434")
    return f"http://{host}:{port}"


def _chat_payload(prompt: "Prompt") -> dict[str, Any]:
    """The ``/api/chat`` request body: the two messages plus the pinned decoding options.

    ``num_ctx`` is sent explicitly to pin the context window regardless of the server's
    default (which varies by Ollama version). Unknown ``options`` keys are ignored by the server.
    """
    return {
        "model": MODEL_TAG,
        "messages": [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
        "stream": True,
        "options": {
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "min_p": MIN_P,
            "seed": SEED,
        },
    }


def _stats_from(final: dict[str, Any]) -> GenerationStats:
    """Read the final ``done`` line's stats, converting the nanosecond durations to seconds.

    ``.get(..., 0)`` is defensive: a response that only loaded the model (empty prompt)
    reports ``done`` without the eval counters, and we want zeros rather than a KeyError.
    """
    return GenerationStats(
        prompt_tokens=final.get("prompt_eval_count", 0),
        answer_tokens=final.get("eval_count", 0),
        load_seconds=final.get("load_duration", 0) / NANOSECONDS_PER_SECOND,
        prompt_eval_seconds=final.get("prompt_eval_duration", 0) / NANOSECONDS_PER_SECOND,
        eval_seconds=final.get("eval_duration", 0) / NANOSECONDS_PER_SECOND,
        total_seconds=final.get("total_duration", 0) / NANOSECONDS_PER_SECOND,
        done_reason=final.get("done_reason", ""),
    )


def _stream_answer(
    client: httpx.Client, url: str, payload: dict[str, Any], on_delta: Callable[[str], None]
) -> GenerateResult:
    """POST the payload and fold the NDJSON stream into a ``GenerateResult``.

    Each non-final line carries an incremental content delta (not cumulative); the final
    line carries ``done: true`` and the stats. Any ``error`` line — a missing model, or a
    mid-stream failure — aborts with its message.
    """
    with client.stream("POST", url, json=payload) as response:
        if response.status_code != 200:
            body = response.read().decode("utf-8", errors="replace")
            if response.status_code == 404:
                raise GenerateError(
                    f"model '{MODEL_TAG}' not found in Ollama — run `make llm-pull` first"
                )
            raise GenerateError(f"Ollama request failed with HTTP {response.status_code}: {body}")

        answer: list[str] = []
        for line in response.iter_lines():
            if not line:
                continue
            message = json.loads(line)
            if "error" in message:
                raise GenerateError(message["error"])
            delta = message.get("message", {}).get("content", "")
            if delta:
                on_delta(delta)
                answer.append(delta)
            if message.get("done"):
                return GenerateResult(answer="".join(answer), stats=_stats_from(message))

    raise GenerateError("Ollama stream ended without a final done message")


def generate(
    prompt: "Prompt",
    *,
    on_delta: Callable[[str], None],
    transport: httpx.BaseTransport | None = None,
) -> GenerateResult:
    """Stream one answer from Ollama for ``prompt``, forwarding each delta to ``on_delta``.

    ``transport`` is injectable for tests (``httpx.MockTransport``); by default httpx opens
    a real connection to :func:`ollama_base_url`. A connection failure is reported with the
    base URL and the ``make llm`` hint; HTTP and stream-shape errors raise ``GenerateError``
    with the exact hint (see the stage contract).
    """
    base_url = ollama_base_url()
    payload = _chat_payload(prompt)
    timeout = httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=None)
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            return _stream_answer(client, f"{base_url}/api/chat", payload, on_delta)
    except (httpx.ConnectError, httpx.ConnectTimeout) as error:
        raise GenerateError(
            f"Ollama not reachable at {base_url}: {error} — run `make llm` first"
        ) from error
    except httpx.TransportError as error:
        # Reached the server but lost it mid-request — e.g. the container died under
        # memory pressure while decoding. Distinct from never reaching it (above).
        raise GenerateError(
            f"Ollama connection failed mid-request: {error} — check `docker compose logs ollama`"
        ) from error
