"""Assemble stage — build the grounded chat prompt from a question and the retrieved chunks.

Composes retrieve's ranked ``RetrievedChunk`` list and the user's question into one
``Prompt`` (system + user message) for the chat API: a static German system prompt carrying
the grounding, abstention, and citation directives, then a user message that lists the
numbered excerpts in rank order and ends with the question. The layout is fixed and
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


# Wording refined against real model behaviour in the 2026-07-17 spot-check
# (docs/stages/generate.md, "Verification"): the partial-answer directive counters
# observed over-abstention, the verbatim-Fundstelle directive counters re-labelled
# citations (Art vs §), and the brevity directive bounds decode cost on CPU.
SYSTEM_PROMPT = (
    "Du bist ein Assistent für Fragen zum deutschen Bundesrecht. Dir werden nummerierte "
    "Auszüge aus Gesetzestexten und eine Frage vorgelegt. Beantworte die Frage "
    "ausschließlich anhand dieser Auszüge; nutze kein anderes Wissen. Wenn die Auszüge "
    "die Antwort nur teilweise enthalten, gib die Teilantwort und benenne, was offen "
    "bleibt. Wenn sie die Antwort gar nicht enthalten, sage das klar und erfinde nichts. "
    "Antworte auf Deutsch und so knapp, wie es ohne Verlust des Wesentlichen möglich "
    "ist. Belege jede Aussage mit der Nummer und Fundstelle des Auszugs, auf den sie "
    "sich stützt; übernimm die Fundstelle wörtlich aus der Bezeichnung des Auszugs, zum "
    "Beispiel: [1] (Art 1 GG) oder [2] (§ 146a AO)."
)

# The prompt's character budget, pinned to the generation model's context window
# (docs/roadmap.md "Generation model", 2026-07-17): num_ctx must also hold the answer, so
# the reserve equals num_predict. The chars-per-token floor is a conservative proxy for
# token-exact counting (deferred to Backlog 7); the measured basis is in docs/stages/assemble.md.
GENERATION_RESERVE_TOKENS = NUM_PREDICT
CHARS_PER_TOKEN_FLOOR = 2.5
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
    user = "Auszüge aus Gesetzestexten:\n\n" + "\n\n".join(blocks) + f"\n\nFrage: {question}"

    size = len(SYSTEM_PROMPT) + len(user)
    if size > MAX_PROMPT_CHARS:
        raise AssembleError(
            f"prompt is {size} characters, over the {MAX_PROMPT_CHARS}-character context "
            f"budget ({NUM_CTX} num_ctx minus {GENERATION_RESERVE_TOKENS} answer-reserve "
            f"tokens at {CHARS_PER_TOKEN_FLOOR} chars/token) — retry with a smaller --top-k"
        )
    return Prompt(system=SYSTEM_PROMPT, user=user)
