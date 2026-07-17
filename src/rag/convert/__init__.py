"""Convert stage — turn fetched law XML into structure-preserving Markdown.

Reads each law's GiI-Norm XML plus ``fetch.json`` from ``data/raw/<slug>/`` and writes
one Markdown file per law to ``data/corpus/<slug>.md``: YAML front matter with
provenance, an H1 from the law title, section headings from the law's
``<gliederungseinheit>`` hierarchy, and one heading per norm unit. Only normative text
is emitted — footnotes and editorial apparatus stay out per the corpus licensing
decision (docs/roadmap.md). The transform is deterministic: same input files,
byte-identical output. Anything the converter cannot render faithfully raises
``ConversionError`` instead of silently dropping content.

Stage contract: docs/stages/convert.md
Theory: docs/theory/corpus-and-parsing.md
"""

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

# Footnote references: their markers are dropped per the licensing decision.
INLINE_IGNORED = {"FnR"}

# The XML's own table of contents; the only norm type convert skips.
TOC_ENBEZ = "Inhaltsübersicht"

# Norm units introduced by these prefixes sit outside the section hierarchy.
APPENDIX_PREFIXES = ("Anlage", "Anhang")

# Markdown allows heading levels 1 through 6 only.
MAX_HEADING_DEPTH = 6


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
    except (json.JSONDecodeError, KeyError, TypeError) as error:
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
    section_depth = 1
    for norm in body_norms:
        rendered, section_depth = _render_norm(norm, section_depth)
        blocks.extend(rendered)
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


def _render_norm(norm: etree._Element, section_depth: int) -> tuple[list[str], int]:
    """One norm → its blocks plus the section depth in force after it.

    Section norms (``<gliederungseinheit>``) emit a heading and update the running
    depth; norm units nest one level below the current section; appendix units reset
    the section depth to 1 and render at depth 2.
    """
    if norm.find("metadaten/gliederungseinheit") is not None:
        return _render_section(norm), _section_depth(norm)

    enbez = norm.findtext("metadaten/enbez")
    if enbez is None:
        raise ConversionError("norm without <enbez> outside the header position")
    if enbez == TOC_ENBEZ:
        return [], section_depth

    if enbez.startswith(APPENDIX_PREFIXES):
        depth, section_depth = 2, 1
    else:
        depth = section_depth + 1
    if depth > MAX_HEADING_DEPTH:
        raise ConversionError(f"heading depth {depth} exceeds H{MAX_HEADING_DEPTH} for {enbez!r}")

    titel = norm.find("metadaten/titel")
    heading = enbez if titel is None else f"{enbez} — {_inline_text(titel)}"
    blocks = [f"{'#' * depth} {heading}"]
    content = norm.find("textdaten/text/Content")
    if content is not None:
        blocks.extend(_render_content(content, enbez))
    return blocks, section_depth


def _section_depth(norm: etree._Element) -> int:
    """Heading depth of a section norm from its ``gliederungskennzahl`` length."""
    kennzahl = norm.findtext("metadaten/gliederungseinheit/gliederungskennzahl") or ""
    if not kennzahl or len(kennzahl) % 3 != 0:
        raise ConversionError(f"invalid gliederungskennzahl {kennzahl!r}")
    depth = 1 + len(kennzahl) // 3
    if depth > MAX_HEADING_DEPTH:
        raise ConversionError(f"section depth {depth} exceeds H{MAX_HEADING_DEPTH}")
    return depth


def _render_section(norm: etree._Element) -> list[str]:
    """A ``<gliederungseinheit>`` section → one heading; its body must render empty."""
    if norm.find("metadaten/enbez") is not None:
        raise ConversionError("section norm unexpectedly has an <enbez>")
    ge = norm.find("metadaten/gliederungseinheit")
    if ge is None:
        raise ConversionError("section norm has no <gliederungseinheit>")
    bez = ge.findtext("gliederungsbez")
    if bez is None:
        raise ConversionError("section norm has no <gliederungsbez>")
    titel = ge.findtext("gliederungstitel")
    bez = _normalize(bez)
    heading = bez if titel is None else f"{bez} — {_normalize(titel)}"

    content = norm.find("textdaten/text/Content")
    if content is not None and _render_content(content, heading):
        raise ConversionError(f"section norm {heading!r} has a non-empty body")
    return [f"{'#' * _section_depth(norm)} {heading}"]


def _render_content(content: etree._Element, context: str) -> list[str]:
    """A norm's ``<Content>``: paragraphs, template sub-headings, and block separators."""
    if _normalize(content.text or ""):
        raise ConversionError(f"stray text in <Content> of {context!r}")
    blocks: list[str] = []
    for child in content:
        if child.tag == "P":
            blocks.extend(_render_paragraph(child, context))
        elif child.tag == "Title":
            blocks.append(f"**{_inline_text(child)}**")
        elif child.tag == "BR":
            pass
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in {context!r}")
        if _normalize(child.tail or ""):
            raise ConversionError(f"stray text after <{child.tag}> in {context!r}")
    return blocks


