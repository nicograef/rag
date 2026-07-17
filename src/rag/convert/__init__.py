"""Convert stage — turn a fetched Wikipedia extract into section-structured Markdown.

Reads each article's plain-text extract plus ``fetch.json`` from ``data/raw/<slug>/`` and
writes one Markdown file per article to ``data/corpus/<slug>.md``: YAML front matter with
provenance, an H1 from the article title, and the article's ``== section ==`` /
``=== subsection ===`` wiki headings translated to ATX headings (``##`` / ``###``). The lead
paragraphs (the text before the first heading) become an ``## Introduction`` section so they
are a chunkable unit like every other section. Non-prose apparatus sections (References,
External links, See also, …) are dropped so only article prose reaches chunking — a
licensing-and-quality act. The transform is deterministic: same extract, byte-identical output.

Stage contract: docs/stages/convert.md
Theory: docs/theory/corpus-and-parsing.md
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from rag import CORPUS_DIR, RAW_DIR, run_per_source

# The lead paragraphs (before the first == heading == ) carry no heading of their own; they
# become this synthetic level-2 section so they are chunkable like any other section.
LEAD_HEADING = "Introduction"

# Non-prose apparatus sections dropped from the corpus (matched case-insensitively on the
# level-2 heading text). Their subsections go with them. TextExtracts already flattens/empties
# most of these; dropping the rest keeps the corpus prose, matching the law-text precedent.
EXCLUDED_SECTIONS = frozenset(
    {
        "references",
        "notes",
        "footnotes",
        "citations",
        "sources",
        "bibliography",
        "works cited",
        "further reading",
        "external links",
        "see also",
    }
)

# A wiki heading line: 2+ balanced ``=`` around the title (e.g. ``== History ==``). The level
# is the number of ``=`` (== → ##, === → ###), matching Markdown ATX depth directly; a level
# past H6 (never valid wiki syntax) is rejected by the depth guard in render_markdown.
HEADING_RE = re.compile(r"^(={2,})\s*(.+?)\s*\1\s*$")

# Markdown allows heading levels 1 through 6 only.
MAX_HEADING_DEPTH = 6


class ConversionError(Exception):
    """Raised when an article's raw artifacts cannot be converted faithfully."""


@dataclass(frozen=True)
class Provenance:
    """The fetch stage's per-article record, read from ``data/raw/<slug>/fetch.json``."""

    slug: str
    source_title: str
    source_url: str
    fetched_at: str


def load_provenance(article_dir: Path) -> Provenance:
    """Read an article's ``fetch.json``; raises ``ConversionError`` if it is malformed."""
    fetch_json = article_dir / "fetch.json"
    try:
        record = json.loads(fetch_json.read_text(encoding="utf-8"))
        return Provenance(
            slug=record["slug"],
            source_title=record["source_title"],
            source_url=record["source_url"],
            fetched_at=record["fetched_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ConversionError(f"invalid {fetch_json}: {error!r}") from error


def convert_article(article_dir: Path, corpus_dir: Path) -> Path:
    """Convert one article's raw directory into ``corpus_dir/<slug>.md``; returns the path.

    The output file is only written after the whole article rendered successfully.
    """
    provenance = load_provenance(article_dir)
    extract_file = article_dir / "extract.txt"
    if not extract_file.is_file():
        raise ConversionError(f"missing {extract_file}")
    extract = extract_file.read_text(encoding="utf-8")
    markdown = render_markdown(extract, provenance)

    corpus_dir.mkdir(parents=True, exist_ok=True)
    output = corpus_dir / f"{provenance.slug}.md"
    output.write_text(markdown, encoding="utf-8")
    return output


@dataclass(frozen=True)
class Section:
    """One rendered section: its heading depth, title, and blank-line-separated body blocks."""

    depth: int
    title: str
    blocks: list[str]


def render_markdown(extract: str, provenance: Provenance) -> str:
    """Render a Wikipedia extract as the article's full Markdown document (pure)."""
    sections = _parse_sections(extract)
    if not sections:
        raise ConversionError(f"extract for {provenance.source_title!r} has no renderable content")

    front_matter = _front_matter(
        {
            "slug": provenance.slug,
            "source_title": provenance.source_title,
            "source_url": provenance.source_url,
            "fetched_at": provenance.fetched_at,
        }
    )
    blocks = [front_matter, f"# {provenance.source_title}"]

    skipping = False
    for section in sections:
        if section.depth == 2:
            skipping = section.title.lower() in EXCLUDED_SECTIONS
        if skipping:
            continue
        if section.depth > MAX_HEADING_DEPTH:
            raise ConversionError(
                f"heading depth {section.depth} exceeds H{MAX_HEADING_DEPTH} for {section.title!r}"
            )
        blocks.append(f"{'#' * section.depth} {section.title}")
        blocks.extend(section.blocks)
    return "\n\n".join(blocks) + "\n"


def _parse_sections(extract: str) -> list[Section]:
    """Split an extract into its ordered sections (the lead becomes ``## Introduction``)."""
    lines = extract.split("\n")
    headings = [
        (index, len(match.group(1)), match.group(2).strip())
        for index, line in enumerate(lines)
        if (match := HEADING_RE.match(line))
    ]

    sections: list[Section] = []
    first_heading = headings[0][0] if headings else len(lines)
    lead_blocks = _body_blocks(lines[:first_heading])
    if lead_blocks:
        sections.append(Section(depth=2, title=LEAD_HEADING, blocks=lead_blocks))

    for position, (line_index, depth, title) in enumerate(headings):
        body_end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        blocks = _body_blocks(lines[line_index + 1 : body_end])
        sections.append(Section(depth=depth, title=title, blocks=blocks))
    return sections


def _body_blocks(lines: list[str]) -> list[str]:
    """A section's lines → one block per non-blank line (blank lines are separators).

    TextExtracts puts every paragraph (and every flattened list item) on its own line, so a
    non-blank line is exactly one block; blank lines only pad around headings.
    """
    return [stripped for line in lines if (stripped := line.strip())]


def _front_matter(fields: dict[str, str]) -> str:
    """YAML front matter; every value double-quoted so no value is re-typed by YAML."""
    out = ["---"]
    for key, value in fields.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'{key}: "{escaped}"')
    out.append("---")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Convert every fetched article; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.convert",
        description="Convert fetched Wikipedia extracts from data/raw/ into Markdown under data/corpus/.",
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="input directory")
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR, help="output directory")
    args = parser.parse_args(argv)

    if not args.raw_dir.is_dir():
        print(f"no fetched articles in {args.raw_dir} — run `make fetch` first", file=sys.stderr)
        return 1

    jobs = [
        (
            article_dir.name,
            lambda article_dir=article_dir: f"→ {convert_article(article_dir, args.corpus_dir)}",
        )
        for article_dir in sorted(path for path in args.raw_dir.iterdir() if path.is_dir())
    ]
    return run_per_source("convert", jobs, (ConversionError, OSError))
