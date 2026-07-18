"""Contract tests for the chunk stage — golden-file driven, no network."""

import json
from pathlib import Path

import pytest

from rag.chunk import (
    ChunkError,
    Section,
    SplitPart,
    _split_body,
    body_from_parts,
    chunk_article,
    main,
    parse_front_matter,
    parse_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"

# The real-shape article fixtures, chunked at the DEFAULT thresholds, exercise the full range:
#   brentford — whole sections plus a tail merge (History + Nickname);
#   fulham    — three sub-floor sections merged into one chunk;
#   citypark  — an oversized History split into ordered parts with a one-segment overlap, a
#               whole Introduction, and a Ground + Nickname merge.
GOLDEN_SLUGS = ["brentford", "fulham", "citypark"]


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_chunk_article_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = chunk_article(FIXTURES / "corpus" / f"{slug}.md", tmp_path)

    assert output == tmp_path / f"{slug}.jsonl"
    assert output.read_bytes() == (FIXTURES / "chunks" / f"{slug}.jsonl").read_bytes()


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_chunk_is_deterministic(slug: str, tmp_path: Path) -> None:
    corpus_file = FIXTURES / "corpus" / f"{slug}.md"
    first = chunk_article(corpus_file, tmp_path / "one").read_bytes()
    second = chunk_article(corpus_file, tmp_path / "two").read_bytes()

    assert first == second


def _records(output: Path) -> list[dict]:
    """The JSON records of a chunk output file, one per line (test helper)."""
    return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]


FRONT_MATTER = (
    "---\n"
    'slug: "club"\n'
    'source_title: "Club F.C."\n'
    'source_url: "https://en.wikipedia.org/wiki/Club_F.C."\n'
    'fetched_at: "2026-07-17T00:00:00+00:00"\n'
    "---\n\n"
    "# Club F.C.\n"
)


def write_corpus(tmp_path: Path, body: str) -> Path:
    """A minimal corpus file: standard front matter, H1, then the given body Markdown."""
    corpus_file = tmp_path / "club.md"
    corpus_file.write_text(FRONT_MATTER + body, encoding="utf-8")
    return corpus_file


# ── Splitting an oversized section ──


def _oversized_split(slug: str, max_chars: int) -> tuple[Section, list[SplitPart]]:
    """The single oversized section of a fixture and its ordered `SplitPart`s (test helper)."""
    lines = (FIXTURES / "corpus" / f"{slug}.md").read_text(encoding="utf-8").splitlines()
    _fields, body_start = parse_front_matter(lines)
    for section in parse_sections(lines[body_start:]):
        text = f"{section.heading}\n\n{section.body}" if section.body else section.heading
        if len(text) > max_chars and section.body:
            return section, _split_body(section.body, section.heading, max_chars)
    raise AssertionError(f"{slug} has no oversized section at max_chars={max_chars}")


def test_parts_minus_overlap_reconstruct_the_section_body() -> None:
    # No-silent-loss (tightened): concatenating the parts' own content (the duplicated overlap
    # dropped) reproduces the section's full body verbatim.
    section, parts = _oversized_split("citypark", 1200)

    assert len(parts) > 1  # the fixture really did split
    assert body_from_parts(parts) == section.body


def test_a_subsection_group_split_repeats_the_previous_final_segment() -> None:
    _, parts = _oversized_split("citypark", 1200)

    # A non-first part leads (after the section heading) with the previous group's final
    # segment — here the whole `### Decline` subsection — as overlap.
    assert parts[1].overlap.startswith("### Decline")
    assert parts[1].text.split("\n\n", 1)[1].startswith(parts[1].overlap)


def test_a_single_oversized_paragraph_uses_a_character_overlap() -> None:
    # A section that is one long paragraph (no subsections, no blank lines) falls through to
    # the recursive-character split, whose overlap is a fixed char window, not a whole segment.
    body = "word " * 200  # ~1000 chars, a single paragraph
    parts = _split_body(body, "## History", 300)

    assert len(parts) > 1
    assert body_from_parts(parts) == body
    assert len(parts[1].overlap) == 300 // 10  # the char window, not a segment
    assert parts[1].text.split("\n\n", 1)[1].startswith(parts[1].overlap)


def test_no_chunk_exceeds_the_max(tmp_path: Path) -> None:
    output = chunk_article(FIXTURES / "corpus" / "citypark.md", tmp_path)

    for record in _records(output):
        assert len(record["text"]) <= 1200


def test_a_section_under_the_max_is_a_single_whole_chunk(tmp_path: Path) -> None:
    # brentford's sections are all under the default max, so none is split — every chunk is a
    # whole `part=null` chunk keyed on its section alone (one `#` in the id).
    output = chunk_article(FIXTURES / "corpus" / "brentford.md", tmp_path)

    for record in _records(output):
        assert record["part"] is None
        assert record["id"].count("#") == 1


def test_split_part_ids_and_citation_are_section_scoped(tmp_path: Path) -> None:
    output = chunk_article(FIXTURES / "corpus" / "citypark.md", tmp_path)
    parts = [r for r in _records(output) if r["part"] is not None]

    assert parts, "citypark should have split parts"
    for record in parts:
        assert record["section"] == "History"
        assert record["citation"] == "City Park F.C. — History"
        assert record["id"].startswith("citypark#History#")
        assert record["section_path"] == []


# ── The merge pass ──


