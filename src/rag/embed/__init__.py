"""Embed stage — turn chunk records into vectors with the pinned sentence-transformers model.

Reads each law's chunk records from ``data/chunks/<slug>.jsonl`` (produced by the chunk
stage) and writes one JSONL file per law to ``data/embeddings/<slug>.jsonl``: one embedding
record per chunk (``id``, ``model``, ``dim``, ``embedding``), input order preserved. Only the
chunk fields ``id`` and ``text`` are consumed. The model is hidden behind the minimal
``Embedder`` interface; the one real implementation wraps the pinned model with batch
encoding on CPU (sentence-transformers is imported lazily so the default test suite never
loads torch). Reproducible within tolerance — never bitwise (floating-point results vary
across hardware and library versions).

Stage contract: docs/stages/embed.md
Theory: docs/theory/embeddings.md
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Protocol

from rag import CHUNKS_DIR, EMBEDDINGS_DIR

# Pinned by the dated model decision in docs/roadmap.md ("Embedding model", 2026-07-14):
# model, normalization, and pgvector distance operator are chosen together — the reasoning
# lives in the decision entry, the values live here.
MODEL_ID = "BAAI/bge-m3"
EMBEDDING_DIM = 1024
NORMALIZE_EMBEDDINGS = True

# CPU batch size: large enough to amortize per-batch overhead, small enough to keep memory flat.
BATCH_SIZE = 32


class EmbedError(Exception):
    """Raised when a law's chunk records cannot be embedded faithfully."""


class Embedder(Protocol):
    """The minimal embedding interface: texts in, vectors out, self-describing.

    ``model`` and ``dim`` describe every vector ``embed`` returns; the embed stage stamps
    them onto each artifact record so load can validate consistency before writing.
    """

    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in input order."""
        ...


class SentenceTransformerEmbedder:
    """The one real ``Embedder``: the pinned model via sentence-transformers, batched on CPU."""

    def __init__(self) -> None:
        # Lazy import: keeps torch out of every code path that injects a fake instead.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(MODEL_ID, device="cpu")
        self.model = MODEL_ID
        self.dim = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, batch_size=BATCH_SIZE, normalize_embeddings=NORMALIZE_EMBEDDINGS
        )
        return [vector.tolist() for vector in vectors]


def read_chunk_texts(chunks_file: Path) -> list[tuple[str, str]]:
    """The ``(id, text)`` pairs of one chunk JSONL file, in file order."""
    pairs: list[tuple[str, str]] = []
    for number, line in enumerate(chunks_file.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
            pairs.append((record["id"], record["text"]))
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise EmbedError(f"invalid chunk record on line {number}: {error}") from error
    if not pairs:
        raise EmbedError("chunk file contains no records")
    return pairs


def embed_law(chunks_file: Path, embeddings_dir: Path, embedder: Embedder) -> Path:
    """Embed one ``data/chunks/<slug>.jsonl`` into ``embeddings_dir/<slug>.jsonl``.

    One self-describing record per chunk — ``id``, ``model``, ``dim``, ``embedding`` —
    in chunk-file order. The output file is only written after the whole law embedded
    successfully.
    """
    pairs = read_chunk_texts(chunks_file)
    vectors = embedder.embed([text for _, text in pairs])
    if len(vectors) != len(pairs):
        raise EmbedError(f"embedder returned {len(vectors)} vectors for {len(pairs)} chunks")

    records = [
        {"id": chunk_id, "model": embedder.model, "dim": embedder.dim, "embedding": vector}
        for (chunk_id, _), vector in zip(pairs, vectors, strict=True)
    ]
    embeddings_dir.mkdir(parents=True, exist_ok=True)
    output = embeddings_dir / chunks_file.name
    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    output.write_text(body, encoding="utf-8")
    return output


def main(argv: list[str] | None = None, embedder: Embedder | None = None) -> int:
    """Embed every chunked law; returns a non-zero exit code if any failed.

    ``embedder`` is injectable for tests; by default the real model is constructed lazily —
    after the input directory check, so a missing input fails fast without loading torch.
    """
    parser = argparse.ArgumentParser(
        prog="python -m rag.embed",
        description="Embed chunk records from data/chunks/ into JSONL under data/embeddings/.",
    )
    parser.add_argument("--chunks-dir", type=Path, default=CHUNKS_DIR, help="input directory")
    parser.add_argument(
        "--embeddings-dir", type=Path, default=EMBEDDINGS_DIR, help="output directory"
    )
    args = parser.parse_args(argv)

    chunks_files = sorted(args.chunks_dir.glob("*.jsonl")) if args.chunks_dir.is_dir() else []
    if not chunks_files:
        print(f"no chunks in {args.chunks_dir} — run `make chunk` first", file=sys.stderr)
        return 1

    if embedder is None:
        embedder = SentenceTransformerEmbedder()

    failed: list[str] = []
    for chunks_file in chunks_files:
        try:
            output = embed_law(chunks_file, args.embeddings_dir, embedder)
        except (EmbedError, OSError) as error:
            print(f"✗ {chunks_file.stem}: {error}", file=sys.stderr)
            failed.append(chunks_file.stem)
        else:
            print(f"✓ {chunks_file.stem} → {output}")
    if failed:
        print(f"embed failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
