"""Contract tests for the assemble stage — golden-prompt driven, no network or model.

``assemble`` is a pure function, so the whole suite runs offline. The golden files under
``tests/fixtures/prompts/`` are committed artifacts (regenerate them deliberately, never on
the fly) that pin the exact system and user message bytes against the fixture chunks.
"""

import json
import re
from pathlib import Path

import pytest

from rag.assemble import MAX_PROMPT_CHARS, AssembleError, assemble
from rag.retrieve import RetrievedChunk

FIXTURES = Path(__file__).parent / "fixtures"

# The exact inputs the golden prompt files were generated from (first three citypark
# records, in file order, with fixed fake distances). assemble ignores distance, but the
# values are pinned here so the golden files stay reproducible.
GOLDEN_QUESTION = "Where does City Park play?"
GOLDEN_DISTANCES = (0.1234, 0.2345, 0.3456)


def _chunk(citation: str, text: str, distance: float = 0.0) -> RetrievedChunk:
    """A RetrievedChunk with only the two consumed fields set; the rest are placeholders."""
    return RetrievedChunk(
        id="x#1",
        source_title="Test F.C.",
        citation=citation,
        source_url="https://en.wikipedia.org/wiki/Test",
        text=text,
        distance=distance,
    )


def _golden_chunks() -> list[RetrievedChunk]:
    """The first three citypark chunks as retrieve would hand them to assemble."""
    records = [
        json.loads(line)
        for line in (FIXTURES / "chunks" / "citypark.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    return [
        RetrievedChunk(
            id=record["id"],
            source_title=record["source_title"],
            citation=record["citation"],
            source_url=record["source_url"],
            text=record["text"],
            distance=distance,
        )
        for record, distance in zip(records[:3], GOLDEN_DISTANCES, strict=True)
    ]


def test_assemble_matches_the_golden_prompt() -> None:
    prompt = assemble(GOLDEN_QUESTION, _golden_chunks())

    prompts = FIXTURES / "prompts"
    assert prompt.system == (prompts / "citypark_system.txt").read_text(encoding="utf-8")
    assert prompt.user == (prompts / "citypark_user.txt").read_text(encoding="utf-8")


def test_assemble_is_deterministic() -> None:
    first = assemble(GOLDEN_QUESTION, _golden_chunks())
    second = assemble(GOLDEN_QUESTION, _golden_chunks())

    assert first == second


def test_excerpts_are_numbered_in_input_order() -> None:
    # Distances descend, so numbering follows the given order, not the ranking.
    chunks = [
        _chunk("A F.C. — History", "First excerpt.", distance=0.9),
        _chunk("B F.C. — Stadium", "Second excerpt.", distance=0.5),
        _chunk("C F.C. — Honours", "Third excerpt.", distance=0.1),
    ]

    user = assemble("A question?", chunks).user

    assert "[1] A F.C. — History\nFirst excerpt." in user
    assert "[2] B F.C. — Stadium\nSecond excerpt." in user
    assert "[3] C F.C. — Honours\nThird excerpt." in user
    assert user.index("[1]") < user.index("[2]") < user.index("[3]")
    assert user.endswith("Question: A question?")  # question last, no trailing newline


def test_zero_chunks_is_an_error() -> None:
    with pytest.raises(AssembleError, match="at least one retrieved chunk"):
        assemble("A question?", [])


def test_over_budget_prompt_fails_loudly_with_size_and_budget() -> None:
    oversized = _chunk("Big F.C. — History", "x" * (MAX_PROMPT_CHARS + 1))

    with pytest.raises(AssembleError) as excinfo:
        assemble("A question?", [oversized])

    message = str(excinfo.value)
    assert str(MAX_PROMPT_CHARS) in message  # the budget number
    reported_size = int(re.findall(r"\d+", message)[0])  # the actual size, named first
    assert reported_size > MAX_PROMPT_CHARS
    assert "smaller --top-k" in message  # the actionable next step
