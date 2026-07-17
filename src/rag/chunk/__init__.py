"""Chunk stage — turn the corpus Markdown into structure-aware chunk records.

Reads each law's Markdown from ``data/corpus/<slug>.md`` (produced by the convert stage)
and writes one JSONL file per law to ``data/chunks/<slug>.jsonl``: one ``Chunk`` record
per norm unit (§, Art, Präambel, Eingangsformel, Anlage, …). The corpus Markdown is parsed
by hand exactly as convert *writes* it — front matter, ATX headings, and blank-line-separated
body blocks — with no YAML or Markdown library. The transform is deterministic: same corpus
input, byte-identical JSONL. Any construct the chunker cannot place raises ``ChunkError``
instead of silently dropping normative text.

A unit that fits ``max_chars`` becomes one whole chunk; an oversized unit is split into
ordered parts — Absatz groups with one-Absatz overlap, a recursive-character fallback for a
single overlong Absatz, or a whole atomic table (the only over-max chunk, logged). A second
pass merges consecutive sub-``merge_floor`` whole units that share a ``section_path`` into one
chunk (never crossing a section boundary, a skipped unit, or the max). Empty-body
``(weggefallen)`` units emit no chunk.

Stage contract: docs/stages/chunk.md
Theory: docs/theory/chunking.md
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from rag import CHUNKS_DIR, CORPUS_DIR

# Front matter fields the chunk stage consumes (the rest — title, builddate — are ignored).
SLUG_KEY = "slug"
LAW_KEY = "abbreviation"
SOURCE_URL_KEY = "source_url"
FETCHED_AT_KEY = "fetched_at"
REQUIRED_KEYS = (SLUG_KEY, LAW_KEY, SOURCE_URL_KEY, FETCHED_AT_KEY)

# The separator convert puts between a norm unit's enbez and its optional titel.
HEADING_SEPARATOR = " — "

# Default size policy: a unit whose `text` exceeds this is split; whole units below the
# merge floor are candidates to merge with same-section neighbours.
DEFAULT_MAX_CHARS = 2000
DEFAULT_MERGE_FLOOR = 500

# An Absatz opens at a block whose first line starts with `(1)`, `(2a)`, … (§-paragraph marker).
ABSATZ_MARKER = re.compile(r"^\(\d+[a-z]?\)")

# The blank line that separates blocks/Absätze in a body (and joins them back into `text`).
BLOCK_SEPARATOR = "\n\n"

# Ordered separators for the recursive character fallback: paragraph → line → sentence → word.
CHAR_SEPARATORS = (BLOCK_SEPARATOR, "\n", ". ", " ")


class ChunkError(Exception):
    """Raised when a law's corpus Markdown cannot be chunked faithfully."""


@dataclass(frozen=True)
class Chunk:
    """One retrievable chunk record; serialized one-per-line to JSONL in field order."""

    id: str
    text: str
    slug: str
    law: str
    unit: str
    section_path: list[str]
    citation: str
    source_url: str
    fetched_at: str
    part: dict[str, int] | None


@dataclass(frozen=True)
class NormUnit:
    """One leaf heading and the material a chunk is built from: its heading, path, and body."""

    unit: str
    heading: str
    section_path: list[str]
    body: str


def parse_front_matter(lines: list[str]) -> dict[str, str]:
    """Decode convert's ``key: "value"`` front matter (scan-based backslash unescape)."""
    if not lines or lines[0] != "---":
        raise ChunkError("missing front matter opening `---`")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            missing = [key for key in REQUIRED_KEYS if key not in fields]
            if missing:
                raise ChunkError(f"front matter missing required key(s): {', '.join(missing)}")
            return fields
        key, separator, rest = line.partition(": ")
        if not separator or not rest.startswith('"') or not rest.endswith('"'):
            raise ChunkError(f"malformed front matter line: {line!r}")
        fields[key] = _unescape(rest[1:-1])
    raise ChunkError("missing front matter closing `---`")


def _unescape(value: str) -> str:
    """Undo convert's ``\\\\``/``\\"`` escaping: a backslash escapes the next char."""
    out: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:
        raise ChunkError(f"dangling backslash in front matter value: {value!r}")
    return "".join(out)


def _heading_depth(line: str) -> int | None:
    """The ATX heading depth of a line (1–6), or ``None`` if it is not a heading."""
    depth = len(line) - len(line.lstrip("#"))
    if 1 <= depth <= 6 and line[depth : depth + 1] == " ":
        return depth
    return None


