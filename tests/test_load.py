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

from rag.embed import EMBEDDING_DIM, embed_article
from rag.load import LoadError, Row, join_article, main, read_records

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_records(slug: str = "brentford") -> tuple[list[dict], list[dict]]:
    """One article's chunk records plus matching fake embedding records (dim 8)."""
    chunks = read_records(FIXTURES / "chunks" / f"{slug}.jsonl")
    embeddings = read_records(FIXTURES / "embeddings" / f"{slug}.jsonl")
    return chunks, embeddings


def _generated_records(slug: str, tmp_path: Path) -> tuple[list[dict], list[dict]]:
    """An article's chunk fixture plus dim-8 fake embeddings generated on the fly.

    tests/fixtures/embeddings/ only ships brentford and citypark goldens, so any other article
    whose part we want to exercise gets its vectors from `embed_article` into `tmp_path` instead.
    """
    chunks_file = FIXTURES / "chunks" / f"{slug}.jsonl"
    embeddings_file = embed_article(chunks_file, tmp_path, FakeEmbedder(dim=8))
    return read_records(chunks_file), read_records(embeddings_file)


def test_join_article_pairs_every_chunk_with_its_vector() -> None:
    chunks, embeddings = _fixture_records()

    rows = join_article("brentford", chunks, embeddings, dim=8)

    assert [row.id for row in rows] == [chunk["id"] for chunk in chunks]
    for row, chunk in zip(rows, chunks, strict=True):
        assert isinstance(row, Row)
        assert row.text == chunk["text"]
        assert row.citation == chunk["citation"]
        assert len(row.embedding) == 8


def test_a_chunk_without_an_embedding_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=f"without an embedding: {chunks[-1]['id']}"):
        join_article("brentford", chunks, embeddings[:-1], dim=8)


def test_an_embedding_without_a_chunk_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=f"without a chunk: {chunks[-1]['id']}"):
        join_article("brentford", chunks[:-1], embeddings, dim=8)


def test_model_disagreement_across_records_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    embeddings[0]["model"] = "another-model"

    with pytest.raises(LoadError, match="disagree on model/dim"):
        join_article("brentford", chunks, embeddings, dim=8)


def test_a_dim_not_matching_the_schema_is_an_error() -> None:
    chunks, embeddings = _fixture_records()

    with pytest.raises(LoadError, match=r"does not match the schema's vector\(16\)"):
        join_article("brentford", chunks, embeddings, dim=16)


def test_a_chunk_record_missing_a_field_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    del chunks[0]["citation"]

    with pytest.raises(LoadError, match="missing field 'citation'"):
        join_article("brentford", chunks, embeddings, dim=8)


def test_an_embedding_record_missing_a_field_is_an_error() -> None:
    chunks, embeddings = _fixture_records()
    del embeddings[0]["model"]

    with pytest.raises(LoadError, match="missing field 'model'"):
        join_article("brentford", chunks, embeddings, dim=8)


def test_a_chunk_record_slug_not_matching_the_file_is_an_error() -> None:
    chunks, embeddings = _fixture_records()  # every record's slug is "brentford"

    with pytest.raises(LoadError, match="do not match the file"):
        join_article("other", chunks, embeddings, dim=8)


def test_section_path_and_part_survive_the_join(tmp_path: Path) -> None:
    # citypark#History#1 carries a non-null part; section_path is empty for a top-level
    # Wikipedia section — both must reach the joined Row unchanged from the source chunk record.
    chunks, embeddings = _generated_records("citypark", tmp_path / "citypark")
    by_id = {row.id: row for row in join_article("citypark", chunks, embeddings, dim=8)}

    split = by_id["citypark#History#1"]
    assert split.part == {"index": 1, "total": 2}
    assert split.section_path == []  # a top-level section has an empty heading trail


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
    (chunks_dir / "article.jsonl").write_bytes(
        (FIXTURES / "chunks" / "brentford.jsonl").read_bytes()
    )

    exit_code = main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(tmp_path / "no")])

    assert exit_code == 1
    assert "make embed" in capsys.readouterr().err


# ── Opt-in integration: a real Postgres (the `test_db` fixture lives in conftest.py) ──