def _render_paragraph(paragraph: etree._Element, context: str) -> list[str]:
    """One ``<P>`` → inline text blocks interleaved with list and table blocks.

    Inline children fold into a running buffer; block children (``DL``, ``table``,
    ``noindex``) flush it first. The ``(1)``-style Absatz marker is plain text.
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
            blocks.append("\n".join(_render_list(child, context)))
        elif child.tag == "table":
            flush()
            blocks.append(_render_table(child, context))
        elif child.tag == "noindex":
            flush()
            blocks.extend(_render_noindex(child, context))
        elif child.tag == "BR":
            flush()
        elif child.tag in INLINE_IGNORED:
            pass
        elif child.tag in ("SP", "NB"):
            buffer += _inline_text(child)
        elif child.tag == "B":
            buffer += f"**{_inline_text(child)}**"
        else:
            raise ConversionError(
                f"unsupported element <{child.tag}> in a paragraph of {context!r}"
            )
        buffer += child.tail or ""
    flush()
    return blocks


def _render_noindex(noindex: etree._Element, context: str) -> list[str]:
    """A ``<noindex>`` wrapper: render its ``<P>`` children; drop ``<kommentar>``."""
    if _normalize(noindex.text or ""):
        raise ConversionError(f"stray text in <noindex> of {context!r}")
    blocks: list[str] = []
    for child in noindex:
        if child.tag == "P":
            blocks.extend(_render_paragraph(child, context))
        elif child.tag == "kommentar":
            pass
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in <noindex> of {context!r}")
        if _normalize(child.tail or ""):
            raise ConversionError(f"stray text after <{child.tag}> in <noindex> of {context!r}")
    return blocks


def _list_items(dl: etree._Element, context: str) -> Iterator[tuple[str, etree._Element]]:
    """Pair each ``<DT>`` marker with its ``<DD>`` item — the one place the DL grammar lives."""
    marker: str | None = None
    for child in dl:
        if child.tag == "DT":
            marker = _list_marker(child, context)
        elif child.tag == "DD":
            if marker is None:
                raise ConversionError(f"<DD> without a preceding <DT> in {context!r}")
            yield marker, child
            marker = None
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in a list of {context!r}")


def _render_list(dl: etree._Element, context: str) -> list[str]:
    """A ``<DL>`` enumeration → plain-text lines, nested lists indented 4 spaces."""
    lines: list[str] = []
    for marker, dd in _list_items(dl, context):
        lines.extend(_render_item(dd, marker, context))
    return lines


def _list_marker(dt: etree._Element, context: str) -> str:
    """A ``<DT>`` marker: its inline text, verbatim (markers are free-form source)."""
    if len(dt) and any(child.tag not in INLINE_IGNORED for child in dt):
        raise ConversionError(f"unsupported child in <DT> of {context!r}")
    return _inline_text(dt)


def _render_item(dd: etree._Element, marker: str, context: str) -> list[str]:
    """A ``<DD>`` item → its lines: marker on the first, the rest indented 4 spaces."""
    content_lines = _item_content_lines(dd, context)
    if not marker:
        return content_lines

    first, *rest = content_lines
    lines = [marker] if first == "" else [f"{marker} {first}"]
    lines.extend("    " + line for line in rest)
    return lines


def _item_content_lines(dd: etree._Element, context: str) -> list[str]:
    """A ``<DD>``'s ``<LA>`` runs → content lines (a leading empty line = starts with a list)."""
    if _normalize(dd.text or ""):
        raise ConversionError(f"stray text in <DD> of {context!r}")
    lines: list[str] = []
    first = True
    for child in dd:
        if child.tag != "LA":
            raise ConversionError(f"unsupported element <{child.tag}> in a <DD> of {context!r}")
        lines.extend(_la_lines(child, context, first))
        first = False
        if _normalize(child.tail or ""):
            raise ConversionError(f"stray text after <LA> in {context!r}")
    return lines or [""]


def _la_lines(la: etree._Element, context: str, first: bool) -> list[str]:
    """One ``<LA>`` → its text and nested-list lines; the first line may be empty."""
    lines: list[str] = []
    buffer = la.text or ""

    def flush() -> None:
        nonlocal buffer
        if text := _normalize(buffer):
            lines.append(text)
        buffer = ""

    for child in la:
        if child.tag == "DL":
            flush()
            lines.extend("    " + line for line in _render_list(child, context))
        elif child.tag in INLINE_IGNORED:
            pass
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in a <LA> of {context!r}")
        buffer += child.tail or ""
    flush()
    if first and lines and not _normalize(la.text or ""):
        return [""] + lines
    return lines or [""]


