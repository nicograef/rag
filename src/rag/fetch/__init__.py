"""Fetch stage — download official law XML from gesetze-im-internet.de.

Reads the law list from ``laws.toml``, downloads each law's ``xml.zip``, extracts
it into ``data/raw/<slug>/``, and records provenance in ``data/raw/<slug>/fetch.json``.
Re-running replaces each law's directory cleanly (idempotence); a failing law never
touches the artifacts of other laws or of its own previous run.

Stage contract: docs/stages/fetch.md
Theory: docs/theory/corpus-and-parsing.md
"""

import argparse
import io
import json
import shutil
import sys
import tempfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

DOWNLOAD_URL = "https://www.gesetze-im-internet.de/{slug}/xml.zip"
TIMEOUT_SECONDS = 30.0


def load_laws(config_path: Path) -> dict[str, str]:
    """Read the law config and return its slug → label mapping."""
    with config_path.open("rb") as file:
        config = tomllib.load(file)
    laws = config.get("laws")
    if not laws:
        raise ValueError(f"no [laws] table in {config_path}")
    return laws


def fetch_law(client: httpx.Client, slug: str, raw_dir: Path) -> list[str]:
    """Download and extract one law, cleanly replacing ``raw_dir/<slug>/``.

    Returns the extracted file names (relative to the law's directory). The
    previous directory is only replaced after download, extraction, and the
    provenance record all succeeded, so a failed run leaves it untouched.
    """
    url = DOWNLOAD_URL.format(slug=slug)
    response = client.get(url)
    response.raise_for_status()

    raw_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=raw_dir) as staging:
        law_dir = Path(staging) / slug
        law_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            archive.extractall(law_dir)
        files = sorted(str(p.relative_to(law_dir)) for p in law_dir.rglob("*") if p.is_file())
        provenance = {
            "slug": slug,
            "source_url": url,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": files,
        }
        provenance_json = json.dumps(provenance, indent=2, ensure_ascii=False)
        (law_dir / "fetch.json").write_text(provenance_json + "\n", encoding="utf-8")

        target = raw_dir / slug
        if target.exists():
            shutil.rmtree(target)
        law_dir.rename(target)
    return files


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    """Fetch every configured law; returns a non-zero exit code if any failed."""
    parser = argparse.ArgumentParser(
        prog="python -m rag.fetch",
        description="Download law XML from gesetze-im-internet.de into data/raw/.",
    )
    parser.add_argument("--config", type=Path, default=Path("laws.toml"), help="law list (TOML)")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="output directory")
    args = parser.parse_args(argv)

    laws = load_laws(args.config)
    if client is None:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as own_client:
            return _fetch_all(own_client, laws, args.raw_dir)
    return _fetch_all(client, laws, args.raw_dir)


def _fetch_all(client: httpx.Client, laws: dict[str, str], raw_dir: Path) -> int:
    failed: list[str] = []
    for slug, label in laws.items():
        try:
            files = fetch_law(client, slug, raw_dir)
        except (httpx.HTTPError, zipfile.BadZipFile) as error:
            print(f"✗ {slug} ({label}): {error}", file=sys.stderr)
            failed.append(slug)
        else:
            print(f"✓ {slug} ({label}): {', '.join(files)}")
    if failed:
        print(f"fetch failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0
