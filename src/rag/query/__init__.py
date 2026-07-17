"""Dev query command — embed one question, print the nearest chunks from the store.

A thin verification tool composing the embed stage's pinned model and one similarity query
against the ``chunks`` table the load stage owns: the question is embedded with the same
model that embedded the corpus, and the top-k rows ordered by the pinned distance operator
are printed with rank, distance, citation, and a text snippet. Explicitly **not** the
Phase 4 retrieve stage — it exists so the phase's success criterion ("plausible §§ for
hand-written test queries") is reproducible, and it is expected to be superseded there.

Stage contract: documented in docs/stages/load.md (verification section)
"""

import argparse
import sys

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from rag.embed import Embedder, SentenceTransformerEmbedder
from rag.load import DISTANCE_OPERATOR, LoadError, connection_conninfo

DEFAULT_TOP_K = 5
SNIPPET_CHARS = 200


def format_hit(rank: int, distance: float, citation: str, text: str) -> str:
    """One printed hit: rank and distance, the citation, and a flattened text snippet."""
    snippet = " ".join(text.split())
    if len(snippet) > SNIPPET_CHARS:
        snippet = snippet[: SNIPPET_CHARS - 1] + "…"
    return f"{rank}. ({distance:.4f}) {citation}\n   {snippet}"


def search(
    connection: psycopg.Connection, embedding: list[float], top_k: int
) -> list[tuple[float, str, str]]:
    """The ``top_k`` nearest chunks as ``(distance, citation, text)``, nearest first."""
    return connection.execute(
        f"""
        SELECT embedding {DISTANCE_OPERATOR} %(query)s AS distance, citation, text
        FROM chunks
        ORDER BY distance
        LIMIT %(top_k)s
        """,
        {"query": Vector(embedding), "top_k": top_k},
    ).fetchall()


def main(argv: list[str] | None = None, embedder: Embedder | None = None) -> int:
    """Print the top-k chunks for one question; non-zero exit when nothing can be searched."""
    parser = argparse.ArgumentParser(
        prog='python -m rag.query "<question>"',
        description="Embed a question and print the nearest chunks from the vector store.",
    )
    parser.add_argument("question", help="the question to search for")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="number of hits to print")
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    try:
        conninfo = connection_conninfo()
    except LoadError as error:
        print(str(error), file=sys.stderr)
        return 1

    if embedder is None:
        embedder = SentenceTransformerEmbedder()
    embedding = embedder.embed([args.question])[0]

    with psycopg.connect(conninfo) as connection:
        register_vector(connection)
        try:
            hits = search(connection, embedding, args.top_k)
        except psycopg.errors.UndefinedTable:
            print("no chunks table — run `make load` first", file=sys.stderr)
            return 1

    if not hits:
        print("the chunks table is empty — run `make load` first", file=sys.stderr)
        return 1
    for rank, (distance, citation, text) in enumerate(hits, start=1):
        print(format_hit(rank, distance, citation, text))
    return 0