def test_consecutive_sub_floor_sections_merge(tmp_path: Path) -> None:
    # Two short sections: one merged chunk keyed on the first, its `section`/`citation` listing
    # both, its text the two sections joined by a blank line, part null.
    corpus_file = write_corpus(
        tmp_path, "\n## Nickname\n\nThe Bees.\n\n## Mascot\n\nBuzz the bee.\n"
    )

    (record,) = _records(chunk_article(corpus_file, tmp_path / "chunks", merge_floor=500))

    assert record["id"] == "club#Nickname"
    assert record["section"] == "Nickname, Mascot"
    assert record["citation"] == "Club F.C. — Nickname, Mascot"
    assert record["text"] == "## Nickname\n\nThe Bees.\n\n## Mascot\n\nBuzz the bee."
    assert record["part"] is None


def test_an_above_floor_section_is_never_merged(tmp_path: Path) -> None:
    # Nickname is a short candidate but History's text is above the floor, so History is a
    # boundary and is never absorbed — each stays its own chunk.
    long_body = "A sentence with several words in it. " * 5  # well over the small floor below
    body = f"\n## Nickname\n\nShort.\n\n## History\n\n{long_body}\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_article(corpus_file, tmp_path / "chunks", merge_floor=60))

    assert [record["section"] for record in records] == ["Nickname", "History"]


def test_an_empty_section_between_candidates_blocks_the_merge(tmp_path: Path) -> None:
    # An empty section is a boundary: candidates on either side are not adjacent, so they each
    # stay their own chunk.
    body = "\n## Nickname\n\nThe Bees.\n\n## Empty\n\n## Mascot\n\nBuzz.\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_article(corpus_file, tmp_path / "chunks", merge_floor=500))

    assert [record["section"] for record in records] == ["Nickname", "Mascot"]


def test_a_merged_chunk_never_exceeds_the_max(tmp_path: Path) -> None:
    # Three sub-floor sections that would together exceed a max SMALLER than the floor: the
    # max rule wins, so the group flushes before overflowing.
    body = "\n## A\n\nSome text here.\n\n## B\n\nSome text here.\n\n## C\n\nSome text here.\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(
        chunk_article(corpus_file, tmp_path / "chunks", max_chars=60, merge_floor=500)
    )

    assert all(len(record["text"]) <= 60 for record in records)
    assert len(records) > 1  # the max forced a flush rather than one over-max merged chunk


def test_an_empty_section_is_skipped(tmp_path: Path) -> None:
    corpus_file = write_corpus(tmp_path, "\n## Empty\n\n## Nickname\n\nThe Bees.\n")

    (record,) = _records(chunk_article(corpus_file, tmp_path / "chunks"))

    assert record["id"] == "club#Nickname"  # the empty section emits no chunk


# ── Guard tests: corpus Markdown the chunker cannot parse fails loudly ──


def test_duplicate_section_id_within_an_article_raises(tmp_path: Path) -> None:
    # `merge_floor=0` keeps the two same-title sections from merging (which would mask the
    # clash), so the two whole `club#History` chunks collide and the guard fires.
    corpus_file = write_corpus(tmp_path, "\n## History\n\nFirst.\n\n## History\n\nSecond.\n")

    with pytest.raises(ChunkError, match="duplicate chunk id"):
        chunk_article(corpus_file, tmp_path / "chunks", merge_floor=0)


def test_front_matter_missing_a_consumed_key_raises(tmp_path: Path) -> None:
    # Front matter without `source_title` must fail loudly (ChunkError), not with a raw
    # KeyError that would escape main()'s per-source isolation.
    corpus_file = tmp_path / "club.md"
    corpus_file.write_text(
        '---\nslug: "club"\nsource_url: "https://en.wikipedia.org/wiki/Club"\n'
        'fetched_at: "2026-07-17T00:00:00+00:00"\n---\n\n# Club\n\n## History\n\nText.\n',
        encoding="utf-8",
    )

    with pytest.raises(ChunkError, match="missing required key"):
        chunk_article(corpus_file, tmp_path / "chunks")


def test_a_malformed_front_matter_line_raises() -> None:
    with pytest.raises(ChunkError, match="malformed front matter line"):
        parse_front_matter(["---", "slug: club", "---"])


def test_a_dangling_backslash_in_a_front_matter_value_raises() -> None:
    with pytest.raises(ChunkError, match="dangling backslash"):
        parse_front_matter(["---", 'source_title: "bad\\"', "---"])


def test_front_matter_without_a_closing_marker_raises() -> None:
    with pytest.raises(ChunkError, match="missing front matter closing"):
        parse_front_matter(["---", 'slug: "club"'])


def test_a_body_not_starting_with_an_h1_raises() -> None:
    with pytest.raises(ChunkError, match="does not start with an H1"):
        parse_sections(["## History", "", "Content."])


def test_main_isolates_a_failing_article_from_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "good.md").write_text(FRONT_MATTER + "\n## History\n\nText.\n", encoding="utf-8")
    (corpus_dir / "broken.md").write_text("no front matter here\n", encoding="utf-8")
    chunks_dir = tmp_path / "chunks"

    exit_code = main(["--corpus-dir", str(corpus_dir), "--chunks-dir", str(chunks_dir)])

    assert exit_code == 1
    assert (chunks_dir / "good.jsonl").exists()  # the healthy article still chunked
    assert not (chunks_dir / "broken.jsonl").exists()  # the failing article wrote nothing
    assert "broken" in capsys.readouterr().err


def test_main_without_corpus_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--corpus-dir", str(tmp_path / "missing"), "--chunks-dir", str(tmp_path)])

    assert exit_code == 1
    assert "make convert" in capsys.readouterr().err
