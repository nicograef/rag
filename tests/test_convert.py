"""Contract tests for the convert stage — golden-file driven, no network.

convert turns a fetched Wikipedia extract into section-structured Markdown. The golden files
under ``tests/fixtures/corpus/`` are committed artifacts (regenerate them deliberately, never
on the fly) pinned against the truncated-article extract fixtures under ``tests/fixtures/raw/``.
"""

import json
from pathlib import Path

import pytest

from rag.chunk import parse_front_matter
from rag.convert import (
    ConversionError,
    Provenance,
    convert_article,
    load_provenance,
    main,
    render_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLES = ["brentford", "fulham"]

PROV = Provenance(
    slug="x",
    source_title="X F.C.",
    source_url="https://en.wikipedia.org/wiki/X",
    fetched_at="2026-07-17T00:00:00+00:00",
)


@pytest.mark.parametrize("slug", ARTICLES)
def test_convert_article_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = convert_article(FIXTURES / "raw" / slug, tmp_path)

    assert output == tmp_path / f"{slug}.md"
    assert output.read_bytes() == (FIXTURES / "corpus" / f"{slug}.md").read_bytes()


@pytest.mark.parametrize("slug", ARTICLES)
def test_convert_is_deterministic(slug: str, tmp_path: Path) -> None:
    article_dir = FIXTURES / "raw" / slug
    first = convert_article(article_dir, tmp_path / "one").read_bytes()
    second = convert_article(article_dir, tmp_path / "two").read_bytes()

    assert first == second


def test_lead_paragraphs_become_an_introduction_section() -> None:
    md = render_markdown("Lead one.\nLead two.\n\n== History ==\nText.", PROV)

    assert "# X F.C." in md
    assert "## Introduction\n\nLead one.\n\nLead two." in md


def test_wiki_headings_translate_to_atx_by_level() -> None:
    md = render_markdown("Lead.\n\n== History ==\n\n=== Early ===\nText.", PROV)
    body = md.split("---", 2)[2]

    assert "## History" in md
    assert "### Early" in md
    assert "==" not in body  # no wiki heading markers survive


def test_excluded_apparatus_sections_are_dropped() -> None:
    extract = (
        "Lead.\n\n== Career ==\nProse.\n\n== See also ==\nLink\n\n"
        "== References ==\n\n== External links ==\nSite"
    )
    md = render_markdown(extract, PROV)

    assert "## Career\n\nProse." in md
    for dropped in ("See also", "References", "External links", "Link", "Site"):
        assert dropped not in md


def test_an_excluded_section_drops_its_subsections_but_a_later_section_resumes() -> None:
    extract = "Lead.\n\n== References ==\n=== Works cited ===\nA book.\n\n== Honours ==\nA cup."
    md = render_markdown(extract, PROV)

    assert "Works cited" not in md and "A book." not in md
    assert "## Honours\n\nA cup." in md


def test_front_matter_escaping_round_trips_to_the_chunk_reader() -> None:
    # A title carrying both a `"` and a `\` must survive convert's front-matter escaping and
    # the chunk stage's front-matter reader byte-for-byte.
    prov = Provenance(slug="x", source_title='A"B\\C', source_url="u", fetched_at="t")
    md = render_markdown("Lead.\n\n== H ==\nText.", prov)

    fields, _ = parse_front_matter(md.rstrip("\n").split("\n"))

    assert fields["source_title"] == 'A"B\\C'


def test_an_extract_with_no_content_is_an_error() -> None:
    with pytest.raises(ConversionError, match="no renderable content"):
        render_markdown("   \n\n  ", PROV)


def test_a_heading_deeper_than_h6_is_an_error() -> None:
    with pytest.raises(ConversionError, match="exceeds H6"):
        render_markdown("Lead.\n\n======= Too deep =======\nText.", PROV)


def test_malformed_fetch_json_is_an_error(tmp_path: Path) -> None:
    article_dir = tmp_path / "x"
    article_dir.mkdir()
    (article_dir / "fetch.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ConversionError, match="invalid"):
        load_provenance(article_dir)


def test_fetch_json_missing_a_field_is_an_error(tmp_path: Path) -> None:
    article_dir = tmp_path / "x"
    article_dir.mkdir()
    (article_dir / "fetch.json").write_text('{"slug": "x"}', encoding="utf-8")

    with pytest.raises(ConversionError, match="invalid"):
        load_provenance(article_dir)


def _valid_fetch_json(slug: str) -> str:
    return json.dumps(
        {"slug": slug, "source_title": slug.title(), "source_url": "u", "fetched_at": "t"}
    )


def test_a_missing_extract_file_is_an_error(tmp_path: Path) -> None:
    article_dir = tmp_path / "x"
    article_dir.mkdir()
    (article_dir / "fetch.json").write_text(_valid_fetch_json("x"), encoding="utf-8")

    with pytest.raises(ConversionError, match="missing"):
        convert_article(article_dir, tmp_path / "corpus")


def test_main_without_raw_dir_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--raw-dir", str(tmp_path / "nope"), "--corpus-dir", str(tmp_path)])

    assert exit_code == 1
    assert "make fetch" in capsys.readouterr().err


def test_main_isolates_a_failing_article_from_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw"
    (raw / "good").mkdir(parents=True)
    (raw / "bad").mkdir()
    (raw / "good" / "fetch.json").write_text(_valid_fetch_json("good"), encoding="utf-8")
    (raw / "good" / "extract.txt").write_text("Lead.\n\n== H ==\nText.", encoding="utf-8")
    (raw / "bad" / "fetch.json").write_text(
        _valid_fetch_json("bad"), encoding="utf-8"
    )  # no extract
    corpus = tmp_path / "corpus"

    exit_code = main(["--raw-dir", str(raw), "--corpus-dir", str(corpus)])

    assert exit_code == 1
    assert (corpus / "good.md").exists()  # the healthy article still converted
    assert not (corpus / "bad.md").exists()  # the failing article wrote nothing
    assert "bad" in capsys.readouterr().err
