"""Contract tests for the chunk stage — golden-file driven, no network."""

import json
from pathlib import Path

import pytest

from rag.chunk import (
    ChunkError,
    NormUnit,
    SplitPart,
    _split_body,
    body_from_parts,
    chunk_law,
    parse_front_matter,
    parse_norm_units,
)

FIXTURES = Path(__file__).parent / "fixtures"

# The small max the split fixtures were pinned at (2000+ char German is not hand-authored).
SPLIT_MAX_CHARS = 300


def test_chunk_law_matches_golden_file(tmp_path: Path) -> None:
    output = chunk_law(FIXTURES / "corpus" / "kassensichv.md", tmp_path)

    assert output == tmp_path / "kassensichv.jsonl"
    assert output.read_bytes() == (FIXTURES / "chunks" / "kassensichv.jsonl").read_bytes()


def test_chunk_is_deterministic(tmp_path: Path) -> None:
    corpus_file = FIXTURES / "corpus" / "kassensichv.md"
    first = chunk_law(corpus_file, tmp_path / "one").read_bytes()
    second = chunk_law(corpus_file, tmp_path / "two").read_bytes()

    assert first == second


# Each split fixture exercises exactly one case at SPLIT_MAX_CHARS:
#   splitg  — a multi-Absatz oversized § → Absatz groups with a one-Absatz overlap;
#   absatzg — a single oversized Absatz  → recursive-character fallback with a char overlap;
#   tableg  — an oversized pipe table    → one atomic chunk over max, logged.
SPLIT_FIXTURES = ("splitg", "absatzg", "tableg")


@pytest.mark.parametrize("slug", SPLIT_FIXTURES)
def test_split_fixture_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = chunk_law(FIXTURES / "corpus" / f"{slug}.md", tmp_path, max_chars=SPLIT_MAX_CHARS)

    assert output.read_bytes() == (FIXTURES / "chunks" / f"{slug}.jsonl").read_bytes()


@pytest.mark.parametrize("slug", SPLIT_FIXTURES)
def test_split_is_deterministic(slug: str, tmp_path: Path) -> None:
    corpus_file = FIXTURES / "corpus" / f"{slug}.md"
    first = chunk_law(corpus_file, tmp_path / "one", max_chars=SPLIT_MAX_CHARS).read_bytes()
    second = chunk_law(corpus_file, tmp_path / "two", max_chars=SPLIT_MAX_CHARS).read_bytes()

    assert first == second


# The three real-shape law fixtures (real convert outputs) chunked at the DEFAULT thresholds
# (max 2000, floor 500) — they exercise the merge pass and full non-§ coverage:
#   strukturg — nested sections: §§ 1+2 merge; § 7 sits under a deeper section (own chunk);
#               Anlage 1 (path []) own chunk; § 3 / §§ 4 bis 6 (empty) skipped.
#   artg      — Art units + Präambel/Eingangsformel/Anhang EV: Eingangsformel+Präambel merge,
#               Art 3+Art 4 merge, Art 1 alone (the repealed Art 2 in between is a boundary).
#   tabelleng — § 1 alone (empty § 2 is a boundary), Anlage 1+Anlage 2 merge (two atomic
#               tables in one chunk), Anlage 3 above the floor stands alone.
LAW_FIXTURES = ("strukturg", "artg", "tabelleng")


@pytest.mark.parametrize("slug", LAW_FIXTURES)
def test_law_fixture_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = chunk_law(FIXTURES / "corpus" / f"{slug}.md", tmp_path)

    assert output.read_bytes() == (FIXTURES / "chunks" / f"{slug}.jsonl").read_bytes()


@pytest.mark.parametrize("slug", LAW_FIXTURES)
def test_law_fixture_is_deterministic(slug: str, tmp_path: Path) -> None:
    corpus_file = FIXTURES / "corpus" / f"{slug}.md"
    first = chunk_law(corpus_file, tmp_path / "one").read_bytes()
    second = chunk_law(corpus_file, tmp_path / "two").read_bytes()

    assert first == second


def _split_parts_for(slug: str, max_chars: int) -> tuple[NormUnit, list[SplitPart]]:
    """The ordered `SplitPart`s of a fixture's single oversized unit (test helper)."""
    lines = (FIXTURES / "corpus" / f"{slug}.md").read_text(encoding="utf-8").splitlines()
    parse_front_matter(lines)
    closing = lines.index("---", 1)
    (unit,) = [u for u in parse_norm_units(lines[closing + 1 :]) if u.body]
    return unit, _split_body(unit.body, unit.heading, unit.unit, max_chars)


@pytest.mark.parametrize("slug", ["splitg", "absatzg"])
def test_parts_minus_overlap_reconstruct_the_body(slug: str) -> None:
    # No-silent-loss (tightened): concatenating the parts' own content (the duplicated
    # overlap dropped) reproduces the unit's full body verbatim — for the Absatz-group case
    # (splitg) and the recursive-character case (absatzg).
    unit, parts = _split_parts_for(slug, SPLIT_MAX_CHARS)

    assert len(parts) > 1  # the fixture really did split
    assert body_from_parts(parts) == unit.body


