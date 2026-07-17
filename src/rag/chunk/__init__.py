"""Chunk stage — turn the corpus Markdown into structure-aware chunk records.

Reads each article's Markdown from ``data/corpus/<slug>.md`` (produced by the convert stage)
and writes one JSONL file per article to ``data/chunks/<slug>.jsonl``: one ``Chunk`` record
per Wikipedia **section** (a level-2 ``##`` heading and everything under it, including its
``###`` subsections). The corpus Markdown is parsed by hand exactly as convert *writes* it —
front matter, ATX headings, and blank-line-separated body blocks — with no YAML or Markdown
library. The transform is deterministic: same corpus input, byte-identical JSONL. Any
construct the chunker cannot place raises ``ChunkError`` instead of silently dropping text.

A section that fits ``max_chars`` becomes one whole chunk; an oversized section is split into
ordered parts — subsection groups (``###``) with one-segment overlap, and a recursive-character
fallback for a single overlong paragraph. A second pass merges consecutive sub-``merge_floor``
whole sections into one chunk. ``max_chars`` is pinned to keep every chunk within the embed
model's token window (see the chunk contract for the measured basis); the embed token-guard is
the hard backstop.

Stage contract: docs/stages/chunk.md
Theory: docs/theory/chunking.md
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from rag import CHUNKS_DIR, CORPUS_DIR, HEADING_SEPARATOR, run_per_source

# Front matter fields the chunk stage consumes (the rest — e.g. title — are ignored).
SLUG_KEY = "slug"
SOURCE_TITLE_KEY = "source_title"
SOURCE_URL_KEY = "source_url"
FETCHED_AT_KEY = "fetched_at"
REQUIRED_KEYS = (SLUG_KEY, SOURCE_TITLE_KEY, SOURCE_URL_KEY, FETCHED_AT_KEY)

# Default size policy. `max_chars` is pinned to keep each chunk within bge-small-en-v1.5's
# 512-token window. Measured over the fetched 20-club corpus with the model's own tokenizer,
# the densest chunk text runs ≈ 2.44 chars/token, so 1200 chars is ≤ ~492 tokens even in the
# worst case — a hard guarantee, not a hope (observed max across the corpus: 375 tokens). The
# embed token-guard is the backstop if a future article is denser still. See docs/stages/chunk.md,
# "Verification". A section whose text exceeds `max_chars` is split; whole sections below the
# merge floor are candidates to merge with neighbours.
DEFAULT_MAX_CHARS = 1200
DEFAULT_MERGE_FLOOR = 400

# The blank line that separates blocks (paragraphs / subheadings) in a body and rejoins them.
BLOCK_SEPARATOR = "\n\n"

# Ordered separators for the recursive character fallback: paragraph → line → sentence → word.
CHAR_SEPARATORS = (BLOCK_SEPARATOR, "\n", ". ", " ")

# A subsection opens at a block whose first line is a level-3-or-deeper ATX heading (`###`).
MIN_SUBHEADING_DEPTH = 3


class ChunkError(Exception):
    """Raised when an article's corpus Markdown cannot be chunked faithfully."""


@dataclass(frozen=True)
class Chunk:
    """One retrievable chunk record; serialized one-per-line to JSONL in field order."""

    id: str
    text: str
    slug: str
    source_title: str
    unit: str
    section_path: list[str]
    citation: str
    source_url: str
    fetched_at: str
    part: dict[str, int] | None


@dataclass(frozen=True)
class Section:
    """One article section: its heading, the material a chunk is built from, and its path."""

    unit: str
    heading: str
    section_path: list[str]
    body: str


