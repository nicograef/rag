"""Shared test doubles — the deterministic fake `Embedder` the default suite injects."""

import hashlib


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