def test_absatz_group_split_repeats_the_previous_final_absatz() -> None:
    _, parts = _split_parts_for("splitg", SPLIT_MAX_CHARS)

    # Every non-first part leads (after the heading) with the previous part's final Absatz.
    assert parts[1].overlap.startswith("(2) Der zweite Absatz")
    assert parts[2].overlap.startswith("(3) Der dritte Absatz")


def test_single_oversized_absatz_uses_a_character_overlap() -> None:
    _, parts = _split_parts_for("absatzg", SPLIT_MAX_CHARS)

    # The fallback overlaps a fixed char window (max_chars // 10 = 30), not a whole Absatz.
    assert len(parts[1].overlap) == SPLIT_MAX_CHARS // 10
    assert parts[1].text.split("\n\n", 1)[1].startswith(parts[1].overlap)


@pytest.mark.parametrize("slug", SPLIT_FIXTURES)
def test_no_chunk_exceeds_max_except_an_atomic_table(slug: str, tmp_path: Path) -> None:
    output = chunk_law(FIXTURES / "corpus" / f"{slug}.md", tmp_path, max_chars=SPLIT_MAX_CHARS)

    for line in output.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        body = record["text"].split("\n\n", 1)[1] if "\n\n" in record["text"] else ""
        is_atomic_table = body.startswith("|") or body.startswith("```table")
        if not is_atomic_table:
            assert len(record["text"]) <= SPLIT_MAX_CHARS


def test_unit_at_or_under_max_is_a_single_whole_chunk(tmp_path: Path) -> None:
    # KassenSichV's units are all under the default max, so none is split — every chunk is a
    # whole `part=null` chunk keyed on its unit alone (no `#<n>` suffix).
    output = chunk_law(FIXTURES / "corpus" / "kassensichv.md", tmp_path)

    for line in output.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        assert record["part"] is None
        assert "#" in record["id"] and record["id"].count("#") == 1


FRONT_MATTER = (
    "---\n"
    'slug: "somelaw"\n'
    'abbreviation: "SomeLaw"\n'
    'title: "Ein Gesetz"\n'
    'source_url: "https://example.org/somelaw/xml.zip"\n'
    'fetched_at: "2026-07-12T00:00:00+00:00"\n'
    'builddate: "20260101000000"\n'
    "---\n\n"
    "# Ein Gesetz\n"
)


def write_corpus(tmp_path: Path, body: str) -> Path:
    """A minimal corpus file: standard front matter, H1, then the given body Markdown."""
    corpus_file = tmp_path / "somelaw.md"
    corpus_file.write_text(FRONT_MATTER + body, encoding="utf-8")
    return corpus_file


def test_duplicate_unit_id_within_a_law_raises(tmp_path: Path) -> None:
    # `merge_floor=0` keeps the two same-enbez §§ from merging (which would mask the clash),
    # so the two whole `somelaw#§ 1` chunks collide and the guard fires.
    corpus_file = write_corpus(tmp_path, "\n## § 1\n\nErster Text.\n\n## § 1\n\nZweiter Text.\n")

    with pytest.raises(ChunkError, match="duplicate chunk id"):
        chunk_law(corpus_file, tmp_path / "chunks", merge_floor=0)


def test_section_with_a_non_empty_body_raises(tmp_path: Path) -> None:
    # `## Teil` is a section (its next heading `### § 1` is deeper) but carries body text.
    corpus_file = write_corpus(tmp_path, "\n## Teil\n\nText im Abschnitt.\n\n### § 1\n\nInhalt.\n")

    with pytest.raises(ChunkError, match="non-empty body"):
        chunk_law(corpus_file, tmp_path / "chunks")


def test_empty_body_unit_is_skipped(tmp_path: Path) -> None:
    corpus_file = write_corpus(tmp_path, "\n## § 1 — (weggefallen)\n\n## § 2\n\nInhalt.\n")

    output = chunk_law(corpus_file, tmp_path / "chunks")

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # only § 2 survives; the empty-body § 1 emits no chunk
    assert '"id": "somelaw#§ 2"' in lines[0]


def _records(output: Path) -> list[dict]:
    """The JSON records of a chunk output file, one per line (test helper)."""
    return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]


def test_consecutive_sub_floor_units_under_one_section_merge(tmp_path: Path) -> None:
    # Two short §§ under the same (flat) section: one merged chunk keyed on the first, its
    # `unit`/`citation` listing both, its text the two units joined by a blank line, part null.
    corpus_file = write_corpus(tmp_path, "\n## § 1\n\nErster.\n\n## § 2\n\nZweiter.\n")

    (record,) = _records(chunk_law(corpus_file, tmp_path / "chunks", merge_floor=500))

    assert record["id"] == "somelaw#§ 1"
    assert record["unit"] == "§ 1, § 2"
    assert record["citation"] == "§ 1, § 2 SomeLaw"
    assert record["text"] == "§ 1\n\nErster.\n\n§ 2\n\nZweiter."
    assert record["part"] is None


