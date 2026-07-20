"""Contract tests for the ask composition — fakes only, no network, database, or model.

``ask.main`` wires retrieve → assemble → generate. The tests inject fake ``retrieve_fn`` and
``generate_fn`` so the flow runs offline, and pin the two contracts a caller depends on: the
stdout shape (streamed answer, blank line, numbered ``Sources:`` block, licence notice) and
the one-line-per-step stderr log. ``assemble`` is exercised for real — it is a pure function.
"""

import pytest

from rag.ask import LICENCE_NOTICE, main
from rag.assemble import SYSTEM_PROMPT
from rag.generate import GenerateError, GenerateResult, GenerationStats
from rag.retrieve import RetrievedChunk, RetrieveError

HITS = [
    RetrievedChunk(
        id="arsenal#History",
        source_title="Arsenal F.C.",
        citation="Arsenal F.C. — History",
        source_url="https://en.wikipedia.org/wiki/Arsenal_F.C.",
        text="First excerpt.",
        distance=0.1,
    ),
    RetrievedChunk(
        id="arsenal#Stadiums",
        source_title="Arsenal F.C.",
        citation="Arsenal F.C. — Stadiums",
        source_url="https://en.wikipedia.org/wiki/Arsenal_F.C.",
        text="Second excerpt.",
        distance=0.2,
    ),
]

DELTAS = ["Ars", "enal play ", "at the Emirates."]
STATS = GenerationStats(
    prompt_tokens=42,
    answer_tokens=7,
    load_seconds=1.0,
    prompt_eval_seconds=0.5,
    eval_seconds=1.5,
    total_seconds=3.0,
    done_reason="stop",
)


def _retrieve_returning(hits):
    """A ``retrieve_fn`` that returns the first ``top_k`` of ``hits``."""

    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        return list(hits[:top_k])

    return retrieve_fn


def _generate_streaming(deltas, stats):
    """A ``generate_fn`` that forwards each delta live, then returns the joined answer."""

    def generate_fn(prompt, on_delta):
        for delta in deltas:
            on_delta(delta)
        return GenerateResult(answer="".join(deltas), stats=stats)

    return generate_fn


def test_happy_path_streams_answer_then_sources_and_logs_each_step(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        ["Where does Arsenal play?", "--top-k", "2"],
        retrieve_fn=_retrieve_returning(HITS),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )

    captured = capsys.readouterr()
    assert exit_code == 0

    answer = "".join(DELTAS)
    sources = "".join(f"[{n}] {hit.citation} — {hit.source_url}\n" for n, hit in enumerate(HITS, 1))
    assert captured.out == f"{answer}\n\nSources:\n{sources}\n{LICENCE_NOTICE}\n"

    err = captured.err
    assert "question: Where does Arsenal play?" in err
    assert "hit 1: (0.1000) Arsenal F.C. — History" in err
    assert "hit 2: (0.2000) Arsenal F.C. — Stadiums" in err
    assert "prompt: " in err and "(system " in err and "user " in err
    assert "generation: 42 prompt + 7 answer tokens" in err
    assert "total 3.0s, done: stop" in err


def test_top_k_is_passed_through_to_retrieve_fn() -> None:
    seen: dict[str, int] = {}

    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        seen["top_k"] = top_k
        return HITS

    main(
        ["a question", "--top-k", "3"],
        retrieve_fn=retrieve_fn,
        generate_fn=_generate_streaming(DELTAS, STATS),
    )

    assert seen["top_k"] == 3


def test_a_non_positive_top_k_is_rejected_at_parsing(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["a question", "--top-k", "0"])

    assert "--top-k must be at least 1" in capsys.readouterr().err


def test_verbose_dumps_both_prompt_parts_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        ["a question", "--verbose"],
        retrieve_fn=_retrieve_returning(HITS),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )

    err = capsys.readouterr().err
    assert "--- system prompt ---" in err
    assert "--- user message ---" in err
    assert SYSTEM_PROMPT in err
    assert "First excerpt." in err  # a chunk body only appears in the dumped user message


def test_without_verbose_the_prompt_is_not_dumped(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        ["a question"],
        retrieve_fn=_retrieve_returning(HITS),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )

    err = capsys.readouterr().err
    assert "--- system prompt ---" not in err
    assert SYSTEM_PROMPT not in err


def test_default_retrieve_path_checks_settings_before_the_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression guard for the real (non-injected) path: a missing .env must fail in
    # milliseconds, not after torch loads the embedding model.
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "rag.ask.SentenceTransformerEmbedder",
        lambda: pytest.fail("the real embedder must not be constructed"),
    )

    exit_code = main(["a question"])  # no injected retrieve_fn — the real CLI path

    captured = capsys.readouterr()
    assert exit_code == 1
    assert ".env.example" in captured.err
    assert captured.out == ""


def test_a_retrieve_error_exits_one_with_the_hint_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        raise RetrieveError("the chunks table is empty — run `make load` first")

    exit_code = main(
        ["a question"], retrieve_fn=retrieve_fn, generate_fn=_generate_streaming(DELTAS, STATS)
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "make load" in captured.err
    assert captured.out == ""  # nothing streamed before the failure


def test_no_hits_makes_assemble_fail_and_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        ["a question"],
        retrieve_fn=_retrieve_returning([]),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "at least one retrieved chunk" in captured.err
    assert captured.out == ""


def test_a_generate_error_exits_one_with_the_hint(capsys: pytest.CaptureFixture[str]) -> None:
    def generate_fn(prompt, on_delta):
        raise GenerateError(
            "Ollama not reachable at http://localhost:11434: refused — run `make llm` first"
        )

    exit_code = main(["a question"], retrieve_fn=_retrieve_returning(HITS), generate_fn=generate_fn)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "make llm" in captured.err
    assert captured.out == ""  # generate failed before streaming any delta


def test_deltas_reach_stdout_even_when_generate_fails_midstream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def generate_fn(prompt, on_delta):
        on_delta("Part")
        on_delta("ial answer")
        raise GenerateError("an unexpected error occurred")

    exit_code = main(["a question"], retrieve_fn=_retrieve_returning(HITS), generate_fn=generate_fn)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == "Partial answer\n"  # the partial answer plus its closing newline
    assert "an unexpected error occurred" in captured.err
