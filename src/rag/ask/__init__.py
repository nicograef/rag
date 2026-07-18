"""Ask — the composition entry point of the online path (retrieve → assemble → generate).

Not a fourth stage: a thin CLI that wires the three online stages into one command. It
retrieves the top-k chunks for a question, assembles the grounded prompt, and streams the
model's answer to stdout token by token, then prints the numbered ``Sources:`` block and a
CC BY-SA licence notice for the Wikipedia excerpts it drew on. Every step logs one line to
stderr — the question, each hit, the prompt
size, and the final generation stats — so a run stays inspectable without touching the
answer on stdout. ``retrieve_fn`` and ``generate_fn`` are injectable, so the whole flow is
testable with fakes (no database, model, or network).

Stage contracts: docs/stages/retrieve.md, docs/stages/assemble.md, docs/stages/generate.md
Theory: docs/theory/llm-generation.md
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

from rag.assemble import AssembleError, Prompt, assemble
from rag.embed import Embedder, SentenceTransformerEmbedder
from rag.generate import GenerateError, GenerateResult, GenerationStats, generate
from rag.retrieve import (
    TOP_K,
    RetrievedChunk,
    RetrieveError,
    check_connection_settings,
    retrieve,
)

# The corpus is English Wikipedia (CC BY-SA 4.0); displaying a retrieved excerpt is a
# reproduction, so every answer surfaces the article links and this licence notice — the
# attribution obligation satisfied at the point of display.
LICENCE_NOTICE = "Excerpts from English Wikipedia, licensed CC BY-SA 4.0."

# The two injectable seams: retrieve turns a question and k into ranked chunks; generate
# turns a prompt and a live delta callback into an answer with stats. Tests pass fakes.
RetrieveFn = Callable[[str, int], list[RetrievedChunk]]
GenerateFn = Callable[[Prompt, Callable[[str], None]], GenerateResult]


@dataclass(frozen=True)
class Answer:
    """The online path's structured result: the answer, the chunks it cited, and the run's stats.

    What :func:`answer_question` returns and both callers surface — the CLI prints it, the HTTP
    API serialises it.
    """

    answer: str
    hits: list[RetrievedChunk]
    stats: GenerationStats


def default_retrieve_fn(embedder: Embedder | None) -> RetrieveFn:
    """Wrap ``rag.retrieve.retrieve``, building the real embedder lazily when none was injected.

    The connection settings are checked before the model construction: missing settings
    should fail in milliseconds, not after seconds of loading the embedding model. Shared by
    the CLI (lazy construction per run) and the API (a warm embedder passed in once).
    """

    def retrieve_top_k(question: str, top_k: int) -> list[RetrievedChunk]:
        check_connection_settings()
        active = embedder if embedder is not None else SentenceTransformerEmbedder()
        return retrieve(question, embedder=active, top_k=top_k)

    return retrieve_top_k


def generate_via_ollama(prompt: Prompt, on_delta: Callable[[str], None]) -> GenerateResult:
    """Default ``generate_fn``: forward to ``rag.generate.generate`` with the live callback."""
    return generate(prompt, on_delta=on_delta)


def _discard_delta(_delta: str) -> None:
    """Default ``on_delta`` for a non-streaming caller (the API): drop each token."""


def answer_question(
    question: str,
    top_k: int,
    *,
    retrieve_fn: RetrieveFn,
    generate_fn: GenerateFn,
    on_delta: Callable[[str], None] = _discard_delta,
    on_retrieved: Callable[[list[RetrievedChunk]], None] | None = None,
    on_assembled: Callable[[Prompt], None] | None = None,
) -> Answer:
    """Run retrieve → assemble → generate once and return the structured :class:`Answer`.

    The single online-path wiring shared by the CLI (``ask.main``) and the HTTP API. Stage
    errors propagate unchanged — ``RetrieveError``, ``AssembleError``, ``GenerateError`` — for
    the caller to map to an exit code or an HTTP status. The optional hooks observe the
    intermediate steps without altering the flow: ``on_retrieved`` after retrieval,
    ``on_assembled`` after prompt assembly, and ``on_delta`` per streamed answer token. The
    CLI passes all three (its live streaming and per-step stderr log); the API passes none.
    """
    hits = retrieve_fn(question, top_k)
    if on_retrieved is not None:
        on_retrieved(hits)
    prompt = assemble(question, hits)
    if on_assembled is not None:
        on_assembled(prompt)
    result = generate_fn(prompt, on_delta)
    return Answer(answer=result.answer, hits=list(hits), stats=result.stats)


def _log_prompt(prompt: Prompt, *, verbose: bool) -> None:
    """Log the prompt size to stderr; with ``verbose`` also dump both messages in full."""
    total = prompt.char_count
    print(
        f"prompt: {total} chars (system {len(prompt.system)}, user {len(prompt.user)})",
        file=sys.stderr,
    )
    if verbose:
        print("--- system prompt ---", file=sys.stderr)
        print(prompt.system, file=sys.stderr)
        print("--- user message ---", file=sys.stderr)
        print(prompt.user, file=sys.stderr)


def _log_stats(stats: GenerationStats) -> None:
    """Log the final generation accounting (token counts and phase timings) to stderr."""
    print(
        f"generation: {stats.prompt_tokens} prompt + {stats.answer_tokens} answer tokens, "
        f"load {stats.load_seconds:.1f}s, prefill {stats.prompt_eval_seconds:.1f}s, "
        f"decode {stats.eval_seconds:.1f}s, total {stats.total_seconds:.1f}s, "
        f"done: {stats.done_reason}",
        file=sys.stderr,
    )


def main(
    argv: list[str] | None = None,
    *,
    embedder: Embedder | None = None,
    retrieve_fn: RetrieveFn | None = None,
    generate_fn: GenerateFn | None = None,
) -> int:
    """Answer one question end to end: retrieve, assemble, generate, and cite the sources.

    The answer streams to stdout token by token, followed by a numbered ``Sources:`` block
    and a CC BY-SA licence notice;
    every step logs one line to stderr. ``retrieve_fn`` and ``generate_fn`` are injectable
    for tests; their defaults wrap the real stages and construct the embedding model lazily,
    only when neither an embedder nor a ``retrieve_fn`` was injected. Returns 0 on success,
    or 1 when retrieve, assemble, or generate fails — the actionable hint goes to stderr.
    """
    parser = argparse.ArgumentParser(
        prog='python -m rag.ask "<question>"',
        description="Answer a question about a football club from the retrieved Wikipedia excerpts.",
    )
    parser.add_argument("question", help="the question to answer")
    parser.add_argument("--top-k", type=int, default=TOP_K, help="number of chunks to retrieve")
    parser.add_argument(
        "--verbose", action="store_true", help="also print the full prompt to stderr"
    )
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    if retrieve_fn is None:
        retrieve_fn = default_retrieve_fn(embedder)
    if generate_fn is None:
        generate_fn = generate_via_ollama

    print(f"question: {args.question}", file=sys.stderr)

    streamed: list[str] = []

    def on_delta(delta: str) -> None:
        print(delta, end="", flush=True)
        streamed.append(delta)

    def on_retrieved(hits: list[RetrievedChunk]) -> None:
        for rank, hit in enumerate(hits, start=1):
            print(f"hit {rank}: ({hit.distance:.4f}) {hit.citation}", file=sys.stderr)

    def on_assembled(prompt: Prompt) -> None:
        _log_prompt(prompt, verbose=args.verbose)

    try:
        answer = answer_question(
            args.question,
            args.top_k,
            retrieve_fn=retrieve_fn,
            generate_fn=generate_fn,
            on_delta=on_delta,
            on_retrieved=on_retrieved,
            on_assembled=on_assembled,
        )
    except (RetrieveError, AssembleError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except GenerateError as error:
        if streamed:
            print()  # close the partial answer line before the hint goes to stderr
        print(str(error), file=sys.stderr)
        return 1

    # The deltas were streamed with end="": close the answer line, then a blank line above
    # the sources block.
    print()
    print()
    print("Sources:")
    for number, hit in enumerate(answer.hits, start=1):
        print(f"[{number}] {hit.citation} — {hit.source_url}")
    print()
    print(LICENCE_NOTICE)

    _log_stats(answer.stats)
    return 0