def parse_front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Decode convert's ``key: "value"`` front matter; return the fields and the body-start index.

    The index is the first line after the closing ``---`` — the single source of the
    front-matter boundary, so callers never re-scan for it (scan-based backslash unescape).
    """
    if not lines or lines[0] != "---":
        raise ChunkError("missing front matter opening `---`")
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            missing = [key for key in REQUIRED_KEYS if key not in fields]
            if missing:
                raise ChunkError(f"front matter missing required key(s): {', '.join(missing)}")
            return fields, index + 1
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


def parse_sections(lines: list[str]) -> list[Section]:
    """Parse an article's body (after front matter) into its ordered level-2 sections.

    Each ``##`` heading starts a section whose body runs to the next ``##`` heading and
    includes any ``###`` subheadings and their text. The H1 article title is not a section;
    ``###``-and-deeper headings are section content, not section boundaries. The heading trail
    above a top-level section is only the article title, so ``section_path`` is empty.
    """
    headings = [
        (index, depth) for index, line in enumerate(lines) if (depth := _heading_depth(line))
    ]
    if not headings or headings[0][1] != 1:
        raise ChunkError("body does not start with an H1 article title")

    boundaries = [index for index, depth in headings if depth == 2]
    sections: list[Section] = []
    for position, line_index in enumerate(boundaries):
        heading = lines[line_index].strip()
        title = lines[line_index][2:].strip()
        body_end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
        body = _body_between(lines, line_index + 1, body_end)
        sections.append(Section(unit=title, heading=heading, section_path=[], body=body))
    return sections


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
    """One ordered piece of a split section's body.

    ``content`` is the *new* body text this part contributes; ``joiner`` is the string that
    reattaches it to the running reconstruction (``""`` for the first part, ``"\n\n"`` when
    the part starts a fresh segment group, ``""`` when it continues a char-split segment).
    ``overlap`` is the duplicated context repeated from the previous part (``""`` for the
    first part, the previous group's final segment for a segment-group part, or a trailing
    character window for a char-split part). ``text`` is what the chunk actually carries.

    Only ``text`` reaches the emitted chunk — ``content``, ``joiner``, and ``overlap`` exist
    so the tests can machine-check the no-silent-loss and overlap contracts via
    :func:`body_from_parts`.
    """

    text: str
    content: str
    joiner: str
    overlap: str


def _split_blocks(body: str) -> list[str]:
    """Split a section body into its blocks (maximal runs of non-blank lines)."""
    return [block for block in body.split(BLOCK_SEPARATOR) if block]


def _opens_subsection(block: str) -> bool:
    """True when a block's first line is a ``###``-or-deeper subheading."""
    depth = _heading_depth(block.split("\n", 1)[0])
    return depth is not None and depth >= MIN_SUBHEADING_DEPTH


def _group_segments(body: str) -> list[str]:
    """Group a section body's blocks into segments (each an ``\\n\\n``-joined block group).

    A new segment opens at a ``###`` subheading block; the paragraphs that follow it attach
    to that segment. Blocks before the first subheading (the section's own intro) form the
    first segment. A section with no subheading yields one segment holding every block, which
    the recursive-character fallback then splits by paragraph when it is oversized.
    """
    segments: list[list[str]] = []
    for block in _split_blocks(body):
        if _opens_subsection(block) or not segments:
            segments.append([block])
        else:
            segments[-1].append(block)
    return [BLOCK_SEPARATOR.join(blocks) for blocks in segments]


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
    result: list[str] = []
    for fragment in fragments:
        result.extend(_split_recursively(fragment, max_chars, rest))
    return result