def test_a_section_boundary_blocks_the_merge(tmp_path: Path) -> None:
    # § 1 and § 2 sit under different sections (their `section_path` differs), so they do not
    # merge even though both are sub-floor — each stays its own single chunk.
    body = "\n## Teil A\n\n### § 1\n\nErster.\n\n## Teil B\n\n### § 2\n\nZweiter.\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_law(corpus_file, tmp_path / "chunks", merge_floor=500))

    assert [r["id"] for r in records] == ["somelaw#§ 1", "somelaw#§ 2"]
    assert all(r["unit"] in ("§ 1", "§ 2") for r in records)


def test_a_skipped_empty_unit_between_candidates_blocks_the_merge(tmp_path: Path) -> None:
    # The repealed § 2 between § 1 and § 3 is a boundary: units separated by a repealed norm
    # are not adjacent, so § 1 and § 3 each stay their own chunk (mirrors artg's Art 1 / Art 3).
    body = "\n## § 1\n\nErster.\n\n## § 2 — (weggefallen)\n\n## § 3\n\nDritter.\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_law(corpus_file, tmp_path / "chunks", merge_floor=500))

    assert [r["id"] for r in records] == ["somelaw#§ 1", "somelaw#§ 3"]


def test_an_above_floor_unit_is_never_merged(tmp_path: Path) -> None:
    # § 1 is a short candidate but § 2's text is at/above the floor, so § 2 is a boundary and
    # is never absorbed — § 1 flushes as its own single chunk and § 2 stands alone.
    long_body = "Ausführlicher Text. " * 5  # ~100 chars, over the small floor below
    body = f"\n## § 1\n\nKurz.\n\n## § 2\n\n{long_body}\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_law(corpus_file, tmp_path / "chunks", merge_floor=60))

    assert [r["id"] for r in records] == ["somelaw#§ 1", "somelaw#§ 2"]
    assert records[0]["unit"] == "§ 1"  # not merged into § 2
    assert records[1]["unit"] == "§ 2"


def test_a_merged_chunk_never_exceeds_the_max(tmp_path: Path) -> None:
    # Three sub-floor §§ that would together exceed a max SMALLER than the floor: the max rule
    # wins, so the group flushes before overflowing and no emitted chunk is over the max.
    unit = "Ein Satz mit etwas Text darin."  # ~30 chars of body per §
    body = f"\n## § 1\n\n{unit}\n\n## § 2\n\n{unit}\n\n## § 3\n\n{unit}\n"
    corpus_file = write_corpus(tmp_path, body)

    records = _records(chunk_law(corpus_file, tmp_path / "chunks", max_chars=80, merge_floor=500))

    assert all(len(r["text"]) <= 80 for r in records)
    assert len(records) > 1  # the max forced a flush rather than one over-max merged chunk


def test_front_matter_missing_a_consumed_key_raises(tmp_path: Path) -> None:
    # Front matter without `abbreviation` must fail loudly (ChunkError), not with a raw
    # KeyError that would escape main()'s per-law isolation.
    corpus_file = tmp_path / "somelaw.md"
    corpus_file.write_text(
        '---\nslug: "somelaw"\nsource_url: "https://example.org/x.zip"\n'
        'fetched_at: "2026-07-12T00:00:00+00:00"\n---\n\n# Ein Gesetz\n\n## § 1\n\nInhalt.\n',
        encoding="utf-8",
    )

    with pytest.raises(ChunkError, match="missing required key"):
        chunk_law(corpus_file, tmp_path / "chunks")


def test_main_isolates_a_failing_law_from_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from rag.chunk import main

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "good.md").write_text(FRONT_MATTER + "\n## § 1\n\nInhalt.\n", encoding="utf-8")
    (corpus_dir / "broken.md").write_text("no front matter here\n", encoding="utf-8")
    chunks_dir = tmp_path / "chunks"

    exit_code = main(["--corpus-dir", str(corpus_dir), "--chunks-dir", str(chunks_dir)])

    assert exit_code == 1
    assert (chunks_dir / "good.jsonl").exists()  # the healthy law still chunked
    assert not (chunks_dir / "broken.jsonl").exists()  # the failing law wrote nothing
    assert "broken" in capsys.readouterr().err


def test_main_without_corpus_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from rag.chunk import main

    exit_code = main(["--corpus-dir", str(tmp_path / "missing"), "--chunks-dir", str(tmp_path)])

    assert exit_code == 1
    assert "make convert" in capsys.readouterr().err
