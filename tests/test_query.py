"""Contract tests for the dev query command.

The default tests cover the assembly logic — result formatting and error handling — with
the fake embedder and no database. The opt-in `integration` test runs one query end to end
against a freshly loaded throwaway database (still with the fake embedder; real-model
retrieval quality is the documented manual verification in docs/stages/load.md).
"""

import json
from pathlib import Path

import psycopg
import pytest
from conftest import FakeEmbedder

from rag.embed import EMBEDDING_DIM, embed_law
from rag.load import main as load_main
from rag.query import format_hit, main

FIXTURES = Path(__file__).parent / "fixtures"


def test_format_hit_shows_rank_distance_citation_and_snippet() -> None:
    line = format_hit(1, 0.1234567, "§ 1 KassenSichV", "Erster Satz.\n\nZweiter Satz.")

    assert line == "1. (0.1235) § 1 KassenSichV\n   Erster Satz. Zweiter Satz."


def test_format_hit_truncates_a_long_text_to_a_snippet() -> None:
    line = format_hit(2, 0.5, "§ 2 SomeLaw", "Wort " * 100)

    snippet = line.split("\n   ")[1]
    assert len(snippet) == 200
    assert snippet.endswith("…")


def test_main_without_connection_settings_fails_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(["Wie funktioniert das?"], embedder=FakeEmbedder())

    assert exit_code == 1
    assert ".env.example" in capsys.readouterr().err


@pytest.mark.integration
def test_query_prints_ranked_hits_from_the_store(
    test_db: psycopg.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Load one law with fake embeddings, then query with a known chunk's exact text: the
    # fake embedder is deterministic, so that chunk is its own nearest neighbor (distance 0).
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    source = FIXTURES / "chunks" / "kassensichv.jsonl"
    (chunks_dir / source.name).write_bytes(source.read_bytes())
    embeddings_dir = tmp_path / "embeddings"
    embed_law(chunks_dir / source.name, embeddings_dir, FakeEmbedder(dim=EMBEDDING_DIM))
    assert (
        load_main(["--chunks-dir", str(chunks_dir), "--embeddings-dir", str(embeddings_dir)]) == 0
    )
    target = json.loads(source.read_text(encoding="utf-8").splitlines()[1])
    capsys.readouterr()  # drop the load output

    exit_code = main([target["text"], "--top-k", "3"], embedder=FakeEmbedder(dim=EMBEDDING_DIM))

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6  # three hits, two lines each — `--top-k` respected
    assert lines[0].startswith(f"1. (0.0000) {target['citation']}")
