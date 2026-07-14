"""Pin the real-model reference vectors for the embed integration test.

Run once on a machine with the pinned model cached (any `make embed` run downloads it):

    uv run python tests/pin_reference_vectors.py

Writes `tests/fixtures/reference_vectors.json` — a few real chunk texts and their vectors
from the pinned model. The integration test in `test_embed.py` asserts cosine similarity
within tolerance against these (never bitwise, per the playbook's determinism promise).
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE_COUNT = 3


def main() -> None:
    from rag.embed import MODEL_ID, SentenceTransformerEmbedder

    chunk_lines = (FIXTURES / "chunks" / "kassensichv.jsonl").read_text(encoding="utf-8")
    texts = [json.loads(line)["text"] for line in chunk_lines.splitlines()[:REFERENCE_COUNT]]

    embedder = SentenceTransformerEmbedder()
    reference = {"model": MODEL_ID, "texts": texts, "embeddings": embedder.embed(texts)}

    output = FIXTURES / "reference_vectors.json"
    output.write_text(json.dumps(reference, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"✓ pinned {len(texts)} reference vectors for {MODEL_ID} → {output}")


if __name__ == "__main__":
    main()
