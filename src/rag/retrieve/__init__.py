"""Retrieve stage — embed a question and return the top-k nearest chunks from the store.

First stage of the online path. Embeds the question with the same pinned model the embed
stage used on the corpus — the deliberate query-document coupling, since a nearest-neighbour
search is only meaningful when query and documents live in one vector space (recorded in the
model decision). Runs one similarity query against the ``chunks`` table the load stage owns,
ordered by the pinned distance operator, and returns ``RetrievedChunk`` records — the
downstream contract the assemble and ask stages consume. The standalone CLI backs
``make query``, the quick retrieval spot-check.

Stage contract: docs/stages/retrieve.md
Theory: docs/theory/embeddings.md, docs/theory/vector-indexes.md
"""

import argparse
import sys
from dataclasses import dataclass

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from rag.embed import Embedder, SentenceTransformerEmbedder
from rag.load import DISTANCE_OPERATOR, LoadError, connection_conninfo

# Pinned by the dated LLM decision in docs/roadmap.md ("Generation model", 2026-07-17):
# k, the chunk sizes, and the generation model's context length form one context budget —
# the reasoning lives in the decision entry, the value lives here.
TOP_K = 5

# The standalone CLI flattens each hit's text to a one-line snippet of this width.
SNIPPET_CHARS = 200

# A missing vector type and a missing table both mean load never ran — one shared hint.
NO_CHUNKS_TABLE_HINT = "no chunks table — run `make load` first"


class RetrieveError(Exception):
    """Raised when the store cannot be searched; the message carries the actionable hint."""


@dataclass(frozen=True)
class RetrievedChunk:
    """One ranked hit — exactly the fields downstream stages consume."""

    id: str
    source_title: str
    citation: str
    source_url: str
    text: str
    distance: float


def check_connection_settings() -> str:
    """The store's conninfo from the environment; ``RetrieveError`` with the hint when unset.

    Split out so the CLIs can fail fast on missing settings before paying for the real
    embedding model's construction.
    """
    try:
        return connection_conninfo()
    except LoadError as error:
        raise RetrieveError(str(error)) from error


def retrieve(question: str, *, embedder: Embedder, top_k: int = TOP_K) -> list[RetrievedChunk]:
    """The ``top_k`` chunks nearest to ``question`` in embedding space, nearest first.

    The connection settings are checked first (cheap fail-fast), then the question is
    embedded with the injected model, then one similarity query runs against the store.
    Every failure raises ``RetrieveError`` carrying the actionable hint; the
    ascending-distance order is the query's ``ORDER BY``.
    """
    conninfo = check_connection_settings()

    embedding = embedder.embed([question])[0]

    try:
        with psycopg.connect(conninfo) as connection:
            try:
                register_vector(connection)
            except psycopg.ProgrammingError as error:
                # No vector type in the database means load never ran (it owns
                # CREATE EXTENSION) — same situation as a missing table, same hint.
                raise RetrieveError(NO_CHUNKS_TABLE_HINT) from error
            try:
                rows = connection.execute(
                    f"""
                    SELECT id, source_title, citation, source_url, text,
                           embedding {DISTANCE_OPERATOR} %(question)s AS distance
                    FROM chunks
                    ORDER BY distance
                    LIMIT %(top_k)s
                    """,
                    {"question": Vector(embedding), "top_k": top_k},
                ).fetchall()
            except psycopg.errors.UndefinedTable as error:
                raise RetrieveError(NO_CHUNKS_TABLE_HINT) from error
    except psycopg.OperationalError as error:
        raise RetrieveError(f"database connection failed: {error} — run `make db` first") from error

    if not rows:
        raise RetrieveError("the chunks table is empty — run `make load` first")
    return [
        RetrievedChunk(
            id=chunk_id,
            source_title=source_title,
            citation=citation,
            source_url=source_url,
            text=text,
            distance=distance,
        )
        for chunk_id, source_title, citation, source_url, text, distance in rows
    ]


def format_hit(rank: int, distance: float, citation: str, text: str) -> str:
    """One printed hit: rank and distance, the citation, and a flattened text snippet."""
    snippet = " ".join(text.split())
    if len(snippet) > SNIPPET_CHARS:
        snippet = snippet[: SNIPPET_CHARS - 1] + "…"
    return f"{rank}. ({distance:.4f}) {citation}\n   {snippet}"


def main(argv: list[str] | None = None, embedder: Embedder | None = None) -> int:
    """Print the top-k chunks for one question; non-zero exit when nothing can be searched.

    ``embedder`` is injectable for tests; by default the real model is constructed lazily,
    so callers that inject a fake never load torch.
    """
    parser = argparse.ArgumentParser(
        prog='python -m rag.retrieve "<question>"',
        description="Embed a question and print the nearest chunks from the vector store.",
    )
    parser.add_argument("question", help="the question to search for")
    parser.add_argument("--top-k", type=int, default=TOP_K, help="number of hits to print")
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    try:
        # Before the model construction below: missing settings should fail in milliseconds,
        # not after seconds of loading (or a first-run download of) the embedding model.
        check_connection_settings()
        if embedder is None:
            embedder = SentenceTransformerEmbedder()
        hits = retrieve(args.question, embedder=embedder, top_k=args.top_k)
    except RetrieveError as error:
        print(str(error), file=sys.stderr)
        return 1

    for rank, hit in enumerate(hits, start=1):
        print(format_hit(rank, hit.distance, hit.citation, hit.text))
    return 0
