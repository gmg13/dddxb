"""Tests for clean + features: banding, outlier trim, yield/appreciation math."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from dddxb.clean.transactions import clean_rents, clean_sales
from dddxb.features.metrics import (
    appreciation_from_history,
    build_cohort_metrics,
    rank_microlocalities,
)
from dddxb.ingest.uae_realestate import month_buckets


def _sale_row(micro, ptype, beds, price, size, d):
    return {"microlocality": micro, "purpose": "for-sale", "date": d,
            "price": price, "area": micro, "property_type": ptype, "size": size, "beds": beds}


def _rent_row(micro, ptype, beds, price, d):
    return {"microlocality": micro, "purpose": "for-rent", "date": d,
            "price": price, "area": micro, "property_type": ptype, "size": 700.0, "beds": beds}


def test_clean_sales_bands_and_trims_outliers():
    df = pl.DataFrame([
        _sale_row("Al Jaddaf", "Apartments", "1", 1_400_000, 700, date(2026, 6, 1)),  # ~2000 psf
        _sale_row("Al Jaddaf", "Apartments", "0", 800_000, 400, date(2026, 6, 1)),     # studio
        _sale_row("Al Jaddaf", "Apartments", "1", 135_000_000, 1000, date(2026, 6, 1)),  # 135k psf outlier
        _sale_row("Al Jaddaf", "Apartments", "1", 0, 700, date(2026, 6, 1)),            # bad price
    ])
    out = clean_sales(df)
    assert out.height == 2  # outlier + bad price dropped
    assert set(out["bed_band"]) == {"1BR", "studio"}
    assert out["property_type"].unique().to_list() == ["apartments"]


def test_clean_rents_dedups():
    r = _rent_row("Al Jaddaf", "Apartments", "1", 70_000, date(2026, 6, 1))
    df = pl.DataFrame([r, dict(r), _rent_row("Al Jaddaf", "Apartments", "1", 100, date(2026, 6, 1))])
    out = clean_rents(df)
    assert out.height == 1  # duplicate collapsed, implausible 100 AED dropped


def test_yield_and_appreciation():
    today = date(2026, 6, 30)
    # straddle the 12-month midpoint (~185d ago) so the halves are early vs late
    early, late = today - timedelta(days=300), today - timedelta(days=30)
    sales = pl.DataFrame(
        # 1BR Al Jaddaf: early median 1.0M, late median 1.1M -> 10% over ~half a 12m window
        [_sale_row("Al Jaddaf", "apartments", "1", 1_000_000, 700, early) for _ in range(4)]
        + [_sale_row("Al Jaddaf", "apartments", "1", 1_100_000, 700, late) for _ in range(4)]
    ).with_columns(pl.col("price").truediv(pl.col("size")).alias("psf"),
                   pl.lit("1BR").alias("bed_band"), pl.lit("medium").alias("size_band"))
    rents = pl.DataFrame(
        [_rent_row("Al Jaddaf", "apartments", "1", 80_000, late) for _ in range(6)]
    ).with_columns(pl.lit("1BR").alias("bed_band"), pl.lit("medium").alias("size_band"))

    cohorts = build_cohort_metrics(sales, rents, months=12, today=today, opex=0.2, min_half=3)
    row = cohorts.row(0, named=True)
    # gross yield = 80k / median sale price (1.05M) ~ 0.0762
    assert abs(row["gross_yield"] - 80_000 / 1_050_000) < 1e-6
    assert abs(row["net_yield"] - row["gross_yield"] * 0.8) < 1e-9
    # appreciation: (1.1/1.0)^(24/12) - 1 = 1.1^2 - 1 = 0.21
    assert abs(row["ann_appr"] - 0.21) < 1e-6
    assert not row["thin"]  # 8 sales, 6 rents

    ranking = rank_microlocalities(cohorts)
    assert ranking.height == 1 and ranking.row(0, named=True)["microlocality"] == "Al Jaddaf"


def test_month_buckets_count_and_edges():
    b = month_buckets(years=4, today=date(2026, 6, 23))
    assert len(b) == 48
    assert b[-1] == ("2026-06", "2026-06-01", "2026-06-30")   # current month
    assert b[0][0] == "2022-07"                                # 48 months back
    assert b[0][1] == "2022-07-01" and b[0][2] == "2022-07-31"


def test_appreciation_from_history_recovers_trend():
    # 24 months of 1BR sales at +1%/month psf (price rises, size fixed at 1000 sqft)
    rows = []
    for k in range(24):
        total = 2024 * 12 + k  # months from Jan 2024
        y, m = divmod(total, 12)
        d = date(y, m + 1, 15)
        psf = 1000 * (1.01 ** k)
        for j in range(8):  # >= min_per_month; distinct rows (vary building) so dedup keeps them
            rows.append({"microlocality": "Test", "purpose": "for-sale", "date": d,
                         "price": psf * 1000, "area": f"Test Tower {j}",
                         "property_type": "apartments", "size": 1000.0, "beds": "1"})
    appr = appreciation_from_history(pl.DataFrame(rows), windows=(12,), min_per_month=5)
    row = appr.row(0, named=True)
    # 12 months of +1%/mo compounding -> (1.01^12)^(12/12) - 1 ~= 12.7%
    assert abs(row["ann_appr_12m"] - (1.01 ** 12 - 1)) < 0.005
    assert row["cagr"] is not None and row["months_covered"] == 24
