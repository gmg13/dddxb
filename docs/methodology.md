# Methodology

Canonical metric definitions and modelling decisions. Idea docs reference this; keep it the single source of truth so analyses stay comparable.

## Terminology

- **Microlocality** — a community or sub-community (e.g. Al Jaddaf), the unit of analysis. Canonical list + mapping rules in `docs/microlocalities.md`.
- **Cohort** — records sharing (microlocality, property type, size band). Metrics compare cohorts across time to avoid mix-shift bias.

## "ROI per capita investment"

The original brief's phrase is ambiguous. **Working definition: return per unit of capital invested (per dirham)** — i.e. a yield / return-on-capital. *Not* per resident. If a true per-resident measure is ever needed, define it here separately.

## Metrics

- **Gross rental yield** = annual rent ÷ purchase price (matched cohort medians).
- **Net rental yield** = (annual rent − annual costs) ÷ purchase price. Costs ≈ service charges + agency + vacancy allowance; assumptions stated per analysis and sensitivity-tested.
- **Annualized price appreciation** = (median price_end ÷ median price_start)^(12 / window_months) − 1, on matched cohorts.
- **Total return** = net rental yield + annualized price appreciation.

## Windows

Trailing 1 / 3 / 6 / 12 months. Note small-sample noise on short windows; enforce a minimum cohort transaction count (value TBD) before ranking.

## Forecasting

- Per-microlocality time series; baseline statsmodels (SARIMAX / exponential smoothing).
- Always report **prediction intervals**, never bare point estimates.
- Validate on held-out recent months; record error metrics.

## Bias guards

- Match cohorts across periods (don't compare raw medians).
- Separate off-plan vs. ready where it affects price/appreciation.
- Flag microlocalities with thin data rather than ranking them on noise.
