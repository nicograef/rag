"""Embed stage — turn chunk records into vectors with the pinned sentence-transformers model.

Reads each law's chunk records from ``data/chunks/<slug>.jsonl`` (produced by the chunk
stage) and writes one JSONL file per law to ``data/embeddings/<slug>.jsonl``: one embedding
record per chunk (``id``, ``model``, ``dim``, ``embedding``), input order preserved. Only the
chunk fields ``id`` and ``text`` are consumed. The model is hidden behind the minimal
``Embedder`` interface; the one real implementation wraps the pinned model with batch
encoding on CPU (sentence-transformers is imported lazily so the default test suite never
loads torch). A chunk longer than the model's token window fails the law instead of being
silently truncated — the no-silent-loss guarantee holds across the chunk→embed boundary.
Reproducible within tolerance — never bitwise (floating-point results vary across hardware
and library versions).

Stage contract: docs/stages/embed.md
Theory: docs/theory/embeddings.md
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Protocol

from rag import CHUNKS_DIR, EMBEDDINGS_DIR, run_per_source

# Pinned by the dated model decision in docs/roadmap.md ("Embedding model", 2026-07-17):
# model, dimension, normalization, and the pgvector distance operator are chosen together — the
# reasoning lives in the decision entry, the values live here. The model tag and batch size are
# env-overridable with the pinned value as the default (the "pinned choice, tunable knob" rule).
MODEL_ID = os.environ.get("EMBED_MODEL_ID", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = 384
NORMALIZE_EMBEDDINGS = True

# CPU batch size, kept modest to fit the 4-core/8 GB floor; override with EMBED_BATCH_SIZE.
# bge-small-en-v1.5 uses a symmetric path — no query/passage instruction prefix — so ingest
# and question embeddings share one interface.
BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "16"))


class EmbedError(Exception):
    """Raised when a law's chunk records cannot be embedded faithfully."""


class Embedder(Protocol):
    """The minimal embedding interface: texts in, vectors out, self-describing.

    ``model`` and ``dim`` describe every vector ``embed`` returns; the embed stage stamps
    them onto each artifact record so load can validate consistency before writing.
    ``max_tokens`` and ``token_count`` let the stage refuse a text the model would
    silently truncate instead of embedding it lossily.
    """

    model: str
    dim: int
    max_tokens: int

    def token_count(self, text: str) -> int:
        """The number of tokens ``embed`` would feed the model for ``text``."""
        ...

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
        # Declared optional upstream; without a window the truncation guard cannot exist.
        max_seq_length = self._model.max_seq_length
        if max_seq_length is None:
            raise EmbedError(f"{MODEL_ID} reports no max_seq_length — cannot guard truncation")
        self.max_tokens = max_seq_length

    def token_count(self, text: str) -> int:
        # The raw tokenizer, not `self._model.tokenize` — the latter already truncates to
        # `max_seq_length`, which would hide exactly the overflow this count exists to catch.
        return len(self._model.tokenizer(text, truncation=False)["input_ids"])

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
    in chunk-file order. A chunk over the model's token window fails the law before
    anything is embedded (``encode`` would silently truncate it); the output file is only
    written after the whole law embedded successfully.
    """
    pairs = read_chunk_texts(chunks_file)
    for chunk_id, text in pairs:
        if (tokens := embedder.token_count(text)) > embedder.max_tokens:
            raise EmbedError(
                f"chunk {chunk_id} is {tokens} tokens, over the model's {embedder.max_tokens}"
                " — refusing to silently truncate normative text"
            )
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

    jobs = [
        (
            chunks_file.stem,
            lambda chunks_file=chunks_file: (
                f"→ {embed_law(chunks_file, args.embeddings_dir, embedder)}"
            ),
        )
        for chunks_file in chunks_files
    ]
    return run_per_source("embed", jobs, (EmbedError, OSError))
