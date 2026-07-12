"""Convert stage — turn fetched law XML into structure-preserving Markdown.

Reads each law's GiI-Norm XML plus ``fetch.json`` from ``data/raw/<slug>/`` and writes
one Markdown file per law to ``data/corpus/<slug>.md``: YAML front matter with
provenance, an H1 from the law title, and one section per norm unit. Only normative
text is emitted — footnotes and editorial apparatus stay out per the corpus licensing
decision (docs/roadmap.md). The transform is deterministic: same input files,
byte-identical output. Anything the converter cannot render faithfully raises
``ConversionError`` instead of silently dropping content.

Stage contract: docs/stages/convert.md
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

# Footnote references: their markers are dropped per the licensing decision.
INLINE_IGNORED = {"FnR"}

# The XML's own table of contents; the only norm type convert skips.
TOC_ENBEZ = "Inhaltsübersicht"

LIST_MARKER = re.compile(r"\d+\.")


class ConversionError(Exception):
    """Raised when a law's raw artifacts cannot be converted faithfully."""


@dataclass(frozen=True)
class Provenance:
    """The fetch stage's per-law record, read from ``data/raw/<slug>/fetch.json``."""

    slug: str
    source_url: str
    fetched_at: str
    files: list[str]


def load_provenance(law_dir: Path) -> Provenance:
    """Read a law's ``fetch.json``; raises ``ConversionError`` if it is malformed."""
    fetch_json = law_dir / "fetch.json"
    try:
        record = json.loads(fetch_json.read_text(encoding="utf-8"))
        return Provenance(
            slug=record["slug"],
            source_url=record["source_url"],
            fetched_at=record["fetched_at"],
            files=record["files"],
        )
    except (json.JSONDecodeError, KeyError) as error:
        raise ConversionError(f"invalid {fetch_json}: {error!r}") from error


def convert_law(law_dir: Path, corpus_dir: Path) -> Path:
    """Convert one law's raw directory into ``corpus_dir/<slug>.md``; returns the path.

    Non-XML files from the archive are ignored (flagged on stdout). The output file is
    only written after the whole law rendered successfully.
    """
    provenance = load_provenance(law_dir)
    xml_names = [name for name in provenance.files if name.endswith(".xml")]
    for skipped in sorted(set(provenance.files) - set(xml_names)):
        print(f"  ignoring non-XML file: {skipped}")
    if len(xml_names) != 1:
        raise ConversionError(f"expected exactly one XML file, found {xml_names or 'none'}")

    root = etree.parse(str(law_dir / xml_names[0])).getroot()
    markdown = render_markdown(root, provenance)

    corpus_dir.mkdir(parents=True, exist_ok=True)
    output = corpus_dir / f"{provenance.slug}.md"
    output.write_text(markdown, encoding="utf-8")
    return output


def render_markdown(root: etree._Element, provenance: Provenance) -> str:
    """Render a ``<dokumente>`` tree as the law's full Markdown document (pure)."""
    norms = root.findall("norm")
    if not norms:
        raise ConversionError("no <norm> elements in document")
    header, *body_norms = norms
    if header.find("metadaten/enbez") is not None:
        raise ConversionError("first <norm> is not the law's header norm (it has an <enbez>)")

    builddate = root.get("builddate")
    if builddate is None:
        raise ConversionError("missing builddate attribute on <dokumente>")
    title = _law_title(header)
    front_matter = _front_matter(
        {
            "slug": provenance.slug,
            "abbreviation": _abbreviation(header),
            "title": title,
            "source_url": provenance.source_url,
            "fetched_at": provenance.fetched_at,
            "builddate": builddate,
        }
    )

    blocks = [front_matter, f"# {title}"]
    for norm in body_norms:
        blocks.extend(_render_norm(norm))
    return "\n\n".join(blocks) + "\n"


def _front_matter(fields: dict[str, str]) -> str:
    """YAML front matter; every value double-quoted so no value is re-typed by YAML."""
    lines = ["---"]
    for key, value in fields.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines)


def _law_title(header: etree._Element) -> str:
    """The law's official long title: the header norm's ``<langue>``."""
    langue = header.find("metadaten/langue")
    if langue is None:
        raise ConversionError("header norm has no <langue> title")
    return _inline_text(langue)


def _abbreviation(header: etree._Element) -> str:
    """Official abbreviation: ``<amtabk>`` if present, else the first ``<jurabk>``."""
    for tag in ("amtabk", "jurabk"):
        element = header.find(f"metadaten/{tag}")
        if element is not None:
            return _inline_text(element)
    raise ConversionError("header norm has neither <amtabk> nor <jurabk>")


