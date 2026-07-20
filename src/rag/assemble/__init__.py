"""Assemble stage — build the grounded chat prompt from a question and the retrieved chunks.

Composes retrieve's ranked ``RetrievedChunk`` list and the user's question into one
``Prompt`` (system + user message) for the chat API: a static English system prompt carrying
the grounding, abstention, citation, and CC BY-SA attribution directives, then a user message
that lists the numbered excerpts in rank order and ends with the question. The layout is fixed and
deterministic — identical inputs yield a byte-identical prompt — and everything static sits
at the front so the served model can reuse the computed prefix (prompt caching / KV caching).
Only each chunk's ``citation`` and ``text`` enter the prompt. A pure function with no CLI;
the ``ask`` composition wires retrieve → assemble → generate. A conservative character
budget, derived from the pinned context length, fails loudly rather than ever truncating.

Stage contract: docs/stages/assemble.md
Theory: docs/theory/llm-generation.md
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rag.generate import NUM_CTX, NUM_PREDICT
from rag.retrieve import RetrievedChunk


@dataclass(frozen=True)
class Prompt:
    """Assemble's output: the system and user message strings for the chat API."""

    system: str
    user: str

    @property
    def char_count(self) -> int:
        """Prompt size in characters — the one definition the budget check and ask log share."""
        return len(self.system) + len(self.user)


# Directives (grounding, partial-answer, abstention, brevity, verbatim citation) plus a
# CC BY-SA attribution line: the excerpts are Wikipedia text, so every answer that surfaces
# one must credit its source article — the citation carries that attribution. The
# partial-answer directive counters over-abstention; the verbatim-citation directive keeps the
# model from re-labelling a source; brevity bounds decode cost on CPU.
SYSTEM_PROMPT = (
    "You are an assistant that answers questions about football clubs using only the "
    "numbered excerpts from English Wikipedia articles given below. Answer strictly from "
    "these excerpts; do not use any other knowledge. If the excerpts answer the question only "
    "in part, give the partial answer and say what is missing. If they do not contain the "
    "answer at all, say so plainly and invent nothing. Answer in English, as concisely as "
    "possible without losing what matters. Support every statement with the number and "
    "citation of the excerpt it rests on, copying the citation verbatim — for example: "
    "[1] (Arsenal F.C. — History). The excerpts are from Wikipedia and licensed CC BY-SA 4.0; "
    "attribute each fact to its source article through that citation."
)

# The prompt's character budget, pinned to the generation model's context window
# (docs/roadmap.md "Generation model", 2026-07-18): num_ctx must also hold the answer, so the
# reserve equals num_predict. The chars-per-token floor is a conservative proxy for token-exact
# counting (deferred to Backlog 7): measured over the corpus with granite's own tokenizer the
# densest chunk runs ≈ 2.31 chars/token, so 2.3 keeps the prompt under the token budget even in
# the worst case (basis in docs/stages/assemble.md).
GENERATION_RESERVE_TOKENS = NUM_PREDICT
CHARS_PER_TOKEN_FLOOR = 2.3
MAX_PROMPT_CHARS = int((NUM_CTX - GENERATION_RESERVE_TOKENS) * CHARS_PER_TOKEN_FLOOR)


class AssembleError(Exception):
    """Raised when no chunks are given or the prompt would exceed the character budget."""


def assemble(question: str, chunks: Sequence[RetrievedChunk]) -> Prompt:
    """Build the grounded ``Prompt`` from a question and retrieve's ranked chunks.

    Numbers the excerpts ``[1]..[n]`` in the order given and puts the question last, keeping
    the static system prompt and the excerpt prefix stable across questions. Consumes only
    each chunk's ``citation`` and ``text``. Raises ``AssembleError`` when no chunks are given
    or when the prompt would exceed ``MAX_PROMPT_CHARS`` — it never truncates.
    """
    if not chunks:
        raise AssembleError("assemble needs at least one retrieved chunk")

    blocks = [
        f"[{number}] {chunk.citation}\n{chunk.text}" for number, chunk in enumerate(chunks, start=1)
    ]
    user = "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"

    prompt = Prompt(system=SYSTEM_PROMPT, user=user)
    size = prompt.char_count
    if size > MAX_PROMPT_CHARS:
        raise AssembleError(
            f"prompt is {size} characters, over the {MAX_PROMPT_CHARS}-character context "
            f"budget ({NUM_CTX} num_ctx minus {GENERATION_RESERVE_TOKENS} answer-reserve "
            f"tokens at {CHARS_PER_TOKEN_FLOOR} chars/token) — retry with a smaller --top-k"
        )
    return prompt
