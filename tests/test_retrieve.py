"""Contract tests for the retrieve stage.

The default tests cover the logic reachable without a store — snippet formatting and the
fail-fast error hints — with the fake embedder and no database. The opt-in `integration`
tests run against a freshly loaded throwaway database (still with the fake embedder; real-
model retrieval quality is the documented manual verification in docs/stages/retrieve.md).
"""

import json
from pathlib import Path

import psycopg
import pytest
from conftest import FakeEmbedder

from rag.embed import EMBEDDING_DIM, embed_law
from rag.load import SCHEMA_SQL
from rag.load import main as load_main
from rag.retrieve import RetrievedChunk, RetrieveError, format_hit, main, retrieve

FIXTURES = Path(__file__).parent / "fixtures"


def test_format_hit_shows_rank_distance_citation_and_snippet() -> None:
    line = format_hit(1, 0.1234567, "Arsenal F.C. — History", "First sentence.\n\nSecond sentence.")

    assert line == "1. (0.1235) Arsenal F.C. — History\n   First sentence. Second sentence."


def test_format_hit_truncates_a_long_text_to_a_snippet() -> None:
    line = format_hit(2, 0.5, "Club F.C. — Stadium", "word " * 100)

    snippet = line.split("\n   ")[1]
    assert len(snippet) == 200
    assert snippet.endswith("…")


def test_main_without_connection_settings_fails_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(["How does it work?"], embedder=FakeEmbedder())

    assert exit_code == 1
    assert ".env.example" in capsys.readouterr().err


def test_main_checks_settings_before_constructing_the_real_embedder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression guard: a missing .env must fail in milliseconds, not after torch loads.
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "rag.retrieve.SentenceTransformerEmbedder",
        lambda: pytest.fail("the real embedder must not be constructed"),
    )

    exit_code = main(["a question"])  # no injected embedder — the real CLI path

    assert exit_code == 1
    assert ".env.example" in capsys.readouterr().err


def test_a_non_positive_top_k_is_rejected_at_parsing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["How does it work?", "--top-k", "0"], embedder=FakeEmbedder())

    assert "--top-k must be at least 1" in capsys.readouterr().err


def test_main_with_an_unreachable_database_fails_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Not an integration test: the connection to port 1 is refused, so it never needs a DB.
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "1")  # nothing listens here — connect refuses at once
    monkeypatch.setenv("POSTGRES_USER", "rag")
    monkeypatch.setenv("POSTGRES_PASSWORD", "rag")
    monkeypatch.setenv("POSTGRES_DB", "rag")

    exit_code = main(["a question"], embedder=FakeEmbedder())

    assert exit_code == 1
    assert "make db" in capsys.readouterr().err


def _load_citypark(test_db: psycopg.Connection, tmp_path: Path) -> None:
    """Load the citypark fixture into the throwaway database with fake embeddings."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    source = FIXTURES / "chunks" / "citypark.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_law(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))
    assert (
        load_main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(embeddings_dir)]) == 0
    )


@pytest.mark.integration
def test_retrieve_returns_ranked_records_and_main_prints_them(
    test_db: psycopg.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Query with a known chunk's exact text: the fake embedder is deterministic, so that
    # chunk is its own nearest neighbor (distance 0) — the ranking is checkable end to end.
    _load_citypark(test_db, tmp_path)
    target = json.loads(
        (FIXTURES / "chunks" / "citypark.jsonl").read_text(encoding="utf-8").splitlines()[1]
    )
    capsys.readouterr()  # drop the load output

    hits = retrieve(target["text"], embedder=FakeEmbedder(dim=EMBEDDING_DIM), top_k=3)

    assert len(hits) == 3
    assert all(isinstance(hit, RetrievedChunk) for hit in hits)
    assert [hit.distance for hit in hits] == sorted(hit.distance for hit in hits)  # ascending
    nearest = hits[0]
    assert nearest.distance == pytest.approx(0.0, abs=1e-6)
    # All six RetrievedChunk fields carry through from the loaded row.
    assert nearest.id == target["id"]
    assert nearest.source_title == target["source_title"]
    assert nearest.citation == target["citation"]
    assert nearest.source_url == target["source_url"]
    assert nearest.text == target["text"]

    exit_code = main([target["text"], "--top-k", "3"], embedder=FakeEmbedder(dim=EMBEDDING_DIM))

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6  # three hits, two lines each — `--top-k` respected
    assert lines[0].startswith(f"1. (0.0000) {target['citation']}")


@pytest.mark.integration
def test_retrieve_without_a_loaded_table_reports_the_missing_table(
    test_db: psycopg.Connection,
) -> None:
    # The vector type exists so registration succeeds, but no one ran the load stage.
    test_db.execute("CREATE EXTENSION IF NOT EXISTS vector")

    with pytest.raises(RetrieveError, match="no chunks table"):
        retrieve("a question", embedder=FakeEmbedder(dim=EMBEDDING_DIM))


@pytest.mark.integration
def test_retrieve_on_a_fresh_database_reports_the_missing_table(
    test_db: psycopg.Connection,
) -> None:
    # A database load never touched has no vector type either — registration itself fails,
    # and the user gets the same hint instead of a raw ProgrammingError.
    with pytest.raises(RetrieveError, match="no chunks table"):
        retrieve("a question", embedder=FakeEmbedder(dim=EMBEDDING_DIM))


@pytest.mark.integration
def test_retrieve_from_an_empty_table_reports_it_empty(test_db: psycopg.Connection) -> None:
    test_db.execute(SCHEMA_SQL)  # schema and index in place, but no rows loaded

    with pytest.raises(RetrieveError, match="empty"):
        retrieve("a question", embedder=FakeEmbedder(dim=EMBEDDING_DIM))