def _render_norm(norm: etree._Element) -> list[str]:
    """One norm unit → heading block plus its body blocks (empty for skipped norms)."""
    if norm.find("metadaten/gliederungseinheit") is not None:
        raise ConversionError("section hierarchy (<gliederungseinheit>) not supported yet")
    enbez = norm.findtext("metadaten/enbez")
    if enbez is None:
        raise ConversionError("norm without <enbez> outside the header position")
    if enbez == TOC_ENBEZ:
        return []

    titel = norm.find("metadaten/titel")
    heading = enbez if titel is None else f"{enbez} — {_inline_text(titel)}"
    blocks = [f"## {heading}"]
    content = norm.find("textdaten/text/Content")
    if content is not None:
        blocks.extend(_render_content(content, enbez))
    return blocks


def _render_content(content: etree._Element, enbez: str) -> list[str]:
    """A norm's ``<Content>``: a sequence of ``<P>`` Absätze."""
    if _normalize(content.text or ""):
        raise ConversionError(f"stray text in <Content> of {enbez!r}")
    blocks: list[str] = []
    for child in content:
        if child.tag != "P":
            raise ConversionError(f"unsupported element <{child.tag}> in {enbez!r}")
        blocks.extend(_render_paragraph(child, enbez))
        if _normalize(child.tail or ""):
            raise ConversionError(f"stray text after <P> in {enbez!r}")
    return blocks


def _render_paragraph(paragraph: etree._Element, enbez: str) -> list[str]:
    """One ``<P>`` → text blocks, split where ``<DL>`` lists and ``<BR>`` breaks occur.

    The ``(1)``-style Absatz marker is part of the paragraph text and survives as-is.
    """
    blocks: list[str] = []
    buffer = paragraph.text or ""

    def flush() -> None:
        nonlocal buffer
        if text := _normalize(buffer):
            blocks.append(text)
        buffer = ""

    for child in paragraph:
        if child.tag == "DL":
            flush()
            blocks.append(_render_list(child, enbez))
        elif child.tag == "BR":
            flush()
        elif child.tag in INLINE_IGNORED:
            pass
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in a paragraph of {enbez!r}")
        buffer += child.tail or ""
    flush()
    return blocks


def _render_list(dl: etree._Element, enbez: str) -> str:
    """A ``<DL>`` enumeration → Markdown ordered list, keeping the source's markers."""
    items: list[str] = []
    marker: str | None = None
    for child in dl:
        if child.tag == "DT":
            marker = _inline_text(child)
            if not LIST_MARKER.fullmatch(marker):
                raise ConversionError(f"unsupported list marker {marker!r} in {enbez!r}")
        elif child.tag == "DD":
            if marker is None:
                raise ConversionError(f"<DD> without a preceding <DT> in {enbez!r}")
            items.append(f"{marker} {_definition_text(child, enbez)}")
            marker = None
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in a list of {enbez!r}")
    return "\n".join(items)


def _definition_text(dd: etree._Element, enbez: str) -> str:
    """A ``<DD>`` list item body: exactly one ``<LA>`` line of inline text."""
    if _normalize(dd.text or "") or [child.tag for child in dd] != ["LA"]:
        raise ConversionError(f"unsupported list item shape in {enbez!r}")
    return _inline_text(dd[0])


def _inline_text(element: etree._Element) -> str:
    """An element's text as one normalized line; only footnote-reference children allowed."""
    parts = [element.text or ""]
    for child in element:
        if child.tag not in INLINE_IGNORED:
            raise ConversionError(f"unsupported inline element <{child.tag}> in <{element.tag}>")
        parts.append(child.tail or "")
    return _normalize("".join(parts))


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return " ".join(text.split())


def main(argv: list[str] | None = None) -> int:
    """Convert every fetched law; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.convert",
        description="Convert fetched law XML from data/raw/ into Markdown under data/corpus/.",
    )
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="input directory")
    parser.add_argument(
        "--corpus-dir", type=Path, default=Path("data/corpus"), help="output directory"
    )
    args = parser.parse_args(argv)

    if not args.raw_dir.is_dir():
        print(f"no fetched laws in {args.raw_dir} — run `make fetch` first", file=sys.stderr)
        return 1

    failed: list[str] = []
    for law_dir in sorted(path for path in args.raw_dir.iterdir() if path.is_dir()):
        try:
            output = convert_law(law_dir, args.corpus_dir)
        except (ConversionError, OSError, etree.LxmlError) as error:
            print(f"✗ {law_dir.name}: {error}", file=sys.stderr)
            failed.append(law_dir.name)
        else:
            print(f"✓ {law_dir.name} → {output}")
    if failed:
        print(f"convert failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
