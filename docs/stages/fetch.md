# Stage contract: fetch

> Code: [`src/rag/fetch/`](../../src/rag/fetch/__init__.py) ·
> Roadmap: [Phase 1 — Fetch & convert](../roadmap.md) ·
> Theory: [corpus & parsing](../theory/corpus-and-parsing.md)

Downloads a plain-text extract for every configured English Wikipedia article into
`data/raw/`, with a provenance record per article. First stage of the offline ingestion
pipeline; licensing basis in the [corpus licensing decision](../roadmap.md#decisions).

## Invocation

```sh
make fetch                      # wraps:
uv run python -m rag.fetch      # options: --config clubs.toml --raw-dir data/raw
```

## Input

- [`clubs.toml`](../../clubs.toml) — a `[clubs]` table mapping each article's short,
  filesystem-safe **slug** to the exact Wikipedia **article title** used as the API
  `titles=` value (e.g. `arsenal = "Arsenal F.C."`). The slug is the stable key for
  `data/raw/<slug>/`, `data/corpus/<slug>.md`, and the DB `slug` column. Adding an
  article to the corpus means adding one line here; `CORPUS_CONFIG`/`--config` can point
  elsewhere to swap the whole list.
- Network: the read-only English Wikipedia **MediaWiki Action API**
  (`https://en.wikipedia.org/w/api.php`, no key/OAuth), one request per article via the
  **TextExtracts** extension:
  `action=query&prop=extracts|info&explaintext=1&exsectionformat=wiki&inprop=url&redirects=1&formatversion=2&maxlag=5&titles=<title>`.
  Returns the full article as plain text with `== Heading ==` markers.

## Output

One directory per article:

```
data/raw/<slug>/
├── extract.txt     # the plain-text extract
└── fetch.json      # provenance record
```

`fetch.json` schema:

| Key            | Meaning                                                   |
| -------------- | ----------------------------------------------------------|
| `slug`         | the article's config slug (= directory and config key)    |
| `source_title` | the resolved article title (redirects followed)           |
| `page_id`      | the MediaWiki page id                                     |
| `revision_id`  | the revision id the extract was taken from                |
| `source_url`   | the article's canonical URL (from `inprop=url`)            |
| `fetched_at`   | fetch time, ISO 8601 UTC                                   |

## Guarantees

- **API etiquette is a hard contract, not a nicety.** Every request carries a
  descriptive `User-Agent` (`rag-playbook/<version> (<contact>)`, never a spoofed
  browser); requests are **sequential** — one article at a time via the shared
  per-source runner; `maxlag=5` asks the API to defer under replication lag; and
  `Accept-Encoding: gzip` keeps transfers small. Fetch requests one title per call, well
  under the API's `exlimit` ≤ 20 titles/request.
- **Idempotence, not determinism.** Re-running cleanly replaces each article's
  directory — the new artifacts are staged in a temporary directory and swapped in only
  after the request, the non-empty check, and both writes all succeed, so a failed run
  leaves the previous directory untouched. Output is *not* deterministic: Wikipedia is a
  living corpus (squads, managers, honours change), so re-running legitimately changes
  the text.
- **Per-article isolation.** A failing article never touches other articles' artifacts
  or its own previous run's directory.
- **Non-empty-extract smoke guard.** An article whose extract is empty (a page with no
  lead paragraph) fails that article rather than entering the corpus silently; running
  `make fetch` over all 20 configured articles is the 20-article smoke test.

## Failure behaviour

An HTTP error, a missing page, a `maxlag` error body, or an empty extract fails that
article with a message on stderr; remaining articles are still fetched. The exit code is
non-zero if any article failed.

## Downstream consumers

**convert** ([contract](convert.md)) reads `extract.txt` and takes `source_title`,
`source_url`, and `fetched_at` from `fetch.json` for the corpus front matter.
