"""Load stage — fill Postgres/pgvector with chunk records and their embeddings, idempotently.

Reads each law's chunk records from ``data/chunks/<slug>.jsonl`` and its embedding records
from ``data/embeddings/<slug>.jsonl`` (produced by the chunk and embed stages), joins them
by chunk ``id``, and writes the ``chunks`` table this stage owns: text, metadata, and a
fixed-dimension vector column with one HNSW index. Every run ensures the schema exists
(``CREATE ... IF NOT EXISTS``) and applies per-law replace semantics — upsert every row by
``id``, then delete the law's rows whose ids are gone — so each law present in the input
mirrors its current artifacts. The mirror is per-law only: a law whose artifact files are
removed entirely is never visited, so its rows stay until the table is rebuilt. A chunk
without a vector, a vector without a chunk, a record ``slug`` that contradicts the file
name, or model/dimension disagreement across records is a per-law error; nothing partial
is written for that law.

Stage contract: docs/stages/load.md
Theory: docs/theory/vector-indexes.md
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import psycopg.conninfo
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from rag import CHUNKS_DIR, EMBEDDINGS_DIR, run_per_source
from rag.embed import EMBEDDING_DIM

# Pinned by the dated model decision in docs/roadmap.md ("Embedding model", 2026-07-14):
# cosine distance, matching the model card's similarity function. The HNSW index uses
# pgvector's default build parameters (m=16, ef_construction=64) — the MVP corpus is far
# too small to need tuning.
DISTANCE_OPERATOR = "<=>"
HNSW_OPERATOR_CLASS = "vector_cosine_ops"

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id text PRIMARY KEY,
    slug text,
    source_title text,
    unit text,
    section_path text[],
    citation text,
    source_url text,
    fetched_at text,
    part jsonb NULL,
    text text,
    embedding vector({EMBEDDING_DIM}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding {HNSW_OPERATOR_CLASS});
"""


class LoadError(Exception):
    """Raised when a law's artifacts cannot be joined or written faithfully."""


@dataclass(frozen=True)
class Row:
    """One ``chunks``-table row: a chunk record joined with its embedding by ``id``."""

    id: str
    slug: str
    source_title: str
    unit: str
    section_path: list[str]
    citation: str
    source_url: str
    fetched_at: str
    part: dict[str, int] | None
    text: str
    embedding: list[float]


def read_records(jsonl_file: Path) -> list[dict[str, Any]]:
    """The JSON records of one artifact file, in file order."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(jsonl_file.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise LoadError(
                f"{jsonl_file.name}: invalid record on line {number}: {error}"
            ) from error
        if not isinstance(record, dict):
            raise LoadError(f"{jsonl_file.name}: invalid record on line {number}: not an object")
        records.append(record)
    return records


def join_law(
    slug: str,
    chunk_records: list[dict[str, Any]],
    embedding_records: list[dict[str, Any]],
    dim: int,
) -> list[Row]:
    """Join one law's chunk and embedding records by ``id`` into table rows, validating both.

    Raises ``LoadError`` — before anything could be written — when a chunk has no vector, a
    vector has no chunk, a chunk record's ``slug`` contradicts the artifact file's ``slug``
    (the prune keys on it), the embedding records disagree on one model and dimension, or
    that dimension does not match the ``dim`` the schema's vector column is fixed to.
    """
    try:
        slugs = {record["slug"] for record in chunk_records}
        models = {record["model"] for record in embedding_records}
        dims = {record["dim"] for record in embedding_records}
        vectors = {record["id"]: record["embedding"] for record in embedding_records}
        chunk_ids = [record["id"] for record in chunk_records]
    except KeyError as error:
        raise LoadError(f"record missing field {error}") from error

    if wrong := sorted(slugs - {slug}):
        raise LoadError(f"chunk record slug(s) {', '.join(wrong)} do not match the file {slug!r}")
    if len(models) > 1 or len(dims) > 1:
        raise LoadError(f"embedding records disagree on model/dim: {models}/{dims}")
    if dims and dims != {dim}:
        raise LoadError(
            f"embedding dim {next(iter(dims))} does not match the schema's vector({dim})"
        )
    if missing := [chunk_id for chunk_id in chunk_ids if chunk_id not in vectors]:
        raise LoadError(f"chunk(s) without an embedding: {', '.join(missing)}")
    if orphans := sorted(set(vectors) - set(chunk_ids)):
        raise LoadError(f"embedding(s) without a chunk: {', '.join(orphans)}")

    try:
        return [
            Row(
                id=record["id"],
                slug=record["slug"],
                source_title=record["source_title"],
                unit=record["unit"],
                section_path=record["section_path"],
                citation=record["citation"],
                source_url=record["source_url"],
                fetched_at=record["fetched_at"],
                part=record["part"],
                text=record["text"],
                embedding=vectors[record["id"]],
            )
            for record in chunk_records
        ]
    except KeyError as error:
        raise LoadError(f"chunk record missing field {error}") from error


def connection_conninfo() -> str:
    """The psycopg connection string from the environment (the same variables Compose uses).

    ``POSTGRES_HOST`` defaults to ``localhost`` and ``POSTGRES_PORT`` to ``5432``; user,
    password, and dbname must be set (they come from ``.env`` via the Makefile).
    """
    missing = [
        name
        for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        if not os.environ.get(name)
    ]
    if missing:
        raise LoadError(f"missing environment variable(s): {', '.join(missing)} (see .env.example)")
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def load_law(connection: psycopg.Connection, slug: str, rows: list[Row]) -> None:
    """Write one law's rows with replace semantics, atomically.

    Upserts every row by ``id``, then deletes the law's rows whose ids are absent from the
    current artifacts — the law's rows mirror the pipeline output and never go stale. Runs
    in one transaction: a mid-law failure leaves the law's previous state intact.
    """
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO chunks (id, slug, source_title, unit, section_path, citation,
                                source_url, fetched_at, part, text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                slug = EXCLUDED.slug, source_title = EXCLUDED.source_title, unit = EXCLUDED.unit,
                section_path = EXCLUDED.section_path, citation = EXCLUDED.citation,
                source_url = EXCLUDED.source_url, fetched_at = EXCLUDED.fetched_at,
                part = EXCLUDED.part, text = EXCLUDED.text, embedding = EXCLUDED.embedding
            """,
            [
                (
                    row.id,
                    row.slug,
                    row.source_title,
                    row.unit,
                    row.section_path,
                    row.citation,
                    row.source_url,
                    row.fetched_at,
                    Jsonb(row.part) if row.part is not None else None,
                    row.text,
                    Vector(row.embedding),
                )
                for row in rows
            ],
        )
        cursor.execute(
            "DELETE FROM chunks WHERE slug = %s AND NOT (id = ANY(%s))",
            (slug, [row.id for row in rows]),
        )


