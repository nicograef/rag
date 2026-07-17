"""Contract tests for the generate stage — no network, all HTTP via httpx.MockTransport.

The default tests drive the Ollama chat client through a mock transport: request shape,
streamed-delta reassembly, stats extraction, and every failure hint. The opt-in
`integration` test runs one tiny real prompt through the local server, skipping cleanly
when Ollama or the pinned model is unavailable.
"""

import json

import httpx
import pytest

from rag.assemble import Prompt
from rag.generate import (
    MIN_P,
    MODEL_TAG,
    NUM_CTX,
    NUM_PREDICT,
    SEED,
    TEMPERATURE,
    TOP_K,
    TOP_P,
    GenerateError,
    generate,
    ollama_base_url,
)

PROMPT = Prompt(system="You are an assistant.", user="Where does the club play?")

# The delta lines carry incremental content; the final line carries `done` and the stats.
DELTA_LINES = (
    {"message": {"role": "assistant", "content": "Para"}, "done": False},
    {"message": {"role": "assistant", "content": "graf "}, "done": False},
    {"message": {"role": "assistant", "content": "eins."}, "done": False},
)
DONE_LINE = {
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "total_duration": 3_000_000_000,
    "load_duration": 1_000_000_000,
    "prompt_eval_count": 42,
    "prompt_eval_duration": 500_000_000,
    "eval_count": 7,
    "eval_duration": 1_500_000_000,
}


def ndjson(*objects: dict) -> bytes:
    """Encode objects as an NDJSON body, the shape Ollama streams from `/api/chat`."""
    return "".join(json.dumps(obj) + "\n" for obj in objects).encode("utf-8")


class Recorder:
    """A MockTransport handler that serves one response and captures the request it saw."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.request: httpx.Request | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        return self._response

    @property
    def payload(self) -> dict:
        assert self.request is not None
        return json.loads(self.request.content)


def serving(response: httpx.Response) -> tuple[httpx.MockTransport, Recorder]:
    """A mock transport serving `response`, plus the recorder that captures the request."""
    recorder = Recorder(response)
    return httpx.MockTransport(recorder), recorder


def _discard(_delta: str) -> None:
    """An `on_delta` that drops its argument, for tests that only assert on the result."""


def test_request_carries_the_model_messages_and_pinned_options() -> None:
    transport, recorder = serving(httpx.Response(200, content=ndjson(*DELTA_LINES, DONE_LINE)))

    generate(PROMPT, on_delta=_discard, transport=transport)

    assert recorder.request is not None
    assert str(recorder.request.url) == f"{ollama_base_url()}/api/chat"
    body = recorder.payload
    assert body["model"] == MODEL_TAG
    assert body["stream"] is True
    assert body["messages"] == [
        {"role": "system", "content": PROMPT.system},
        {"role": "user", "content": PROMPT.user},
    ]
    assert body["options"] == {
        "num_ctx": NUM_CTX,
        "num_predict": NUM_PREDICT,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "top_k": TOP_K,
        "min_p": MIN_P,
        "seed": SEED,
    }


def test_streamed_deltas_are_reassembled_in_order() -> None:
    transport, _ = serving(httpx.Response(200, content=ndjson(*DELTA_LINES, DONE_LINE)))
    deltas: list[str] = []

    result = generate(PROMPT, on_delta=deltas.append, transport=transport)

    assert deltas == ["Para", "graf ", "eins."]  # forwarded live, in order
    assert result.answer == "Paragraf eins."


def test_stats_convert_nanoseconds_to_seconds_and_capture_done_reason() -> None:
    transport, _ = serving(httpx.Response(200, content=ndjson(*DELTA_LINES, DONE_LINE)))

    stats = generate(PROMPT, on_delta=_discard, transport=transport).stats

    assert stats.prompt_tokens == 42
    assert stats.answer_tokens == 7
    assert stats.load_seconds == 1.0
    assert stats.prompt_eval_seconds == 0.5
    assert stats.eval_seconds == 1.5
    assert stats.total_seconds == 3.0
    assert stats.done_reason == "stop"


def test_unknown_model_maps_404_to_the_llm_pull_hint() -> None:
    transport, _ = serving(httpx.Response(404, content=b'{"error":"model not found"}'))

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=transport)

    assert f"model '{MODEL_TAG}' not found" in str(caught.value)
    assert "make llm-pull" in str(caught.value)


def test_a_connection_failure_maps_to_the_llm_hint() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=httpx.MockTransport(refuse))

    assert ollama_base_url() in str(caught.value)
    assert "make llm" in str(caught.value)


def test_a_mid_stream_transport_drop_maps_to_the_logs_hint() -> None:
    def drop(_request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed connection without a complete message body")

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=httpx.MockTransport(drop))

    assert "mid-request" in str(caught.value)
    assert "docker compose logs ollama" in str(caught.value)


def test_a_non_404_http_error_reports_status_and_body() -> None:
    transport, _ = serving(httpx.Response(400, content=b'{"error":"model is required"}'))

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=transport)

    assert "HTTP 400" in str(caught.value)
    assert "model is required" in str(caught.value)


def test_a_mid_stream_error_line_aborts_with_its_message() -> None:
    lines = ndjson(
        {"message": {"role": "assistant", "content": "Para"}, "done": False},
        {"error": "an unexpected error occurred"},
    )
    transport, _ = serving(httpx.Response(200, content=lines))

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=transport)

    assert "an unexpected error occurred" in str(caught.value)


def test_a_stream_without_a_done_line_is_an_error() -> None:
    transport, _ = serving(httpx.Response(200, content=ndjson(*DELTA_LINES)))

    with pytest.raises(GenerateError) as caught:
        generate(PROMPT, on_delta=_discard, transport=transport)

    assert "without a final done message" in str(caught.value)


def _ollama_skip_reason() -> str | None:
    """A skip reason when Ollama or the pinned model is unavailable within ~2s, else None."""
    base = ollama_base_url()
    try:
        with httpx.Client(timeout=2.0) as client:
            client.get(f"{base}/api/version").raise_for_status()
            tags = client.get(f"{base}/api/tags").json()
    except httpx.HTTPError as error:
        return f"Ollama not reachable at {base} ({error}) — run `make llm`"
    if MODEL_TAG not in {model.get("name") for model in tags.get("models", [])}:
        return f"model {MODEL_TAG} not pulled — run `make llm-pull`"
    return None


@pytest.mark.integration
def test_generate_answers_a_tiny_prompt_from_the_real_model() -> None:
    reason = _ollama_skip_reason()
    if reason:
        pytest.skip(reason)

    prompt = Prompt(system="Answer with one word.", user="Say hello.")
    deltas: list[str] = []
    result = generate(prompt, on_delta=deltas.append)

    assert result.answer.strip()
    assert "".join(deltas) == result.answer
    assert result.stats.prompt_tokens > 0
    assert result.stats.done_reason == "stop"
