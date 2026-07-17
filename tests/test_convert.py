"""Contract tests for the convert stage — golden-file driven, no network."""

import shutil
from pathlib import Path

import pytest

from rag.convert import ConversionError, convert_law, load_provenance, main

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_LAW = FIXTURES / "raw" / "kassensichv"
GOLDEN = FIXTURES / "corpus" / "kassensichv.md"

LAWS = ["kassensichv", "strukturg", "artg", "tabelleng"]


@pytest.mark.parametrize("slug", LAWS)
def test_convert_law_matches_golden_file(slug: str, tmp_path: Path) -> None:
    output = convert_law(FIXTURES / "raw" / slug, tmp_path)

    assert output == tmp_path / f"{slug}.md"
    assert output.read_bytes() == (FIXTURES / "corpus" / f"{slug}.md").read_bytes()


@pytest.mark.parametrize("slug", LAWS)
def test_convert_is_deterministic(slug: str, tmp_path: Path) -> None:
    law_dir = FIXTURES / "raw" / slug
    first = convert_law(law_dir, tmp_path / "one").read_bytes()
    second = convert_law(law_dir, tmp_path / "two").read_bytes()

    assert first == second


def law_dir_with_xml(tmp_path: Path, xml: str) -> Path:
    """A minimal law directory: one XML file plus a matching fetch.json."""
    law_dir = tmp_path / "somelaw"
    law_dir.mkdir(parents=True)
    (law_dir / "law.xml").write_text(xml, encoding="utf-8")
    (law_dir / "fetch.json").write_text(
        '{"slug": "somelaw", "source_url": "https://example.org/somelaw/xml.zip",'
        ' "fetched_at": "2026-07-12T00:00:00+00:00", "files": ["law.xml"]}',
        encoding="utf-8",
    )
    return law_dir


HEADER_NORM = (
    "<norm><metadaten><jurabk>SomeLaw</jurabk><langue>Ein Gesetz</langue></metadaten></norm>"
)


@pytest.mark.parametrize(
    "norm",
    [
        # Unknown inline element in a paragraph — must not be silently dropped.
        "<norm><metadaten><enbez>§ 1</enbez></metadaten><textdaten><text>"
        "<Content><P>Bild <IMG SRC='x.png'/> hier.</P></Content></text></textdaten></norm>",
        # A block-level element in a table cell is beyond the cell-flattening model.
        "<norm><metadaten><enbez>Anlage 1</enbez></metadaten><textdaten><text><Content><P>"
        "<table><tgroup cols='1'><tbody><row><entry><P>Absatz</P></entry></row>"
        "</tbody></tgroup></table></P></Content></text></textdaten></norm>",
        # A gliederungskennzahl of length 18 would push its units past H6.
        "<norm><metadaten><gliederungseinheit>"
        "<gliederungskennzahl>010010010010010010</gliederungskennzahl>"
        "<gliederungsbez>Tief</gliederungsbez></gliederungseinheit></metadaten></norm>",
        # A section norm must render to an empty body; stray text is content loss.
        "<norm><metadaten><gliederungseinheit><gliederungskennzahl>010</gliederungskennzahl>"
        "<gliederungsbez>Teil</gliederungsbez></gliederungseinheit></metadaten><textdaten><text>"
        "<Content><P>Text im Abschnitt</P></Content></text></textdaten></norm>",
        # A thead with two rows is outside the single-header-row table model.
        "<norm><metadaten><enbez>Anlage 1</enbez></metadaten><textdaten><text><Content><P>"
        "<table><tgroup cols='1'><thead><row><entry>A</entry></row>"
        "<row><entry>B</entry></row></thead><tbody><row><entry>C</entry></row></tbody>"
        "</tgroup></table></P></Content></text></textdaten></norm>",
        # A DD holding something other than <LA> — the item shape is unsupported.
        "<norm><metadaten><enbez>§ 1</enbez></metadaten><textdaten><text><Content><P>"
        "Liste: <DL><DT>1.</DT><DD><P>Absatz statt LA</P></DD></DL></P></Content>"
        "</text></textdaten></norm>",
        # A gliederungskennzahl whose length is not a multiple of three is malformed.
        "<norm><metadaten><gliederungseinheit><gliederungskennzahl>0100</gliederungskennzahl>"
        "<gliederungsbez>Teil</gliederungsbez></gliederungseinheit></metadaten></norm>",
    ],
)
def test_unsupported_structure_fails_loudly_instead_of_losing_content(
    tmp_path: Path, norm: str
) -> None:
    xml = f"<dokumente builddate='20260101000000'>{HEADER_NORM}{norm}</dokumente>"
    law_dir = law_dir_with_xml(tmp_path, xml)

    with pytest.raises(ConversionError):
        convert_law(law_dir, tmp_path / "corpus")
    assert not (tmp_path / "corpus" / "somelaw.md").exists()  # nothing written on failure


def test_non_xml_files_are_ignored_and_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    law_dir = tmp_path / "kassensichv"
    shutil.copytree(FIXTURE_LAW, law_dir)
    (law_dir / "attachment.gif").write_bytes(b"GIF89a")
    fetch_json = (law_dir / "fetch.json").read_text(encoding="utf-8")
    (law_dir / "fetch.json").write_text(
        fetch_json.replace('"BJNR351500017.xml"', '"BJNR351500017.xml",\n    "attachment.gif"'),
        encoding="utf-8",
    )

    output = convert_law(law_dir, tmp_path / "corpus")

    assert output.read_bytes() == GOLDEN.read_bytes()
    assert "ignoring non-XML file: attachment.gif" in capsys.readouterr().out


def test_a_non_object_fetch_json_is_an_error(tmp_path: Path) -> None:
    law_dir = tmp_path / "somelaw"
    law_dir.mkdir()
    (law_dir / "fetch.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ConversionError, match=r"invalid .*fetch\.json"):
        load_provenance(law_dir)


def test_main_converts_all_laws_and_a_failing_law_stops_no_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_dir = tmp_path / "raw"
    shutil.copytree(FIXTURE_LAW, raw_dir / "kassensichv")
    broken = law_dir_with_xml(raw_dir, "<dokumente>not well-formed")
    corpus_dir = tmp_path / "corpus"

    exit_code = main(["--raw-dir", str(raw_dir), "--corpus-dir", str(corpus_dir)])

    assert exit_code == 1
    assert (corpus_dir / "kassensichv.md").exists()
    assert not (corpus_dir / "somelaw.md").exists()
    assert broken.name in capsys.readouterr().err


def test_main_without_fetched_laws_fails_with_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--raw-dir", str(tmp_path / "missing"), "--corpus-dir", str(tmp_path)])

    assert exit_code == 1
    assert "make fetch" in capsys.readouterr().err
