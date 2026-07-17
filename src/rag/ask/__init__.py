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


def _default_retrieve_fn(embedder: Embedder | None) -> RetrieveFn:
    """Wrap ``rag.retrieve.retrieve``, building the real embedder lazily when none was injected.

    The connection settings are checked before the model construction: missing settings
    should fail in milliseconds, not after seconds of loading the embedding model.
    """

    def retrieve_top_k(question: str, top_k: int) -> list[RetrievedChunk]:
        check_connection_settings()
        active = embedder if embedder is not None else SentenceTransformerEmbedder()
        return retrieve(question, embedder=active, top_k=top_k)

    return retrieve_top_k


def _generate_via_ollama(prompt: Prompt, on_delta: Callable[[str], None]) -> GenerateResult:
    """Default ``generate_fn``: forward to ``rag.generate.generate`` with the live callback."""
    return generate(prompt, on_delta=on_delta)


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
        retrieve_fn = _default_retrieve_fn(embedder)
    if generate_fn is None:
        generate_fn = _generate_via_ollama

    print(f"question: {args.question}", file=sys.stderr)

    try:
        hits = retrieve_fn(args.question, args.top_k)
    except RetrieveError as error:
        print(str(error), file=sys.stderr)
        return 1
    for rank, hit in enumerate(hits, start=1):
        print(f"hit {rank}: ({hit.distance:.4f}) {hit.citation}", file=sys.stderr)

    try:
        prompt = assemble(args.question, hits)
    except AssembleError as error:
        print(str(error), file=sys.stderr)
        return 1
    _log_prompt(prompt, verbose=args.verbose)

    streamed: list[str] = []

    def on_delta(delta: str) -> None:
        print(delta, end="", flush=True)
        streamed.append(delta)

    try:
        result = generate_fn(prompt, on_delta)
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
    for number, hit in enumerate(hits, start=1):
        print(f"[{number}] {hit.citation} — {hit.source_url}")
    print()
    print(LICENCE_NOTICE)

    _log_stats(result.stats)
    return 0