def parse_norm_units(lines: list[str]) -> list[NormUnit]:
    """Parse a law's body (after front matter) into its ordered leaf norm units.

    Applies the leaf rule (a heading is a section iff the next heading is deeper, else a
    leaf norm unit), tracks the section path via a running stack, and slices each leaf's
    body as the lines up to the next heading. A section with a non-empty body raises.
    """
    headings = [
        (index, depth) for index, line in enumerate(lines) if (depth := _heading_depth(line))
    ]
    if not headings or headings[0][1] != 1:
        raise ChunkError("body does not start with an H1 law title")

    units: list[NormUnit] = []
    section_stack: list[tuple[int, str]] = []
    for position, (line_index, depth) in enumerate(headings):
        if depth == 1:
            continue  # the law title (H1) is not a norm unit
        text = lines[line_index][depth:].strip()
        next_depth = headings[position + 1][1] if position + 1 < len(headings) else 0

        while section_stack and section_stack[-1][0] >= depth:
            section_stack.pop()
        section_path = [heading for _, heading in section_stack]

        if next_depth > depth:  # a section: it has deeper children
            body_end = headings[position + 1][0]
            if _body_between(lines, line_index + 1, body_end):
                raise ChunkError(f"section heading {text!r} has a non-empty body")
            section_stack.append((depth, text))
            continue

        body_end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        body = _body_between(lines, line_index + 1, body_end)
        unit = text.split(HEADING_SEPARATOR, 1)[0]
        units.append(NormUnit(unit=unit, heading=text, section_path=section_path, body=body))
    return units


def _body_between(lines: list[str], start: int, end: int) -> str:
    """The body lines ``[start, end)`` with leading/trailing blank lines stripped."""
    body = lines[start:end]
    while body and not body[0].strip():
        body = body[1:]
    while body and not body[-1].strip():
        body = body[:-1]
    return "\n".join(body)


@dataclass(frozen=True)
class SplitPart:
    """One ordered piece of a split unit's body.

    ``content`` is the *new* body text this part contributes; ``joiner`` is the string that
    reattaches it to the running reconstruction (``""`` for the first part, ``"\n\n"`` when
    the part starts a fresh Absatz group, ``""`` when it continues a char-split Absatz).
    ``overlap`` is the duplicated context repeated from the previous part (``""`` for the
    first part, the previous group's final Absatz for an Absatz-group part, or a trailing
    character window for a char-split part). ``text`` is what the chunk actually carries.
    """

    text: str
    content: str
    joiner: str
    overlap: str


def _split_blocks(body: str) -> list[str]:
    """Split a unit body into its blocks (maximal runs of non-blank lines)."""
    return [block for block in body.split(BLOCK_SEPARATOR) if block]


def _is_table_block(block: str) -> bool:
    """A block is an atomic table when every line is a pipe row or it is a fenced ``table``."""
    lines = block.split("\n")
    return all(line.startswith("|") for line in lines) or lines[0] == "```table"


def _group_absaetze(body: str) -> list[str]:
    """Group a body's blocks into Absätze (each an ``\\n\\n``-joined block group).

    A new Absatz opens at a block whose first line matches the ``(1)`` marker; unmarked
    leading blocks attach to the current Absatz. A body without any marker yields one Absatz
    per block.
    """
    absaetze: list[list[str]] = []
    for block in _split_blocks(body):
        opens_absatz = bool(ABSATZ_MARKER.match(block.split("\n", 1)[0]))
        if opens_absatz or not absaetze:
            absaetze.append([block])
        else:
            absaetze[-1].append(block)
    return [BLOCK_SEPARATOR.join(blocks) for blocks in absaetze]


def _char_overlap(max_chars: int) -> int:
    """The trailing character window repeated between recursive char-split pieces."""
    return max_chars // 10


