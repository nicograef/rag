"""Shared test plumbing — the deterministic fake `Embedder` and the throwaway database.

The default suite uses only `FakeEmbedder`; the `test_db` fixture backs the opt-in
`integration` tests and skips them cleanly when no database is reachable.
"""

import hashlib
import os

import psycopg
import psycopg.conninfo
import pytest


class FakeEmbedder:
    """A trivial deterministic `Embedder`: vectors derived from a hash of the text.

    Same text → same vector on every platform, so golden-file tests are byte-exact and the
    default suite never loads torch, downloads a model, or touches the network. The vectors
    are meaningless as semantics — they only exercise the stages' plumbing. `dim` defaults
    to a readable 8 for golden files; the load integration tests pass the schema's real
    dimension instead.
    """

    model = "fake-embedder"

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        block = 0
        while len(values) < self.dim:
            digest = hashlib.sha256(f"{block}:{text}".encode()).digest()
            values.extend(round(byte / 255, 6) for byte in digest)
            block += 1
        return values[: self.dim]


TEST_DB = "rag_test"


def _admin_settings() -> dict[str, str]:
    """Connection settings from the environment, with `.env.example`'s documented defaults."""
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "user": os.environ.get("POSTGRES_USER", "rag"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
        "dbname": os.environ.get("POSTGRES_DB", "rag"),
    }


@pytest.fixture()
def test_db(monkeypatch: pytest.MonkeyPatch):
    """A fresh throwaway database for one test, with the environment pointed at it."""
    settings = _admin_settings()
    try:
        admin = psycopg.connect(
            psycopg.conninfo.make_conninfo(**settings, connect_timeout=2), autocommit=True
        )
    except psycopg.OperationalError:
        pytest.skip("database unreachable — start it with `make db` (see .env.example)")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")

    monkeypatch.setenv("POSTGRES_HOST", settings["host"])
    monkeypatch.setenv("POSTGRES_PORT", settings["port"])
    monkeypatch.setenv("POSTGRES_USER", settings["user"])
    monkeypatch.setenv("POSTGRES_PASSWORD", settings["password"])
    monkeypatch.setenv("POSTGRES_DB", TEST_DB)
    connection = psycopg.connect(
        psycopg.conninfo.make_conninfo(**{**settings, "dbname": TEST_DB}), autocommit=True
    )
    yield connection

    connection.close()
    admin.execute(f"DROP DATABASE {TEST_DB} WITH (FORCE)")
    admin.close()
