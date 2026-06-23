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

import polars as pl

from dddxb.clean.transactions import COHORT_KEYS

log = logging.getLogger(__name__)

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
