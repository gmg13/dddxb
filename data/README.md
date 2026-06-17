# data/

- `raw/` — immutable source pulls (DLD transactions, Ejari, listing snapshots).
- `interim/` — cleaned / intermediate.
- `processed/` — analysis-ready parquet; the shared surface subagents read/write.

Contents are **gitignored** — regenerate from sources in `docs/data-sources.md`.
