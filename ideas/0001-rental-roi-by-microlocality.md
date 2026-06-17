# 0001 — Rental ROI & total capital gains by microlocality

**Status:** idea
**Created:** 2026-06-18

## Problem

An investor with capital to deploy wants to know **where in Dubai** to buy. "Where" means a **microlocality** — a community / sub-community like Al Jaddaf, Business Bay, or JVC — not the whole city. Two questions drive the decision: what income will the capital earn (rental ROI), and how will the asset's value move (capital gains). This idea answers both, historically and forward-looking, ranked by microlocality.

## Key questions

1. **Rental ROI:** Which microlocality offers the highest **return per dirham invested**, computed over trailing **1 / 3 / 6 / 12-month** windows? Segmentable by property type (apartment/villa) and size band (studio/1BR/2BR…).
2. **Total capital gains:** Which microlocality has delivered — and is **forecast** to deliver — the highest **total return**, defined as annualized rental yield **plus** price appreciation, over the same windows?

## Metric definitions

> ⚠️ "ROI **per capita** investment" from the original brief is ambiguous. We define it as return **per unit of capital invested** (per dirham), i.e. a yield — **not** per resident. Revisit in `docs/methodology.md` if a true per-resident view is ever wanted.

- **Gross rental yield** = annual rent ÷ purchase price.
  - `annual rent`: median Ejari/listing annual rent for matched type+size+microlocality in the window.
  - `purchase price`: median DLD transaction price for the same cohort in the window.
- **Net rental yield** = (annual rent − annual costs) ÷ purchase price, where costs ≈ service charges + agency + vacancy allowance (assumptions documented).
- **Price appreciation (annualized)** = (median price_end ÷ median price_start)^(12/window_months) − 1, on matched cohorts to avoid mix-shift bias.
- **Total return** = net rental yield + annualized price appreciation.
- **Forecast total return** = projected appreciation (model below) + forward yield estimate.

Cohort matching (same type, size band, and microlocality across periods) is essential — raw median price swings are dominated by what sold, not by value change.

## Data needed

- **Transactions:** DLD via Dubai Pulse — price, date, area/community, property type, size, rooms.
- **Rentals:** Ejari registrations (annual rent) and/or current listings (asking rent) for yield.
- **Listings (current):** Property Finder / Bayut / Dubizzle — asking price & rent, to extend beyond registered transactions and capture the live market.
- **Geometry:** community / sub-community boundaries to define and map microlocalities.

See `docs/data-sources.md` for access notes; use the claudeOS `/ingest-kb` skill to catalogue and pin down sources.

## Method

`ingest` (transactions ∥ rentals ∥ listings — parallel subagents) → `clean` (normalize fields, map every record to a canonical microlocality, build size bands) → `features` (cohort medians, yields, annualized appreciation, total return per window) → `analysis` (rank microlocalities; tables + choropleth) → `forecast` (per-microlocality price trajectory).

- **Forecast baseline:** statsmodels (e.g. SARIMAX / exponential smoothing) per microlocality time series; cross-validate on held-out recent months; report prediction intervals, not point estimates.
- **Parallelism:** fan out one subagent per microlocality cohort (or per data source during ingest); each writes parquet to `data/processed/` so results merge without collision.

## Outputs

- Ranked tables: microlocality × window → gross/net yield, annualized appreciation, total return.
- Choropleth map of total return by microlocality.
- Forecast panel: projected total return (next 6–12 months) with intervals, per microlocality.
- A short "where to buy" summary that names the top microlocalities per objective (income vs. growth vs. blended).

## Open questions

- Yield basis: registered Ejari rent (lagging, accurate) vs. current asking rent (live, noisy)? Likely report both.
- Minimum transaction count per cohort before a microlocality is rankable (small-sample noise).
- How to treat off-plan vs. ready, and new launches, in appreciation.
- Net-yield cost assumptions (service charges vary widely by building) — sensitivity-test them.
