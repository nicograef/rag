"""Contract tests for the embed stage — golden-file driven with a fake embedder.

The default tests run without torch, a model download, or network. Real-model behavior
lives in the opt-in `integration` test at the bottom, which skips cleanly when the model
cache is absent (pin reference vectors with `uv run python tests/pin_reference_vectors.py`).
"""

import json
from pathlib import Path

import pytest
from conftest import FakeEmbedder

from rag.embed import EmbedError, embed_law, main, read_chunk_texts

FIXTURES = Path(__file__).parent / "fixtures"

# The chunk fixtures pinned as embed golden files (one flat law, one with merged units).
GOLDEN_SLUGS = ("kassensichv", "artg")


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_embed_law_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = embed_law(FIXTURES / "chunks" / f"{slug}.jsonl", tmp_path, FakeEmbedder())

    assert output == tmp_path / f"{slug}.jsonl"
    assert output.read_bytes() == (FIXTURES / "embeddings" / f"{slug}.jsonl").read_bytes()


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_embed_is_deterministic(slug: str, tmp_path: Path) -> None:
    chunks_file = FIXTURES / "chunks" / f"{slug}.jsonl"
    first = embed_law(chunks_file, tmp_path / "one", FakeEmbedder()).read_bytes()
    second = embed_law(chunks_file, tmp_path / "two", FakeEmbedder()).read_bytes()

    assert first == second


def test_records_are_self_describing_and_order_preserving(tmp_path: Path) -> None:
    chunks_file = FIXTURES / "chunks" / "kassensichv.jsonl"
    output = embed_law(chunks_file, tmp_path, FakeEmbedder())

    chunk_ids = [chunk_id for chunk_id, _ in read_chunk_texts(chunks_file)]
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert [record["id"] for record in records] == chunk_ids  # input order preserved
    for record in records:
        assert record["model"] == "fake-embedder"
        assert record["dim"] == 8
        assert len(record["embedding"]) == 8


def test_invalid_chunk_record_raises(tmp_path: Path) -> None:
    chunks_file = tmp_path / "broken.jsonl"
    chunks_file.write_text('{"id": "x#§ 1"}\n', encoding="utf-8")  # no `text`

    with pytest.raises(EmbedError, match="line 1"):
        embed_law(chunks_file, tmp_path / "embeddings", FakeEmbedder())


def test_a_chunk_over_the_token_window_fails_before_writing(tmp_path: Path) -> None:
    # FakeEmbedder counts whitespace words as tokens; five words is over the max of three.
    chunks_file = tmp_path / "over.jsonl"
    chunks_file.write_text(
        json.dumps({"id": "x#§ 1", "text": "ein zwei drei vier fünf"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    embeddings_dir = tmp_path / "embeddings"

    with pytest.raises(EmbedError, match="refusing to silently truncate") as excinfo:
        embed_law(chunks_file, embeddings_dir, FakeEmbedder(max_tokens=3))

    assert "x#§ 1" in str(excinfo.value)
    assert not (embeddings_dir / "over.jsonl").exists()  # nothing written on failure


def test_main_isolates_a_failing_law_from_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    good = (FIXTURES / "chunks" / "kassensichv.jsonl").read_bytes()
    (chunks_dir / "good.jsonl").write_bytes(good)
    (chunks_dir / "broken.jsonl").write_text("not json\n", encoding="utf-8")
    embeddings_dir = tmp_path / "embeddings"

    exit_code = main(
        ["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(embeddings_dir)],
        embedder=FakeEmbedder(),
    )

    assert exit_code == 1
    assert (embeddings_dir / "good.jsonl").exists()  # the healthy law still embedded
    assert not (embeddings_dir / "broken.jsonl").exists()  # the failing law wrote nothing
    assert "broken" in capsys.readouterr().err


def test_main_without_chunks_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--chunks-dir", str(tmp_path / "missing"), "--embeddings-dir", str(tmp_path)],
        embedder=FakeEmbedder(),
    )

    assert exit_code == 1
    assert "make chunk" in capsys.readouterr().err


# ── Opt-in integration: the real pinned model ──

REFERENCE_VECTORS = FIXTURES / "reference_vectors.json"


def _model_cache_dir() -> Path:
    from rag.embed import MODEL_ID

    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{MODEL_ID.replace('/', '--')}"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b)


@pytest.mark.integration
def test_real_model_reproduces_reference_vectors_within_tolerance() -> None:
    if not _model_cache_dir().is_dir():
        pytest.skip("model cache absent — run `make embed` once to download the pinned model")
    if not REFERENCE_VECTORS.is_file():
        pytest.skip(
            "reference vectors not pinned — run `uv run python tests/pin_reference_vectors.py`"
        )

    from rag.embed import EMBEDDING_DIM, MODEL_ID, SentenceTransformerEmbedder

    reference = json.loads(REFERENCE_VECTORS.read_text(encoding="utf-8"))
    assert reference["model"] == MODEL_ID, "reference vectors were pinned for another model"

    embedder = SentenceTransformerEmbedder()
    vectors = embedder.embed(reference["texts"])

    for vector, pinned in zip(vectors, reference["embeddings"], strict=True):
        assert len(vector) == EMBEDDING_DIM
        assert abs(sum(x * x for x in vector) ** 0.5 - 1.0) < 1e-3  # normalized, per decision
        # Within tolerance, never bitwise: floating-point results vary across hardware.
        assert _cosine(vector, pinned) > 0.999
