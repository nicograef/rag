"""RAG over German law texts — pipeline package.

Offline ingestion stages land as subpackages one roadmap phase at a time
(fetch → convert → chunk → embed → load); the online path (retrieve →
assemble → generate) follows, with evaluation as a cross-cutting harness.
See docs/roadmap.md.
"""

import sys
from collections.abc import Callable, Iterable
from pathlib import Path

__version__ = "0.1.0"

# Stage-handoff directories — each producer's output directory is the next stage's
# input; the stage CLIs use these as their --*-dir defaults, and the Makefile passes
# no overrides, so a handoff moves with one edit here.
RAW_DIR = Path("data/raw")
CORPUS_DIR = Path("data/corpus")
CHUNKS_DIR = Path("data/chunks")
EMBEDDINGS_DIR = Path("data/embeddings")

# The corpus-Markdown separator between a heading's designation and its optional title
# ("§ 1 — Zweck"). Convert writes it and chunk splits on it — defined once here so the
# writer and the reader cannot drift apart.
HEADING_SEPARATOR = " — "


def run_per_source(
    stage: str,
    jobs: Iterable[tuple[str, Callable[[], str]]],
    errors: tuple[type[Exception], ...],
) -> int:
    """Run one stage's per-source jobs under the shared isolation contract, defined once here.

    Each job is ``(name, work)``; ``work()`` does one source's work and returns the detail
    printed after ``✓ <name>``. A job that raises one of ``errors`` is reported on stderr
    (``✗ <name>: <error>``) and recorded — the remaining sources still run — and the stage
    exits non-zero if any source failed. Callers building jobs in a comprehension must bind
    the loop variable as a lambda default (``lambda item=item: ...``): a lambda looks names
    up late, so without it every job would work on the last item.
    """
    failed: list[str] = []
    for name, work in jobs:
        try:
            detail = work()
        except errors as error:
            print(f"✗ {name}: {error}", file=sys.stderr)
            failed.append(name)
        else:
            print(f"✓ {name} {detail}")
    if failed:
        print(f"{stage} failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
