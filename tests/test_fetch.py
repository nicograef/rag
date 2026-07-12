"""Contract tests for the fetch stage — no network, all HTTP via httpx.MockTransport."""

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rag.fetch import fetch_law, load_laws, main

XML_STUB = b'<?xml version="1.0"?><dokumente/>'


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """Build an in-memory zip archive, the shape fetch downloads from the site."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def client_serving(zips: dict[str, bytes]) -> httpx.Client:
    """Client whose transport serves ``zips[slug]`` for ``/<slug>/xml.zip``, else 404."""

    def handle(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.split("/")[1]
        if slug in zips:
            return httpx.Response(200, content=zips[slug])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_fetch_law_extracts_archive_and_records_provenance(tmp_path: Path) -> None:
    archive = zip_bytes({"BJNR000010949.xml": XML_STUB, "attachment.gif": b"GIF89a"})
    with client_serving({"gg": archive}) as client:
        files = fetch_law(client, "gg", tmp_path)

    law_dir = tmp_path / "gg"
    assert files == ["BJNR000010949.xml", "attachment.gif"]
    assert (law_dir / "BJNR000010949.xml").read_bytes() == XML_STUB
    assert (law_dir / "attachment.gif").exists()  # non-XML attachments are extracted too

    provenance = json.loads((law_dir / "fetch.json").read_text(encoding="utf-8"))
    assert provenance["slug"] == "gg"
    assert provenance["source_url"] == "https://www.gesetze-im-internet.de/gg/xml.zip"
    assert provenance["files"] == files
    assert datetime.fromisoformat(provenance["fetched_at"]).tzinfo == UTC


def test_refetch_cleanly_replaces_previous_artifacts(tmp_path: Path) -> None:
    law_dir = tmp_path / "gg"
    law_dir.mkdir()
    (law_dir / "stale.xml").write_bytes(b"from a previous run")

    with client_serving({"gg": zip_bytes({"new.xml": XML_STUB})}) as client:
        fetch_law(client, "gg", tmp_path)

    assert not (law_dir / "stale.xml").exists()
    assert (law_dir / "new.xml").exists()


def test_failed_download_leaves_previous_artifacts_intact(tmp_path: Path) -> None:
    law_dir = tmp_path / "gg"
    law_dir.mkdir()
    (law_dir / "kept.xml").write_bytes(b"from a previous run")

    with client_serving({}) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_law(client, "gg", tmp_path)

    assert (law_dir / "kept.xml").read_bytes() == b"from a previous run"


def test_main_fetches_all_configured_laws(tmp_path: Path) -> None:
    config = tmp_path / "laws.toml"
    config.write_text('[laws]\ngg = "GG"\nkassensichv = "KassenSichV"\n', encoding="utf-8")
    raw_dir = tmp_path / "raw"
    zips = {"gg": zip_bytes({"gg.xml": XML_STUB}), "kassensichv": zip_bytes({"ksv.xml": XML_STUB})}

    with client_serving(zips) as client:
        exit_code = main(["--config", str(config), "--raw-dir", str(raw_dir)], client=client)

    assert exit_code == 0
    assert (raw_dir / "gg" / "fetch.json").exists()
    assert (raw_dir / "kassensichv" / "fetch.json").exists()


def test_main_reports_failure_but_fetches_remaining_laws(tmp_path: Path) -> None:
    config = tmp_path / "laws.toml"
    config.write_text('[laws]\nmissing = "Missing"\ngg = "GG"\n', encoding="utf-8")
    raw_dir = tmp_path / "raw"

    with client_serving({"gg": zip_bytes({"gg.xml": XML_STUB})}) as client:
        exit_code = main(["--config", str(config), "--raw-dir", str(raw_dir)], client=client)

    assert exit_code == 1
    assert not (raw_dir / "missing").exists()
    assert (raw_dir / "gg" / "fetch.json").exists()  # the failure did not stop other laws


def test_repo_law_config_lists_the_mvp_corpus() -> None:
    laws = load_laws(Path(__file__).parents[1] / "laws.toml")
    assert list(laws) == ["ao_1977", "ustg_1980", "kassensichv", "gg"]
