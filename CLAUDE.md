# dddxb — Dubai property analytics

Data science on Dubai property **transactions** and **listings**. The job is to answer investment questions per **microlocality** (community / sub-community, e.g. Al Jaddaf) — rental ROI and total capital gains (rental yield + price appreciation), both historical over 1/3/6/12-month windows and forecast.

## Working agreement (always on)

1. **Plan first.** This repo defaults to plan mode (`.claude/settings.json`). Produce a plan and get sign-off before editing or running analysis. Don't jump to code.
2. **Work multi-agent.** For any task with independent units, dispatch **parallel subagents** rather than doing it serially — e.g. one agent per microlocality cohort, or ingest ∥ clean ∥ forecast. Use the `superpowers:dispatching-parallel-agents` and `superpowers:subagent-driven-development` skills. Keep shared state in `data/processed/` (parquet) so agents don't collide.
3. **Reproducible.** All deps via `uv` (`uv sync`, `uv run …`). No ad-hoc pip. Data lives in `data/` and is **never committed**.

## Map — load on demand

| Need | Read |
|------|------|
| Metric definitions, methodology, microlocality defs | `docs/` |
| Candidate analyses & their specs | `ideas/` (start: `ideas/0001-rental-roi-by-microlocality.md`) |
| Data sources & access notes | `docs/data-sources.md` |
| Reusable code | `src/dddxb/{ingest,clean,features,analysis,forecast}/` |

## Pipeline shape

`ingest` (pull DLD transactions + listings) → `clean` (normalize, map to microlocality) → `features` (yield, ROI, capital gains) → `analysis` / `forecast`. Exploratory work in `notebooks/`; anything reusable graduates into `src/dddxb/`.

## Metric caution

"ROI per capita investment" is ambiguous — define it explicitly before computing (see `docs/methodology.md`). Default working definition: return per dirham invested (i.e. yield / total return on capital), **not** per resident.
