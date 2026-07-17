"""Contract tests for the load stage.

The default tests cover the pure logic — chunk/embedding joining and its validation
failures — with no database. The opt-in `integration` tests at the bottom run the stage
against the Compose Postgres (`make db`) in a dedicated throwaway database, and skip with a
reason when it is unreachable.
"""

import json
from pathlib import Path

import psycopg
import pytest
from conftest import FakeEmbedder

from rag.embed import EMBEDDING_DIM, embed_law
from rag.load import LoadError, Row, join_law, main, read_records

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_records(slug: str = "kassensichv") -> tuple[list[dict], list[dict]]:
    """One law's chunk records plus matching fake embedding records (dim 8)."""
    chunks = read_records(FIXTURES / "chunks" / f"{slug}.jsonl")
    embeddings = read_records(FIXTURES / "embeddings" / f"{slug}.jsonl")
    return chunks, embeddings


def test_join_law_pairs_every_chunk_with_its_vector() -> None:
    chunks, embeddings = _fixture_records()

    rows = join_law(chunks, embeddings, dim=8)

    assert [row.id for row in rows] == [chunk["id"] for chunk in chunks]
    for row, chunk in zip(rows, chunks, strict=True):
        assert isinstance(row, Row)
        assert row.text == chunk["text"]
        assert row.citation == chunk["citation"]
        assert len(row.embedding) == 8


def test_a_chunk_without_an_embedding_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=f"without an embedding: {chunks[-1]['id']}"):
        join_law(chunks, embeddings[:-1], dim=8)


def test_an_embedding_without_a_chunk_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=f"without a chunk: {chunks[-1]['id']}"):
        join_law(chunks[:-1], embeddings, dim=8)


def test_model_disagreement_across_records_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    embeddings[0]["model"] = "another-model"

    with pytest.raises(LoadError, match="disagree on model/dim"):
        join_law(chunks, embeddings, dim=8)


def test_a_dim_not_matching_the_schema_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=r"does not match the schema's vector\(16\)"):
        join_law(chunks, embeddings, dim=16)


def test_a_chunk_record_missing_a_field_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    del chunks[0]["citation"]

    with pytest.raises(LoadError, match="missing field 'citation'"):
        join_law(chunks, embeddings, dim=8)


def test_an_embedding_record_missing_a_field_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    del embeddings[0]["model"]

    with pytest.raises(LoadError, match="missing field 'model'"):
        join_law(chunks, embeddings, dim=8)


def test_read_records_reports_the_broken_line(tmp_path: Path) -> None:
    jsonl = tmp_path / "broken.jsonl"
    jsonl.write_text('{"id": "ok"}\nnot json\n', encoding="utf-8")

    with pytest.raises(LoadError, match="line 2"):
        read_records(jsonl)


def test_read_records_rejects_a_non_object_line(tmp_path: Path) -> None:
    jsonl = tmp_path / "broken.jsonl"
    jsonl.write_text('{"id": "ok"}\n[1, 2]\n', encoding="utf-8")

    with pytest.raises(LoadError, match="line 2: not an object"):
        read_records(jsonl)


def test_main_without_chunks_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--chunks-dir", str(tmp_path / "no"), "--embeddings-dir", str(tmp_path)])

    assert exit_code == 1
    assert "make chunk" in capsys.readouterr().err


def test_main_without_embeddings_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "law.jsonl").write_bytes((FIXTURES / "chunks" / "kassensichv.jsonl").read_bytes())

    exit_code = main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(tmp_path / "no")])

    assert exit_code == 1
    assert "make embed" in capsys.readouterr().err


# ── Opt-in integration: a real Postgres (the `test_db` fixture lives in conftest.py) ──


def _write_artifacts(tmp_path: Path, slug: str = "kassensichv") -> tuple[Path, Path, int]:
    """Fixture chunks plus real-dimension fake embeddings on disk; returns dirs + row count."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    source = FIXTURES / "chunks" / f"{slug}.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_law(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))
    count = len(source.read_text(encoding="utf-8").splitlines())
    return chunks_dir, embeddings_dir, count


def _run_load(chunks_dir: Path, embeddings_dir: Path) -> int:
    return main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(embeddings_dir)])


@pytest.mark.integration
def test_load_creates_schema_index_and_rows(test_db: psycopg.Connection, tmp_path: Path) -> None:
    chunks_dir, embeddings_dir, count = _write_artifacts(tmp_path)

    assert _run_load(chunks_dir, embeddings_dir) == 0

    rows = test_db.execute("SELECT count(*) FROM chunks").fetchone()
    assert rows == (count,)
    index = test_db.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'chunks_embedding_idx'"
    ).fetchone()
    assert index is not None
    assert "USING hnsw" in index[0] and "vector_cosine_ops" in index[0]


@pytest.mark.integration
def test_rerunning_load_is_idempotent(test_db: psycopg.Connection, tmp_path: Path) -> None:
    chunks_dir, embeddings_dir, count = _write_artifacts(tmp_path)
    assert _run_load(chunks_dir, embeddings_dir) == 0

    # Change one chunk's text upstream and re-run: same row count, the row updated in place.
    chunks_file = chunks_dir / "kassensichv.jsonl"
    records = [json.loads(line) for line in chunks_file.read_text("utf-8").splitlines()]
    records[0]["text"] = "Ein geänderter Text."
    chunks_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    embed_law(chunks_file, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))

    assert _run_load(chunks_dir, embeddings_dir) == 0

    assert test_db.execute("SELECT count(*) FROM chunks").fetchone() == (count,)
    row = test_db.execute("SELECT text FROM chunks WHERE id = %s", (records[0]["id"],)).fetchone()
    assert row == ("Ein geänderter Text.",)


@pytest.mark.integration
def test_a_removed_chunk_is_pruned_on_reload(test_db: psycopg.Connection, tmp_path: Path) -> None:
    chunks_dir, embeddings_dir, count = _write_artifacts(tmp_path)
    assert _run_load(chunks_dir, embeddings_dir) == 0

    chunks_file = chunks_dir / "kassensichv.jsonl"
    lines = chunks_file.read_text(encoding="utf-8").splitlines(keepends=True)
    removed_id = json.loads(lines[-1])["id"]
    chunks_file.write_text("".join(lines[:-1]), encoding="utf-8")
    embed_law(chunks_file, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))

    assert _run_load(chunks_dir, embeddings_dir) == 0

    assert test_db.execute("SELECT count(*) FROM chunks").fetchone() == (count - 1,)
    stale = test_db.execute("SELECT 1 FROM chunks WHERE id = %s", (removed_id,)).fetchone()
    assert stale is None


@pytest.mark.integration
def test_a_wrong_dimension_artifact_is_rejected(
    test_db: psycopg.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    source = FIXTURES / "chunks" / "kassensichv.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_law(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM + 1))

    assert _run_load(chunks_dir, embeddings_dir) == 1

    assert "does not match the schema" in capsys.readouterr().err
    assert test_db.execute("SELECT count(*) FROM chunks").fetchone() == (0,)  # nothing partial