def _render_table(table: etree._Element, context: str) -> str:
    """A CALS ``<table>`` → a Markdown pipe table, or a fenced block when irregular."""
    groups = table.findall("tgroup")
    if len(groups) != 1:
        raise ConversionError(f"expected exactly one <tgroup> in a table of {context!r}")
    tgroup = groups[0]
    cols_attr = tgroup.get("cols")
    if cols_attr is None or not cols_attr.isdigit():
        raise ConversionError(f"table without a valid cols count in {context!r}")
    cols = int(cols_attr)

    theads = tgroup.findall("thead")
    if len(theads) > 1:
        raise ConversionError(f"more than one <thead> in a table of {context!r}")
    head_rows = theads[0].findall("row") if theads else []
    if len(head_rows) > 1:
        raise ConversionError(f"<thead> with more than one row in a table of {context!r}")
    bodies = tgroup.findall("tbody")
    if len(bodies) != 1:
        raise ConversionError(f"expected exactly one <tbody> in a table of {context!r}")
    body_rows = bodies[0].findall("row")

    if _is_regular(head_rows + body_rows, cols):
        return _render_regular_table(head_rows, body_rows, cols, context)
    return _render_fenced_table(head_rows + body_rows, context)


def _is_regular(rows: list[etree._Element], cols: int) -> bool:
    """True when every row has exactly ``cols`` cells and no cell spans."""
    for row in rows:
        entries = row.findall("entry")
        if len(entries) != cols:
            return False
        if any(entry.get(attr) for entry in entries for attr in ("morerows", "namest", "nameend")):
            return False
    return True


def _render_regular_table(
    head_rows: list[etree._Element], body_rows: list[etree._Element], cols: int, context: str
) -> str:
    """A regular CALS table → a Markdown pipe table (empty header when no ``<thead>``)."""
    header = _flatten_row(head_rows[0], context) if head_rows else [""] * cols
    lines = [_pipe_row(header), _pipe_row(["---"] * cols)]
    lines.extend(_pipe_row(_flatten_row(row, context)) for row in body_rows)
    return "\n".join(lines)


def _pipe_row(cells: list[str]) -> str:
    """A Markdown table row, pipes in cell text escaped."""
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def _render_fenced_table(rows: list[etree._Element], context: str) -> str:
    """An irregular CALS table → a fenced ``table`` block preserving the source layout."""
    lines = ["```table"]
    lines.extend(" | ".join(_flatten_row(row, context)) for row in rows)
    lines.append("```")
    return "\n".join(lines)


def _flatten_row(row: etree._Element, context: str) -> list[str]:
    """A ``<row>`` → one flattened line per ``<entry>``."""
    return [_flatten_cell(entry, context) for entry in row.findall("entry")]


def _flatten_cell(entry: etree._Element, context: str) -> str:
    """A table ``<entry>`` → one whitespace-normalized line."""
    parts = [entry.text or ""]
    for child in entry:
        if child.tag == "BR":
            parts.append(" ")
        elif child.tag == "B":
            parts.append(f"**{_inline_text(child)}**")
        elif child.tag == "NB":
            parts.append(_inline_text(child))
        elif child.tag == "DL":
            parts.append(" " + " ".join(_flatten_list(child, context)) + " ")
        elif child.tag in INLINE_IGNORED:
            pass
        else:
            raise ConversionError(
                f"unsupported element <{child.tag}> in a table cell of {context!r}"
            )
        parts.append(child.tail or "")
    return _normalize("".join(parts))


def _flatten_list(dl: etree._Element, context: str) -> list[str]:
    """A ``<DL>`` inside a cell → ``marker text`` fragments (recursing into nested lists)."""
    fragments: list[str] = []
    for marker, dd in _list_items(dl, context):
        fragments.append(_normalize(f"{marker} {_flatten_dd(dd, context)}"))
    return fragments


def _flatten_dd(dd: etree._Element, context: str) -> str:
    """A ``<DD>`` inside a cell → its ``<LA>`` runs and nested lists as one line."""
    parts: list[str] = []
    for child in dd:
        if child.tag != "LA":
            raise ConversionError(f"unsupported element <{child.tag}> in a <DD> of {context!r}")
        parts.append(_flatten_la(child, context))
    return " ".join(part for part in parts if part)


def _flatten_la(la: etree._Element, context: str) -> str:
    """A ``<LA>`` inside a cell → its text and nested-list fragments as one line."""
    parts = [la.text or ""]
    for child in la:
        if child.tag == "DL":
            parts.append(" " + " ".join(_flatten_list(child, context)) + " ")
        elif child.tag in INLINE_IGNORED:
            pass
        else:
            raise ConversionError(f"unsupported element <{child.tag}> in a <LA> of {context!r}")
        parts.append(child.tail or "")
    return _normalize("".join(parts))


def _inline_text(element: etree._Element) -> str:
    """An element's text as one normalized line; only footnote-reference children allowed.

    ``<BR/>`` in inline context becomes a single space; footnote markers drop.
    """
    parts = [element.text or ""]
    for child in element:
        if child.tag == "BR":
            parts.append(" ")
        elif child.tag not in INLINE_IGNORED:
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
