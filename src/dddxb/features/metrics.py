"""Investment metrics per microlocality, per docs/methodology.md.

Cohort = (microlocality, property_type, bed_band). All metrics compare matched
cohort medians to avoid mix-shift bias:

- gross_yield   = median annual rent / median sale price
- net_yield     = gross_yield * (1 - OPEX_FRACTION)   [placeholder; sensitivity-test]
- ann_appr      = (median price late-half / early-half) ^ (24 / window_months) - 1
- total_return  = net_yield + ann_appr

Cohorts thinner than ``min_cohort`` sales/rents are flagged (``thin``) and kept
out of the headline ranking rather than ranked on noise.

NOTE on appreciation reliability: ``ann_appr`` is only as good as the temporal
coverage of the sales. A depth-capped pull returns the *most-recent* records, so
early-window months are under-sampled and the half-window ratio is biased (and the
short-window exponent amplifies it). Trust yields from any pull; trust appreciation
only from a **date-stratified** pull that samples each month across the window.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import polars as pl

from dddxb.clean.transactions import COHORT_KEYS, clean_sales

log = logging.getLogger(__name__)


def _month_index(ym: str) -> int:
    """'YYYY-MM' -> absolute month number, for differencing."""
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1

# Default operating-cost haircut on gross rent (service charge + vacancy +
# management). Documented placeholder — vary in sensitivity analysis.
OPEX_FRACTION = 0.20


def build_cohort_metrics(
    sales: pl.DataFrame,
    rents: pl.DataFrame,
    *,
    months: int,
    today: date | None = None,
    opex: float = OPEX_FRACTION,
    min_cohort: int = 5,
    min_half: int = 3,
) -> pl.DataFrame:
    """Join sale + rent cohort medians and compute yield / appreciation / return."""
    keys = list(COHORT_KEYS)
    today = today or date.today()
    midpoint = today - timedelta(days=round(months * 30.4 / 2))

    sale_cohort = sales.group_by(keys).agg(
        pl.len().alias("n_sales"),
        pl.col("price").median().alias("med_price"),
        pl.col("psf").median().alias("med_psf"),
        pl.col("size").median().alias("med_size"),
    )
    rent_cohort = rents.group_by(keys).agg(
        pl.len().alias("n_rents"),
        pl.col("price").median().alias("med_rent"),
    )

    # Appreciation: matched-cohort early vs late half-window median price.
    late_mask = pl.col("date") >= midpoint
    early = sales.filter(~late_mask).group_by(keys).agg(
        pl.len().alias("n_early"), pl.col("price").median().alias("p_early")
    )
    late = sales.filter(late_mask).group_by(keys).agg(
        pl.len().alias("n_late"), pl.col("price").median().alias("p_late")
    )
    exp = 24.0 / months  # half-window centres are ~months/2 apart
    appr = (
        early.join(late, on=keys, how="inner")
        .with_columns(
            pl.when(
                (pl.col("n_early") >= min_half) & (pl.col("n_late") >= min_half)
                & (pl.col("p_early") > 0)
            )
            .then((pl.col("p_late") / pl.col("p_early")) ** exp - 1)
            .otherwise(None)
            .alias("ann_appr")
        )
        .select([*keys, "ann_appr"])
    )

    out = (
        sale_cohort.join(rent_cohort, on=keys, how="inner")
        .join(appr, on=keys, how="left")
        .with_columns(
            (pl.col("med_rent") / pl.col("med_price")).alias("gross_yield"),
        )
        .with_columns(
            (pl.col("gross_yield") * (1 - opex)).alias("net_yield"),
        )
        .with_columns(
            (pl.col("net_yield") + pl.col("ann_appr").fill_null(0.0)).alias("total_return"),
            (
                (pl.col("n_sales") < min_cohort) | (pl.col("n_rents") < min_cohort)
            ).alias("thin"),
        )
        .sort("total_return", descending=True)
    )
    return out


def monthly_psf(
    history: pl.DataFrame, *, min_per_month: int = 5, apartments_only: bool = False
) -> pl.DataFrame:
    """Median AED/sqft per microlocality per month from the sale-history sample.

    Cleans first (trims outliers, adds psf), then keeps only months with enough
    sales for a stable median. psf controls for unit-size mix across months.
    ``apartments_only`` restricts to the dominant, comparable product so the
    series isn't whipsawed by villa/penthouse mix shifts (use for appreciation).
    """
    df = clean_sales(history)
    if apartments_only:
        df = df.filter(pl.col("property_type") == "apartments")
    return (
        df.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .group_by(["microlocality", "ym"])
        .agg(pl.len().alias("n"), pl.col("psf").median().alias("psf"))
        .filter(pl.col("n") >= min_per_month)
        .sort(["microlocality", "ym"])
    )


def appreciation_from_history(
    history: pl.DataFrame,
    *,
    windows: tuple[int, ...] = (3, 6, 12),
    min_per_month: int = 5,
) -> pl.DataFrame:
    """Annualized appreciation per microlocality from the monthly psf series.

    For each trailing window ``w`` (months): ``(psf_latest / psf_{latest-w})^(12/w) - 1``
    using each microlocality's latest month as the anchor (nearest month within ±1
    used if an exact target month is missing). Also a long-run CAGR over the full
    covered span. Methodology: docs/methodology.md.
    """
    series = monthly_psf(history, min_per_month=min_per_month)
    rows: list[dict] = []
    for name in series["microlocality"].unique().to_list():
        g = series.filter(pl.col("microlocality") == name)
        psf = {r["ym"]: r["psf"] for r in g.iter_rows(named=True)}
        idx = {ym: _month_index(ym) for ym in psf}
        if not psf:
            continue
        latest = max(psf, key=lambda k: idx[k])
        latest_i, psf_latest = idx[latest], psf[latest]

        def psf_near(target_i: int, tol: int = 1):
            cands = [ym for ym, i in idx.items() if abs(i - target_i) <= tol]
            if not cands:
                return None
            return psf[min(cands, key=lambda ym: abs(idx[ym] - target_i))]

        rec = {"microlocality": name, "psf_latest": psf_latest, "months_covered": len(psf)}
        for w in windows:
            start = psf_near(latest_i - w)
            rec[f"ann_appr_{w}m"] = (
                (psf_latest / start) ** (12.0 / w) - 1 if start and start > 0 else None
            )
        earliest = min(psf, key=lambda k: idx[k])
        span = latest_i - idx[earliest]
        rec["cagr"] = (
            (psf_latest / psf[earliest]) ** (12.0 / span) - 1
            if span >= 12 and psf[earliest] > 0 else None
        )
        rows.append(rec)
    return pl.DataFrame(rows)


def appreciation_ols(
    history: pl.DataFrame, *, min_per_month: int = 5, min_months: int = 12
) -> pl.DataFrame:
    """Robust annualized appreciation per microlocality via a log-linear OLS fit.

    Fits ``ln(median apartment psf) ~ month`` over the whole covered span and
    annualizes the slope (``exp(12*slope) - 1``). Using every month (not two
    endpoints) and a per-month median makes this far more stable than a
    single-month point-to-point ratio, which is dominated by product-mix noise in
    heterogeneous communities (e.g. Meydan reads +56% point-to-point vs a sane
    ~19% here). ``appr_r2`` is the fit quality — low R² means the community's psf
    is too noisy/mixed to trust the trend, so lean on the sample validator.
    """
    series = monthly_psf(history, min_per_month=min_per_month, apartments_only=True)
    rows: list[dict] = []
    for name in series["microlocality"].unique().to_list():
        g = series.filter(pl.col("microlocality") == name).sort("ym")
        if g.height < min_months:
            continue
        x = np.array([_month_index(v) for v in g["ym"].to_list()], dtype=float)
        psf = g["psf"].to_numpy()
        y = np.log(psf)
        slope, intercept = np.polyfit(x, y, 1)
        resid = y - (intercept + slope * x)
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else None
        rows.append({
            "microlocality": name,
            "cagr": float(np.exp(12 * slope) - 1),
            "appr_r2": r2,
            "psf_latest": float(psf[-1]),
            "months_covered": int(g.height),
        })
    return pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={"microlocality": pl.String, "cagr": pl.Float64, "appr_r2": pl.Float64,
                "psf_latest": pl.Float64, "months_covered": pl.Int64}
    )


def apply_history_appreciation(ranking: pl.DataFrame, history: pl.DataFrame) -> pl.DataFrame:
    """Replace the ranking's appreciation with the robust OLS-CAGR history figure.

    Sets ``ann_appr`` to the log-linear CAGR from :func:`appreciation_ols`, carries
    ``appr_r2`` (confidence) and ``psf_latest``, and recomputes
    ``total_return = net_yield + ann_appr``.
    """
    appr = appreciation_ols(history)
    out = (
        ranking.join(appr.select(["microlocality", "cagr", "appr_r2", "psf_latest"]),
                     on="microlocality", how="left")
        .with_columns(pl.col("cagr").alias("ann_appr"))
        .with_columns((pl.col("net_yield") + pl.col("ann_appr").fill_null(0.0)).alias("total_return"))
        .sort("total_return", descending=True)
    )
    return out


def rank_microlocalities(cohorts: pl.DataFrame) -> pl.DataFrame:
    """Roll cohorts up to a per-microlocality ranking (count-weighted, non-thin)."""
    solid = cohorts.filter(~pl.col("thin"))
    w = pl.min_horizontal("n_sales", "n_rents").alias("_w")
    return (
        solid.with_columns(w)
        .group_by("microlocality")
        .agg(
            pl.col("n_sales").sum().alias("n_sales"),
            pl.col("n_rents").sum().alias("n_rents"),
            ((pl.col("gross_yield") * pl.col("_w")).sum() / pl.col("_w").sum()).alias("gross_yield"),
            ((pl.col("net_yield") * pl.col("_w")).sum() / pl.col("_w").sum()).alias("net_yield"),
            ((pl.col("ann_appr") * pl.col("_w")).sum() / pl.col("_w").sum()).alias("ann_appr"),
            ((pl.col("total_return") * pl.col("_w")).sum() / pl.col("_w").sum()).alias("total_return"),
            pl.col("med_psf").median().alias("med_psf"),
        )
        .sort("total_return", descending=True)
    )
