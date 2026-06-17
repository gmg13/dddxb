# dddxb

Data science & analytics on **Dubai property transactions and current listings**.

The goal: answer investor questions at the **microlocality** level (community / sub-community such as Al Jaddaf), for example —

- Which microlocality has the **highest rental ROI per dirham invested** over the last 1 / 3 / 6 / 12 months?
- Which microlocality has seen — and is forecast to see — the **most total capital gains** (annualized rental yield + price appreciation)?

## Stack

Python 3.12 via [`uv`](https://docs.astral.sh/uv/). Core: **DuckDB** (SQL analytics on local parquet), **polars/pandas**, **geopandas** (microlocality geometry), **scikit-learn / statsmodels** (forecasting), **plotly**, **JupyterLab**.

## Setup

```bash
uv sync              # create .venv and install deps
uv run jupyter lab   # exploratory work
uv run pytest        # tests
```

## Layout

```
src/dddxb/   ingest → clean → features → analysis → forecast   (importable package)
notebooks/   numbered exploratory notebooks
ideas/       analysis idea docs (see ideas/README.md)
docs/        data dictionary, microlocality defs, metric methodology, data sources
data/        raw / interim / processed   (gitignored — never committed)
tests/
```

## How we work here

- **Plan mode is the default** (`.claude/settings.json`) — plans before edits.
- **Multi-agent by default** — independent work is dispatched to parallel subagents (see `CLAUDE.md`).
- Data is never committed; pipelines regenerate it from sources documented in `docs/data-sources.md`.