def _split_recursively(text: str, max_chars: int, separators: tuple[str, ...]) -> list[str]:
    """Split ``text`` into ``≤ max_chars`` fragments whose concatenation is ``text`` verbatim.

    Tries separators in order (paragraph → line → sentence → word); a fragment that is still
    oversized is re-split on the next separator, and one with no usable separator left is
    hard-cut at ``max_chars``. Separators are kept (attached to the following fragment) so
    ``"".join(result) == text`` — the no-silent-loss guarantee. No overlap is added here.
    """
    if len(text) <= max_chars:
        return [text]
    if not separators:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    separator, rest = separators[0], separators[1:]
    if separator not in text:
        return _split_recursively(text, max_chars, rest)

    # Re-attach the separator to every piece but the first, so pieces concatenate to `text`.
    pieces = text.split(separator)
    tokens = [pieces[0], *(separator + piece for piece in pieces[1:])]

    fragments: list[str] = []
    buffer = ""
    for token in tokens:
        if buffer and len(buffer) + len(token) > max_chars:
            fragments.append(buffer)
            buffer = token
        else:
            buffer += token
    if buffer:
        fragments.append(buffer)
    return [out for fragment in fragments for out in _split_recursively(fragment, max_chars, rest)]


def _char_split_absatz(
    absatz: str, heading: str, max_chars: int, leading_joiner: str
) -> list[SplitPart]:
    """Char-split one oversized Absatz into parts, each with a trailing-window overlap.

    The Absatz is fragmented by :func:`_split_recursively` (which preserves separators, so the
    fragments concatenate back to the Absatz); each non-first fragment repeats the last
    ``_char_overlap`` characters of the previous fragment so boundary context survives.
    ``leading_joiner`` reattaches the whole Absatz to the running body during reconstruction
    (``"\n\n"`` when Absätze precede it, ``""`` when it is the body's first Absatz); the
    continuation fragments join with ``""`` because the preserved separators already do so.
    """
    budget = max_chars - len(heading) - len(BLOCK_SEPARATOR)
    overlap_window = min(_char_overlap(max_chars), max(budget // 2, 0))
    fragments = _split_recursively(absatz, max(budget - overlap_window, 1), CHAR_SEPARATORS)

    parts: list[SplitPart] = []
    previous = ""
    for index, fragment in enumerate(fragments):
        overlap = previous[-overlap_window:] if index and overlap_window else ""
        piece = overlap + fragment
        parts.append(
            SplitPart(
                text=f"{heading}{BLOCK_SEPARATOR}{piece}",
                content=fragment,
                joiner=leading_joiner if index == 0 else "",
                overlap=overlap,
            )
        )
        previous = piece
    return parts


def _split_body(body: str, heading: str, unit: str, max_chars: int) -> list[SplitPart]:
    """Split an oversized unit body into ordered parts (Absatz groups + char fallback).

    Greedily accumulates whole Absätze into a part while heading + one-Absatz overlap +
    content stays ``≤ max_chars``; each non-first group repeats the previous group's final
    Absatz as overlap (dropped when even the first content Absatz would not otherwise fit —
    the max invariant wins). A single Absatz that alone overflows falls back to a character
    split; an atomic oversized table is emitted whole (the only over-max chunk) and logged.
    """
    absaetze = _group_absaetze(body)
    parts: list[SplitPart] = []
    previous_absatz = ""  # the final Absatz of the previous group, repeated as overlap
    index = 0
    while index < len(absaetze):
        heading_len = len(heading) + len(BLOCK_SEPARATOR)
        overlap = previous_absatz
        prefix_len = heading_len + (len(overlap) + len(BLOCK_SEPARATOR) if overlap else 0)

        first = absaetze[index]
        if prefix_len + len(first) > max_chars:
            # One Absatz cannot share a part with the overlap: drop the overlap (max wins).
            overlap, prefix_len = "", heading_len
            if prefix_len + len(first) > max_chars:  # still overflows alone → recurse/atomic
                leading_joiner = BLOCK_SEPARATOR if parts else ""
                parts.extend(
                    _split_oversized_absatz(first, heading, unit, max_chars, leading_joiner)
                )
                previous_absatz = first
                index += 1
                continue

        group: list[str] = []
        used = prefix_len
        while index < len(absaetze):
            nxt = absaetze[index]
            addition = (len(BLOCK_SEPARATOR) if group else 0) + len(nxt)
            if group and used + addition > max_chars:
                break
            group.append(nxt)
            used += addition
            index += 1

        content = BLOCK_SEPARATOR.join(group)
        piece = f"{overlap}{BLOCK_SEPARATOR}{content}" if overlap else content
        parts.append(
            SplitPart(
                text=f"{heading}{BLOCK_SEPARATOR}{piece}",
                content=content,
                joiner=BLOCK_SEPARATOR if parts else "",
                overlap=overlap,
            )
        )
        previous_absatz = group[-1]
    return parts


def _split_oversized_absatz(
    absatz: str, heading: str, unit: str, max_chars: int, leading_joiner: str
) -> list[SplitPart]:
    """Handle a single Absatz that alone overflows: keep a table whole, else char-split it."""
    if _is_table_block(absatz):
        chars = len(heading) + len(BLOCK_SEPARATOR) + len(absatz)
        print(f"  ! oversized table in {unit}: {chars} chars (kept whole)")
        return [
            SplitPart(
                text=f"{heading}{BLOCK_SEPARATOR}{absatz}",
                content=absatz,
                joiner=leading_joiner,
                overlap="",
            )
        ]
    return _char_split_absatz(absatz, heading, max_chars, leading_joiner)


def body_from_parts(parts: list[SplitPart]) -> str:
    """Reconstruct a split unit's body from its parts (the no-silent-loss inverse).

    Each part contributes only its own ``content`` (never the duplicated ``overlap``),
    reattached via its ``joiner``; the result must equal the unit's original body verbatim.
    """
    return "".join(part.joiner + part.content for part in parts)


def _unit_text(unit: NormUnit) -> str:
    """The whole ``text`` of a unit: its plain heading, then a blank line, then its body."""
    return f"{unit.heading}{BLOCK_SEPARATOR}{unit.body}" if unit.body else unit.heading


def _build_chunk(
    fields: dict[str, str],
    chunk_id: str,
    text: str,
    unit: str,
    section_path: list[str],
    part: dict[str, int] | None,
) -> Chunk:
    """Assemble one ``Chunk`` record, filling the provenance fields from the front matter."""
    law = fields[LAW_KEY]
    return Chunk(
        id=chunk_id,
        text=text,
        slug=fields[SLUG_KEY],
        law=law,
        unit=unit,
        section_path=section_path,
        citation=f"{unit} {law}",
        source_url=fields[SOURCE_URL_KEY],
        fetched_at=fields[FETCHED_AT_KEY],
        part=part,
    )


def _chunks_from_unit(unit: NormUnit, fields: dict[str, str], max_chars: int) -> list[Chunk]:
    """Build the ``Chunk``(s) for one norm unit: one whole chunk, or ordered split parts."""
    slug = fields[SLUG_KEY]
    text = _unit_text(unit)

    def _chunk(chunk_id: str, chunk_text: str, part: dict[str, int] | None) -> Chunk:
        return _build_chunk(fields, chunk_id, chunk_text, unit.unit, unit.section_path, part)

    if len(text) <= max_chars or not unit.body:
        return [_chunk(f"{slug}#{unit.unit}", text, None)]

    parts = _split_body(unit.body, unit.heading, unit.unit, max_chars)
    total = len(parts)
    if total == 1:  # a lone atomic oversized table needs no part numbering
        return [_chunk(f"{slug}#{unit.unit}", parts[0].text, None)]
    return [
        _chunk(f"{slug}#{unit.unit}#{n}", part.text, {"index": n, "total": total})
        for n, part in enumerate(parts, start=1)
    ]


def _group_len(units: list[NormUnit]) -> int:
    """The length of the merged ``text`` these units would form (blank-line joined)."""
    return len(BLOCK_SEPARATOR.join(_unit_text(unit) for unit in units))


def _flush_group(group: list[NormUnit], fields: dict[str, str], max_chars: int) -> list[Chunk]:
    """Emit the chunk(s) for one merge group of same-section sub-floor whole units.

    A group of one is a normal single whole chunk (byte-identical to the pre-merge output).
    A group of two or more becomes one merged chunk: its ``text`` joins the covered units'
    texts with a blank line, it keys on the FIRST covered unit, and ``unit``/``citation``
    list every covered unit (``part`` is ``null``).
    """
    if len(group) == 1:
        return _chunks_from_unit(group[0], fields, max_chars)
    first = group[0]
    text = BLOCK_SEPARATOR.join(_unit_text(unit) for unit in group)
    unit = ", ".join(unit.unit for unit in group)
    chunk = _build_chunk(
        fields, f"{fields[SLUG_KEY]}#{first.unit}", text, unit, first.section_path, None
    )
    return [chunk]


def _chunks_from_units(
    units: list[NormUnit], fields: dict[str, str], max_chars: int, merge_floor: int
) -> list[Chunk]:
    """Turn the ordered norm units into chunks, applying skip, split, and the merge pass.

    A unit is a **merge candidate** iff it is WHOLE (its ``text`` fits ``max_chars``, i.e. it
    was not split) AND shorter than ``merge_floor``. Candidates are gathered into one open
    group in document order; a unit **flushes** the group when it is empty-body (skipped, a
    boundary), split, an above-floor whole unit, or a candidate whose ``section_path`` differs
    from the group's. After a candidate joins, the group flushes once its combined ``text``
    reaches the floor; a candidate that would push the merged ``text`` over ``max_chars``
    flushes the group first (the max rule wins over the floor even when ``floor > max``).
    """
    chunks: list[Chunk] = []
    group: list[NormUnit] = []

    def flush() -> None:
        if group:
            chunks.extend(_flush_group(group, fields, max_chars))
            group.clear()

    for unit in units:
        if not unit.body:
            flush()  # empty-body (weggefallen) unit — a boundary, emits no chunk
            continue
        text = _unit_text(unit)
        is_candidate = len(text) <= max_chars and len(text) < merge_floor
        if not is_candidate:
            flush()  # a split or above-floor whole unit — a boundary, emitted on its own
            chunks.extend(_chunks_from_unit(unit, fields, max_chars))
            continue
        if group and unit.section_path != group[0].section_path:
            flush()  # a candidate under a different section — a boundary
        if group and _group_len([*group, unit]) > max_chars:
            flush()  # never push a merged chunk over the max
        group.append(unit)
        if _group_len(group) >= merge_floor:
            flush()  # the group has reached the floor — start fresh at the next candidate
    flush()
    return chunks


def chunk_corpus(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    merge_floor: int = DEFAULT_MERGE_FLOOR,
) -> list[Chunk]:
    """Parse one law's corpus Markdown into its chunk records.

    Emits one chunk per non-empty norm unit that fits ``max_chars``; a unit whose ``text``
    exceeds it is split into ordered parts (Absatz groups with one-Absatz overlap, or the
    recursive-character fallback, or a whole atomic table). Consecutive whole units below
    ``merge_floor`` that share a ``section_path`` are merged into one chunk. A unit whose body
    strips to the empty string (a ``(weggefallen)`` placeholder) is skipped and is a merge
    boundary. Raises ``ChunkError`` on a duplicate ``id`` within the law.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # the file's trailing newline is not a body line
    fields = parse_front_matter(lines)
    closing = lines.index("---", 1)

    units = parse_norm_units(lines[closing + 1 :])
    chunks: list[Chunk] = []
    seen_ids: set[str] = set()
    for chunk in _chunks_from_units(units, fields, max_chars, merge_floor):
        if chunk.id in seen_ids:
            raise ChunkError(f"duplicate chunk id within law: {chunk.id!r}")
        seen_ids.add(chunk.id)
        chunks.append(chunk)
    return chunks


def chunk_law(
    corpus_file: Path,
    chunks_dir: Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    merge_floor: int = DEFAULT_MERGE_FLOOR,
) -> Path:
    """Chunk one ``data/corpus/<slug>.md`` into ``chunks_dir/<slug>.jsonl``; returns the path.

    The output file is only written after the whole law parsed successfully.
    """
    chunks = chunk_corpus(corpus_file.read_text(encoding="utf-8"), max_chars, merge_floor)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    output = chunks_dir / f"{corpus_file.stem}.jsonl"
    body = "".join(json.dumps(asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks)
    output.write_text(body, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    """Chunk every corpus law; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.chunk",
        description="Chunk law Markdown from data/corpus/ into JSONL under data/chunks/.",
    )
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR, help="input directory")
    parser.add_argument("--chunks-dir", type=Path, default=CHUNKS_DIR, help="output directory")
    args = parser.parse_args(argv)

    corpus_files = sorted(args.corpus_dir.glob("*.md")) if args.corpus_dir.is_dir() else []
    if not corpus_files:
        print(f"no corpus in {args.corpus_dir} — run `make convert` first", file=sys.stderr)
        return 1

    failed: list[str] = []
    for corpus_file in corpus_files:
        try:
            output = chunk_law(corpus_file, args.chunks_dir)
        except (ChunkError, OSError) as error:
            print(f"✗ {corpus_file.stem}: {error}", file=sys.stderr)
            failed.append(corpus_file.stem)
        else:
            print(f"✓ {corpus_file.stem} → {output}")
    if failed:
        print(f"chunk failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