def _char_split_segment(
    segment: str, heading: str, max_chars: int, leading_joiner: str
) -> list[SplitPart]:
    """Char-split one oversized segment into parts, each with a trailing-window overlap.

    The segment is fragmented by :func:`_split_recursively` (which preserves separators, so the
    fragments concatenate back to the segment); each non-first fragment repeats the last
    ``_char_overlap`` characters of the previous fragment so boundary context survives.
    ``leading_joiner`` reattaches the whole segment to the running body during reconstruction
    (``"\n\n"`` when segments precede it, ``""`` when it is the body's first segment); the
    continuation fragments join with ``""`` because the preserved separators already do so.
    """
    budget = max_chars - len(heading) - len(BLOCK_SEPARATOR)
    overlap_window = min(_char_overlap(max_chars), max(budget // 2, 0))
    fragments = _split_recursively(segment, max(budget - overlap_window, 1), CHAR_SEPARATORS)

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


def _split_body(body: str, heading: str, max_chars: int) -> list[SplitPart]:
    """Split an oversized section body into ordered parts (segment groups + char fallback).

    Greedily accumulates whole segments into a part while heading + one-segment overlap +
    content stays ``≤ max_chars``; each non-first group repeats the previous group's final
    segment as overlap (dropped when even the first content segment would not otherwise fit —
    the max invariant wins). A single segment that alone overflows falls back to a character
    split.
    """
    segments = _group_segments(body)
    parts: list[SplitPart] = []
    previous_segment = ""  # the final segment of the previous group, repeated as overlap
    index = 0
    while index < len(segments):
        heading_len = len(heading) + len(BLOCK_SEPARATOR)
        overlap = previous_segment
        prefix_len = heading_len + (len(overlap) + len(BLOCK_SEPARATOR) if overlap else 0)

        first = segments[index]
        if prefix_len + len(first) > max_chars:
            # One segment cannot share a part with the overlap: drop the overlap (max wins).
            overlap, prefix_len = "", heading_len
            if prefix_len + len(first) > max_chars:  # still overflows alone → recurse
                leading_joiner = BLOCK_SEPARATOR if parts else ""
                parts.extend(_char_split_segment(first, heading, max_chars, leading_joiner))
                previous_segment = first
                index += 1
                continue

        group: list[str] = []
        used = prefix_len
        while index < len(segments):
            nxt = segments[index]
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
        previous_segment = group[-1]
    return parts


def body_from_parts(parts: list[SplitPart]) -> str:
    """Reconstruct a split section's body from its parts (the no-silent-loss inverse).

    Each part contributes only its own ``content`` (never the duplicated ``overlap``),
    reattached via its ``joiner``; the result must equal the section's original body verbatim.
    """
    return "".join(part.joiner + part.content for part in parts)


def _section_text(section: Section) -> str:
    """The whole ``text`` of a section: its heading, then a blank line, then its body."""
    return f"{section.heading}{BLOCK_SEPARATOR}{section.body}" if section.body else section.heading


def _build_chunk(
    fields: dict[str, str],
    chunk_id: str,
    text: str,
    unit: str,
    section_path: list[str],
    part: dict[str, int] | None,
) -> Chunk:
    """Assemble one ``Chunk`` record, filling the provenance fields from the front matter."""
    source_title = fields[SOURCE_TITLE_KEY]
    return Chunk(
        id=chunk_id,
        text=text,
        slug=fields[SLUG_KEY],
        source_title=source_title,
        unit=unit,
        section_path=section_path,
        citation=f"{source_title}{HEADING_SEPARATOR}{unit}",
        source_url=fields[SOURCE_URL_KEY],
        fetched_at=fields[FETCHED_AT_KEY],
        part=part,
    )


def _chunks_from_section(section: Section, fields: dict[str, str], max_chars: int) -> list[Chunk]:
    """Build the ``Chunk``(s) for one section: one whole chunk, or ordered split parts."""
    slug = fields[SLUG_KEY]
    text = _section_text(section)

    def _chunk(chunk_id: str, chunk_text: str, part: dict[str, int] | None) -> Chunk:
        return _build_chunk(fields, chunk_id, chunk_text, section.unit, section.section_path, part)

    if len(text) <= max_chars or not section.body:
        return [_chunk(f"{slug}#{section.unit}", text, None)]

    parts = _split_body(section.body, section.heading, max_chars)
    total = len(parts)
    if total == 1:
        return [_chunk(f"{slug}#{section.unit}", parts[0].text, None)]
    return [
        _chunk(f"{slug}#{section.unit}#{n}", part.text, {"index": n, "total": total})
        for n, part in enumerate(parts, start=1)
    ]


def _group_len(sections: list[Section]) -> int:
    """The length of the merged ``text`` these sections would form (blank-line joined)."""
    return len(BLOCK_SEPARATOR.join(_section_text(section) for section in sections))


def _flush_group(group: list[Section], fields: dict[str, str], max_chars: int) -> list[Chunk]:
    """Emit the chunk(s) for one merge group of sub-floor whole sections.

    A group of one is a normal single whole chunk (byte-identical to the pre-merge output).
    A group of two or more becomes one merged chunk: its ``text`` joins the covered sections'
    texts with a blank line, it keys on the FIRST covered section, and ``unit``/``citation``
    list every covered section (``part`` is ``null``).
    """
    if len(group) == 1:
        return _chunks_from_section(group[0], fields, max_chars)
    first = group[0]
    text = BLOCK_SEPARATOR.join(_section_text(section) for section in group)
    unit = ", ".join(section.unit for section in group)
    chunk = _build_chunk(
        fields, f"{fields[SLUG_KEY]}#{first.unit}", text, unit, first.section_path, None
    )
    return [chunk]


def _chunks_from_sections(
    sections: list[Section], fields: dict[str, str], max_chars: int, merge_floor: int
) -> list[Chunk]:
    """Turn the ordered sections into chunks, applying skip, split, and the merge pass.

    A section is a **merge candidate** iff it is WHOLE (its ``text`` fits ``max_chars``, i.e.
    it was not split) AND shorter than ``merge_floor``. Candidates are gathered into one open
    group in document order; a section **flushes** the group when it is empty-body (skipped),
    split, or an above-floor whole section. After a candidate joins, the group flushes once
    its combined ``text`` reaches the floor; a candidate that would push the merged ``text``
    over ``max_chars`` flushes the group first (the max rule wins over the floor).
    """
    chunks: list[Chunk] = []
    group: list[Section] = []

    def flush() -> None:
        if group:
            chunks.extend(_flush_group(group, fields, max_chars))
            group.clear()

    for section in sections:
        if not section.body:
            flush()  # an empty section — a boundary, emits no chunk
            continue
        text = _section_text(section)
        is_candidate = len(text) <= max_chars and len(text) < merge_floor
        if not is_candidate:
            flush()  # a split or above-floor whole section — a boundary, emitted on its own
            chunks.extend(_chunks_from_section(section, fields, max_chars))
            continue
        if group and _group_len([*group, section]) > max_chars:
            flush()  # never push a merged chunk over the max
        group.append(section)
        if _group_len(group) >= merge_floor:
            flush()  # the group has reached the floor — start fresh at the next candidate
    flush()
    return chunks


def chunk_corpus(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    merge_floor: int = DEFAULT_MERGE_FLOOR,
) -> list[Chunk]:
    """Parse one article's corpus Markdown into its chunk records.

    Emits one chunk per non-empty section that fits ``max_chars``; a section whose ``text``
    exceeds it is split into ordered parts (subsection groups with one-segment overlap, or the
    recursive-character fallback). Consecutive whole sections below ``merge_floor`` are merged
    into one chunk. An empty section emits no chunk. Raises ``ChunkError`` on a duplicate
    ``id`` within the article.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # the file's trailing newline is not a body line
    fields, body_start = parse_front_matter(lines)

    sections = parse_sections(lines[body_start:])
    chunks: list[Chunk] = []
    seen_ids: set[str] = set()
    for chunk in _chunks_from_sections(sections, fields, max_chars, merge_floor):
        if chunk.id in seen_ids:
            raise ChunkError(f"duplicate chunk id within article: {chunk.id!r}")
        seen_ids.add(chunk.id)
        chunks.append(chunk)
    return chunks


def chunk_article(
    corpus_file: Path,
    chunks_dir: Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    merge_floor: int = DEFAULT_MERGE_FLOOR,
) -> Path:
    """Chunk one ``data/corpus/<slug>.md`` into ``chunks_dir/<slug>.jsonl``; returns the path.

    The output file is only written after the whole article parsed successfully.
    """
    chunks = chunk_corpus(corpus_file.read_text(encoding="utf-8"), max_chars, merge_floor)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    output = chunks_dir / f"{corpus_file.stem}.jsonl"
    body = "".join(json.dumps(asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks)
    output.write_text(body, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    """Chunk every corpus article; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.chunk",
        description="Chunk article Markdown from data/corpus/ into JSONL under data/chunks/.",
    )
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR, help="input directory")
    parser.add_argument("--chunks-dir", type=Path, default=CHUNKS_DIR, help="output directory")
    args = parser.parse_args(argv)

    corpus_files = sorted(args.corpus_dir.glob("*.md")) if args.corpus_dir.is_dir() else []
    if not corpus_files:
        print(f"no corpus in {args.corpus_dir} — run `make convert` first", file=sys.stderr)
        return 1

    jobs = [
        (
            corpus_file.stem,
            lambda corpus_file=corpus_file: f"→ {chunk_article(corpus_file, args.chunks_dir)}",
        )
        for corpus_file in corpus_files
    ]
    return run_per_source("chunk", jobs, (ChunkError, OSError))
