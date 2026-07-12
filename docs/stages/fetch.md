# Stage contract: fetch

> Code: [`src/rag/fetch/`](../../src/rag/fetch/__init__.py) ·
> Roadmap: [Phase 1 — Fetch & convert](../roadmap.md)

Downloads the official XML of every configured law from gesetze-im-internet.de into
`data/raw/`, with a provenance record per law. First stage of the offline ingestion
pipeline; licensing basis in the
[corpus licensing decision](../roadmap.md#decisions).

## Invocation

```sh
make fetch                      # wraps:
uv run python -m rag.fetch      # options: --config laws.toml --raw-dir data/raw
```

## Input

- [`laws.toml`](../../laws.toml) — a `[laws]` table mapping each law's
  gesetze-im-internet.de **slug** to a human-readable label (log output only).
  Adding a law to the corpus means adding one entry.
- Network: `https://www.gesetze-im-internet.de/<slug>/xml.zip` per law
  (≈ 0.4 MB zipped / 1.9 MB extracted for the four MVP laws, measured 2026-07-12).

## Output

One directory per law:

```
data/raw/<slug>/
├── <doknr>.xml     # extracted archive contents (GiI-Norm DTD 1.01)
└── fetch.json      # provenance record
```

`fetch.json` schema:

| Key          | Meaning                                                    |
| ------------ | ---------------------------------------------------------- |
| `slug`       | the law's site slug (= directory and config key)           |
| `source_url` | the exact URL the archive was downloaded from              |
| `fetched_at` | download time, ISO 8601 UTC                                 |
| `files`      | extracted file names, relative to the law's directory      |

The archive may contain non-XML attachments (the DTD allows `IMG`/`FILE`); fetch
extracts and lists them like any other file — filtering is convert's job.

## Guarantees

- **Idempotence, not determinism.** Re-running cleanly replaces each law's directory —
  the new contents are staged in a temporary directory and swapped in only after
  download, extraction, and provenance record all succeeded; no stale files survive.
  Output is *not* deterministic: the source is a living corpus and amended laws
  legitimately change it.
- **Per-law isolation.** A failing law never touches other laws' artifacts or its own
  previous run's directory.

## Failure behaviour

An HTTP error or corrupt archive skips that law with a message on stderr; remaining
laws are still fetched. The exit code is non-zero if any law failed.

## Downstream consumers

**convert** ([contract](convert.md)) reads the extracted XML file(s) and takes
`source_url` and `fetched_at` from `fetch.json` for the corpus front matter.
