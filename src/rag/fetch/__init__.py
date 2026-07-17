"""Fetch stage — download English Wikipedia article extracts via the MediaWiki Action API.

Reads the article list from ``clubs.toml`` (slug → exact article title) and, for each
article, requests its plain-text extract from the read-only MediaWiki Action API
(TextExtracts: ``prop=extracts&explaintext=1&exsectionformat=wiki``) and writes the extract
plus a provenance record to ``data/raw/<slug>/`` (``extract.txt`` + ``fetch.json``).
Re-running replaces each article's directory cleanly (idempotence); a failing article never
touches the artifacts of other articles or of its own previous run. Fetch promises
idempotence, not determinism — Wikipedia is a living corpus, so re-running legitimately
changes the text.

API etiquette is a hard contract, not a nicety: a descriptive ``User-Agent`` (never a spoofed
browser), sequential requests (one article at a time via the shared per-source runner),
``maxlag=5`` for courtesy, and ``Accept-Encoding: gzip``. An article whose extract is empty
(a page with no lead paragraph) fails the stage rather than entering the corpus silently —
this is the non-empty-extract smoke guard.

Stage contract: docs/stages/fetch.md
Theory: docs/theory/corpus-and-parsing.md
"""

import argparse
import json
import shutil
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import httpx

from rag import RAW_DIR, __version__, run_per_source

# Read-only MediaWiki Action API endpoint (no key, no OAuth).
API_URL = "https://en.wikipedia.org/w/api.php"

# Etiquette contract (docs/theory/corpus-and-parsing.md): a descriptive User-Agent naming the
# tool and a contact is mandatory — the API blocks generic/spoofed agents. maxlag=5 asks the
# API to defer when replication lag is high; gzip keeps transfers small. Sent on every request
# so they hold regardless of how the httpx client was constructed (tests inject a bare client).
CONTACT = "graef.nico@gmail.com"
USER_AGENT = f"rag-playbook/{__version__} ({CONTACT})"
MAXLAG_SECONDS = 5
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}

TIMEOUT_SECONDS = 30.0
DEFAULT_CONFIG = Path("clubs.toml")


class FetchError(Exception):
    """Raised when an article cannot be fetched or yields an empty extract."""


def load_articles(config_path: Path) -> dict[str, str]:
    """Read the corpus config and return its slug → article-title mapping."""
    with config_path.open("rb") as file:
        config = tomllib.load(file)
    clubs = config.get("clubs")
    if not clubs:
        raise FetchError(f"no [clubs] table in {config_path}")
    return clubs


def _query_params(title: str) -> dict[str, str]:
    """The Action API query for one article's plain-text extract and provenance."""
    return {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "extracts|info",
        "explaintext": "1",
        "exsectionformat": "wiki",
        "inprop": "url",
        "redirects": "1",
        "maxlag": str(MAXLAG_SECONDS),
        "titles": title,
    }


def fetch_article(client: httpx.Client, slug: str, title: str, raw_dir: Path) -> Path:
    """Download one article's extract, cleanly replacing ``raw_dir/<slug>/``; returns its path.

    Writes ``extract.txt`` (the plain-text extract) and ``fetch.json`` (provenance: slug,
    resolved title, page id, revision id, article URL, fetch time). The previous directory is
    only replaced after the request, the non-empty check, and both writes all succeeded, so a
    failed run leaves it untouched. An empty extract fails the article (the smoke guard).
    """
    response = client.get(API_URL, params=_query_params(title), headers=REQUEST_HEADERS)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:  # e.g. maxlag exceeded — the API answers 200 with an error body
        raise FetchError(
            f"API error for {title!r}: {payload['error'].get('info', payload['error'])}"
        )

    pages = payload.get("query", {}).get("pages", [])
    if not pages:
        raise FetchError(f"no page returned for {title!r}")
    page = pages[0]
    if page.get("missing"):
        raise FetchError(f"article {title!r} does not exist")
    extract = page.get("extract", "")
    if not extract.strip():
        raise FetchError(f"article {title!r} has an empty extract — nothing to ingest")

    provenance = {
        "slug": slug,
        "source_title": page["title"],  # the resolved title (redirects followed)
        "page_id": page["pageid"],
        "revision_id": page["lastrevid"],
        "source_url": page["fullurl"],
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    raw_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=raw_dir) as staging:
        article_dir = Path(staging) / slug
        article_dir.mkdir()
        (article_dir / "extract.txt").write_text(extract, encoding="utf-8")
        provenance_json = json.dumps(provenance, indent=2, ensure_ascii=False)
        (article_dir / "fetch.json").write_text(provenance_json + "\n", encoding="utf-8")

        target = raw_dir / slug
        if target.exists():
            shutil.rmtree(target)
        article_dir.rename(target)
    return target


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    """Fetch every configured article; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.fetch",
        description="Download Wikipedia article extracts via the MediaWiki Action API into data/raw/.",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="corpus article list (TOML)"
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="output directory")
    args = parser.parse_args(argv)

    articles = load_articles(args.config)
    if client is None:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as own_client:
            return _fetch_all(own_client, articles, args.raw_dir)
    return _fetch_all(client, articles, args.raw_dir)


def _fetch_all(client: httpx.Client, articles: dict[str, str], raw_dir: Path) -> int:
    jobs = [
        (
            f"{slug} ({title})",
            lambda slug=slug, title=title: f"→ {fetch_article(client, slug, title, raw_dir)}",
        )
        for slug, title in articles.items()
    ]
    return run_per_source("fetch", jobs, (httpx.HTTPError, FetchError))