def _write_artifacts(tmp_path: Path, slug: str = "brentford") -> tuple[Path, Path, int]:
    """Fixture chunks plus real-dimension fake embeddings on disk; returns dirs + row count."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    source = FIXTURES / "chunks" / f"{slug}.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_article(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))
    count = len(source.read_text(encoding="utf-8").splitlines())
    return chunks_dir, embeddings_dir, count


def _run_load(chunks_dir: Path, embeddings_dir: Path) -> int:
    return main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(embeddings_dir)])


def test_main_with_an_unreachable_database_fails_with_a_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Not an integration test: the connection to port 1 is refused, so it never needs a DB.
    chunks_dir, embeddings_dir, _ = _write_artifacts(tmp_path)
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "1")  # nothing listens here — connect refuses at once
    monkeypatch.setenv("POSTGRES_USER", "rag")
    monkeypatch.setenv("POSTGRES_PASSWORD", "rag")
    monkeypatch.setenv("POSTGRES_DB", "rag")

    assert _run_load(chunks_dir, embeddings_dir) == 1
    assert "make db" in capsys.readouterr().err


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
    chunks_file = chunks_dir / "brentford.jsonl"
    records = [json.loads(line) for line in chunks_file.read_text("utf-8").splitlines()]
    records[0]["text"] = "An edited text."
    chunks_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    embed_article(chunks_file, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))

    assert _run_load(chunks_dir, embeddings_dir) == 0

    assert test_db.execute("SELECT count(*) FROM chunks").fetchone() == (count,)
    row = test_db.execute("SELECT text FROM chunks WHERE id = %s", (records[0]["id"],)).fetchone()
    assert row == ("An edited text.",)


@pytest.mark.integration
def test_a_removed_chunk_is_pruned_on_reload(test_db: psycopg.Connection, tmp_path: Path) -> None:
    chunks_dir, embeddings_dir, count = _write_artifacts(tmp_path)
    assert _run_load(chunks_dir, embeddings_dir) == 0

    chunks_file = chunks_dir / "brentford.jsonl"
    lines = chunks_file.read_text(encoding="utf-8").splitlines(keepends=True)
    removed_id = json.loads(lines[-1])["id"]
    chunks_file.write_text("".join(lines[:-1]), encoding="utf-8")
    embed_article(chunks_file, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))

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
    source = FIXTURES / "chunks" / "brentford.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_article(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM + 1))

    assert _run_load(chunks_dir, embeddings_dir) == 1

    assert "does not match the schema" in capsys.readouterr().err
    assert test_db.execute("SELECT count(*) FROM chunks").fetchone() == (0,)  # nothing partial


@pytest.mark.integration
def test_a_stale_dimension_table_is_rejected_with_a_reset_hint(
    test_db: psycopg.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A chunks table left at a different embedding dimension is not migrated by
    # CREATE ... IF NOT EXISTS; load must refuse with a `make reset` hint before the first
    # insert, not fail on an opaque pgvector error.
    stale_dim = EMBEDDING_DIM + 100
    test_db.execute("CREATE EXTENSION IF NOT EXISTS vector")
    test_db.execute(f"CREATE TABLE chunks (id text PRIMARY KEY, embedding vector({stale_dim}))")
    chunks_dir, embeddings_dir, _ = _write_artifacts(tmp_path)

    assert _run_load(chunks_dir, embeddings_dir) == 1

    err = capsys.readouterr().err
    assert "make reset" in err
    assert f"vector({stale_dim})" in err


@pytest.mark.integration
def test_section_path_and_part_round_trip_through_the_database(
    test_db: psycopg.Connection, tmp_path: Path
) -> None:
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    embeddings_dir = tmp_path / "embeddings"
    source = FIXTURES / "chunks" / "citypark.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embed_article(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))

    assert _run_load(chunks_dir, embeddings_dir) == 0

    # part is a jsonb column → a dict on the way back.
    split = {r["id"]: r for r in read_records(source)}
    expected_part = split["citypark#History#1"]["part"]
    assert expected_part is not None
    row = test_db.execute(
        "SELECT part FROM chunks WHERE id = %s", ("citypark#History#1",)
    ).fetchone()
    assert row == (expected_part,)

    # section_path is a text[] column → an empty list for a top-level section.
    row = test_db.execute(
        "SELECT section_path FROM chunks WHERE id = %s", ("citypark#History#1",)
    ).fetchone()
    assert row == ([],)