def _load_law_files(
    connection: psycopg.Connection, chunks_dir: Path, embeddings_dir: Path, slug: str
) -> str:
    """One law's job: require both artifacts, join them, write; returns the ``✓`` detail."""
    chunks_file = chunks_dir / f"{slug}.jsonl"
    embeddings_file = embeddings_dir / f"{slug}.jsonl"
    if not chunks_file.is_file():
        raise LoadError("embeddings without chunk records — run `make chunk`")
    if not embeddings_file.is_file():
        raise LoadError("chunk records without embeddings — run `make embed`")
    rows = join_law(slug, read_records(chunks_file), read_records(embeddings_file), EMBEDDING_DIM)
    load_law(connection, slug, rows)
    return f"→ chunks table ({len(rows)} rows)"


def main(argv: list[str] | None = None) -> int:
    """Load every law's artifacts into Postgres; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.load",
        description="Load chunk records and embeddings into the Postgres/pgvector store.",
    )
    parser.add_argument(
        "--chunks-dir", type=Path, default=CHUNKS_DIR, help="chunk records directory"
    )
    parser.add_argument(
        "--embeddings-dir", type=Path, default=EMBEDDINGS_DIR, help="embeddings directory"
    )
    args = parser.parse_args(argv)

    chunks_files = sorted(args.chunks_dir.glob("*.jsonl")) if args.chunks_dir.is_dir() else []
    if not chunks_files:
        print(f"no chunks in {args.chunks_dir} — run `make chunk` first", file=sys.stderr)
        return 1
    embeddings_files = (
        sorted(args.embeddings_dir.glob("*.jsonl")) if args.embeddings_dir.is_dir() else []
    )
    if not embeddings_files:
        print(f"no embeddings in {args.embeddings_dir} — run `make embed` first", file=sys.stderr)
        return 1

    try:
        conninfo = connection_conninfo()
    except LoadError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(SCHEMA_SQL)
            register_vector(connection)

            slugs = sorted({f.stem for f in chunks_files} | {f.stem for f in embeddings_files})
            jobs = [
                (
                    slug,
                    lambda slug=slug: _load_law_files(
                        connection, args.chunks_dir, args.embeddings_dir, slug
                    ),
                )
                for slug in slugs
            ]
            return run_per_source("load", jobs, (LoadError, OSError))
    except psycopg.OperationalError as error:
        print(f"database connection failed: {error} — run `make db` first", file=sys.stderr)
        return 1
