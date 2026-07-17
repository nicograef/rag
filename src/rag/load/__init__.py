"""Load stage — fill Postgres/pgvector with chunk records and their embeddings, idempotently.

Reads each law's chunk records from ``data/chunks/<slug>.jsonl`` and its embedding records
from ``data/embeddings/<slug>.jsonl`` (produced by the chunk and embed stages), joins them
by chunk ``id``, and writes the ``chunks`` table this stage owns: text, metadata, and a
fixed-dimension vector column with one HNSW index. Every run recreates the schema
idempotently and applies per-law replace semantics — upsert every row by ``id``, then
delete the law's rows whose ids are gone — so the store always mirrors the current
artifacts. A chunk without a vector, a vector without a chunk, or model/dimension
disagreement across records is a per-law error; nothing partial is written for that law.

Stage contract: docs/stages/load.md
Theory: docs/theory/vector-indexes.md
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
import psycopg.conninfo
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from rag import CHUNKS_DIR, EMBEDDINGS_DIR
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
    law text,
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
    law: str
    unit: str
    section_path: list[str]
    citation: str
    source_url: str
    fetched_at: str
    part: dict[str, int] | None
    text: str
    embedding: list[float]


def read_records(jsonl_file: Path) -> list[dict]:
    """The JSON records of one artifact file, in file order."""
    records = []
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


def join_law(chunk_records: list[dict], embedding_records: list[dict], dim: int) -> list[Row]:
    """Join one law's chunk and embedding records by ``id`` into table rows, validating both.

    Raises ``LoadError`` — before anything could be written — when a chunk has no vector, a
    vector has no chunk, the embedding records disagree on one model and dimension, or that
    dimension does not match the ``dim`` the schema's vector column is fixed to.
    """
    try:
        models = {record["model"] for record in embedding_records}
        dims = {record["dim"] for record in embedding_records}
        vectors = {record["id"]: record["embedding"] for record in embedding_records}
        chunk_ids = [record["id"] for record in chunk_records]
    except KeyError as error:
        raise LoadError(f"record missing field {error}") from error

    if len(models) > 1 or len(dims) > 1:
        raise LoadError(f"embedding records disagree on model/dim: {models}/{dims}")
    if dims and dims != {dim}:
        raise LoadError(f"embedding dim {dims.pop()} does not match the schema's vector({dim})")
    if missing := [chunk_id for chunk_id in chunk_ids if chunk_id not in vectors]:
        raise LoadError(f"chunk(s) without an embedding: {', '.join(missing)}")
    if orphans := sorted(set(vectors) - set(chunk_ids)):
        raise LoadError(f"embedding(s) without a chunk: {', '.join(orphans)}")

    try:
        return [
            Row(
                id=record["id"],
                slug=record["slug"],
                law=record["law"],
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
    current artifacts — the store mirrors the pipeline output and never accumulates stale
    rows. Runs in one transaction: a mid-law failure leaves the law's previous state intact.
    """
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO chunks (id, slug, law, unit, section_path, citation,
                                source_url, fetched_at, part, text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                slug = EXCLUDED.slug, law = EXCLUDED.law, unit = EXCLUDED.unit,
                section_path = EXCLUDED.section_path, citation = EXCLUDED.citation,
                source_url = EXCLUDED.source_url, fetched_at = EXCLUDED.fetched_at,
                part = EXCLUDED.part, text = EXCLUDED.text, embedding = EXCLUDED.embedding
            """,
            [
                (
                    row.id,
                    row.slug,
                    row.law,
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

    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(SCHEMA_SQL)
        register_vector(connection)

        failed: list[str] = []
        slugs = sorted({f.stem for f in chunks_files} | {f.stem for f in embeddings_files})
        for slug in slugs:
            try:
                chunks_file = args.chunks_dir / f"{slug}.jsonl"
                embeddings_file = args.embeddings_dir / f"{slug}.jsonl"
                if not chunks_file.is_file():
                    raise LoadError("embeddings without chunk records — run `make chunk`")
                if not embeddings_file.is_file():
                    raise LoadError("chunk records without embeddings — run `make embed`")
                rows = join_law(
                    read_records(chunks_file), read_records(embeddings_file), EMBEDDING_DIM
                )
                load_law(connection, slug, rows)
            except (LoadError, OSError) as error:
                print(f"✗ {slug}: {error}", file=sys.stderr)
                failed.append(slug)
            else:
                print(f"✓ {slug} → chunks table ({len(rows)} rows)")
    if failed:
        print(f"load failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
