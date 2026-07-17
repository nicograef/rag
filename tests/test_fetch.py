"""Contract tests for the fetch stage — no network, all HTTP via httpx.MockTransport."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rag.fetch import USER_AGENT, FetchError, fetch_article, load_articles, main


def api_response(title: str, extract: str, *, missing: bool = False) -> dict:
    """A ``formatversion=2`` Action API query response for one title."""
    if missing:
        return {"query": {"pages": [{"title": title, "missing": True}]}}
    return {
        "query": {
            "pages": [
                {
                    "title": title,
                    "pageid": 1,
                    "lastrevid": 2,
                    "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "extract": extract,
                }
            ]
        }
    }


def client_serving(pages: dict[str, str], *, seen: list | None = None) -> httpx.Client:
    """A client whose transport answers the API from ``pages`` (title → extract), else missing."""

    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        title = request.url.params["titles"]
        if title not in pages:
            return httpx.Response(200, json=api_response(title, "", missing=True))
        return httpx.Response(200, json=api_response(title, pages[title]))

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_fetch_article_writes_extract_and_provenance(tmp_path: Path) -> None:
    with client_serving({"Arsenal F.C.": "Arsenal is a club.\n\n== History ==\nOld."}) as client:
        target = fetch_article(client, "arsenal", "Arsenal F.C.", tmp_path)

    assert target == tmp_path / "arsenal"
    assert (target / "extract.txt").read_text(encoding="utf-8").startswith("Arsenal is a club.")
    provenance = json.loads((target / "fetch.json").read_text(encoding="utf-8"))
    assert provenance["slug"] == "arsenal"
    assert provenance["source_title"] == "Arsenal F.C."
    assert provenance["source_url"] == "https://en.wikipedia.org/wiki/Arsenal_F.C."
    assert provenance["revision_id"] == 2
    assert datetime.fromisoformat(provenance["fetched_at"]).tzinfo == UTC


def test_fetch_sends_the_etiquette_headers_and_query(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    with client_serving({"Arsenal F.C.": "Text."}, seen=seen) as client:
        fetch_article(client, "arsenal", "Arsenal F.C.", tmp_path)

    request = seen[0]
    assert request.headers["User-Agent"] == USER_AGENT
    assert (
        USER_AGENT.startswith("rag-playbook/") and "(" in USER_AGENT
    )  # descriptive, not a browser
    assert request.headers["Accept-Encoding"] == "gzip"
    assert request.url.params["maxlag"] == "5"
    assert request.url.params["explaintext"] == "1"
    assert request.url.params["exsectionformat"] == "wiki"
    assert request.url.params["titles"] == "Arsenal F.C."


def test_an_empty_extract_fails_the_article(tmp_path: Path) -> None:
    with client_serving({"Empty F.C.": "   "}) as client, pytest.raises(FetchError, match="empty"):
        fetch_article(client, "empty", "Empty F.C.", tmp_path)


def test_a_missing_article_fails(tmp_path: Path) -> None:
    with client_serving({}) as client, pytest.raises(FetchError, match="does not exist"):
        fetch_article(client, "ghost", "Ghost F.C.", tmp_path)


def test_a_maxlag_error_body_fails(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": "maxlag", "info": "waiting for db"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(FetchError, match="API error"):
            fetch_article(client, "x", "X F.C.", tmp_path)


def test_refetch_cleanly_replaces_previous_artifacts(tmp_path: Path) -> None:
    article_dir = tmp_path / "arsenal"
    article_dir.mkdir()
    (article_dir / "stale.txt").write_text("from a previous run", encoding="utf-8")

    with client_serving({"Arsenal F.C.": "New text."}) as client:
        fetch_article(client, "arsenal", "Arsenal F.C.", tmp_path)

    assert not (article_dir / "stale.txt").exists()
    assert (article_dir / "extract.txt").read_text(encoding="utf-8") == "New text."


def test_a_failed_fetch_leaves_previous_artifacts_intact(tmp_path: Path) -> None:
    article_dir = tmp_path / "arsenal"
    article_dir.mkdir()
    (article_dir / "extract.txt").write_text("kept", encoding="utf-8")

    with client_serving({}) as client, pytest.raises(FetchError):
        fetch_article(client, "arsenal", "Arsenal F.C.", tmp_path)

    assert (article_dir / "extract.txt").read_text(encoding="utf-8") == "kept"


def test_main_fetches_all_configured_articles(tmp_path: Path) -> None:
    config = tmp_path / "clubs.toml"
    config.write_text(
        '[clubs]\narsenal = "Arsenal F.C."\nchelsea = "Chelsea F.C."\n', encoding="utf-8"
    )
    raw_dir = tmp_path / "raw"

    with client_serving({"Arsenal F.C.": "A.", "Chelsea F.C.": "C."}) as client:
        exit_code = main(["--config", str(config), "--raw-dir", str(raw_dir)], client=client)

    assert exit_code == 0
    assert (raw_dir / "arsenal" / "extract.txt").exists()
    assert (raw_dir / "chelsea" / "extract.txt").exists()


def test_main_reports_failure_but_fetches_remaining_articles(tmp_path: Path) -> None:
    config = tmp_path / "clubs.toml"
    config.write_text('[clubs]\nghost = "Ghost F.C."\narsenal = "Arsenal F.C."\n', encoding="utf-8")
    raw_dir = tmp_path / "raw"

    with client_serving({"Arsenal F.C.": "A."}) as client:
        exit_code = main(["--config", str(config), "--raw-dir", str(raw_dir)], client=client)

    assert exit_code == 1
    assert not (raw_dir / "ghost").exists()
    assert (raw_dir / "arsenal" / "extract.txt").exists()  # the failure did not stop other articles


def test_repo_config_lists_the_twenty_current_clubs() -> None:
    clubs = load_articles(Path(__file__).parents[1] / "clubs.toml")

    assert len(clubs) == 20
    assert clubs["arsenal"] == "Arsenal F.C."
    assert clubs["wolves"] == "Wolverhampton Wanderers F.C."
    # slugs are filesystem-safe: lowercase, alphanumerics and hyphens only.
    assert all(slug == slug.lower() and slug.replace("-", "").isalnum() for slug in clubs)
