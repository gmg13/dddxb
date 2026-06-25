"""Clean & standardize ingested transactions into analysis-ready frames.

Reads the ingest interim parquet (canonical columns: microlocality, purpose,
date, price, area, property_type, size, beds) and produces processed sale/rent
frames with normalized property type, bed band, and size band, outliers trimmed
and exact duplicates dropped. Cohort = (microlocality, property_type, bed_band)
per ``docs/methodology.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

INTERIM_DIR = Path("data/interim")
PROCESSED_DIR = Path("data/processed")

# Plausibility bounds (AED/sqft) — trims data-entry errors and bulk/portfolio
# deals that would distort medians (e.g. a 135M whole-floor sale).
SALE_PSF_MIN, SALE_PSF_MAX = 200.0, 12000.0
# Sanity bounds on annual rent (AED) to drop monthly-vs-annual mistakes etc.
RENT_MIN, RENT_MAX = 5_000.0, 5_000_000.0

COHORT_KEYS = ("microlocality", "property_type", "bed_band")


def _with_bands(df: pl.DataFrame) -> pl.DataFrame:
    """Add normalized property_type, bed_band, size_band."""
    beds_i = pl.col("beds").cast(pl.Int64, strict=False)
    return df.with_columns(
        pl.col("property_type").str.to_lowercase().str.strip_chars().alias("property_type"),
        pl.when(beds_i == 0).then(pl.lit("studio"))
        .when(beds_i == 1).then(pl.lit("1BR"))
        .when(beds_i == 2).then(pl.lit("2BR"))
        .when(beds_i == 3).then(pl.lit("3BR"))
        .when(beds_i >= 4).then(pl.lit("4BR+"))
        .otherwise(pl.lit("unknown")).alias("bed_band"),
        pl.when(pl.col("size") < 500).then(pl.lit("<500"))
        .when(pl.col("size") < 800).then(pl.lit("500-800"))
        .when(pl.col("size") < 1200).then(pl.lit("800-1200"))
        .when(pl.col("size") < 2000).then(pl.lit("1200-2000"))
        .when(pl.col("size") >= 2000).then(pl.lit("2000+"))
        .otherwise(pl.lit("unknown")).alias("size_band"),
    )


def clean_sales(df: pl.DataFrame) -> pl.DataFrame:
    """Drop non-positive price/size, trim AED/sqft outliers, dedup, add bands."""
    out = (
        df.filter((pl.col("price") > 0) & (pl.col("size") > 0))
        .with_columns((pl.col("price") / pl.col("size")).alias("psf"))
        .filter(pl.col("psf").is_between(SALE_PSF_MIN, SALE_PSF_MAX))
        .unique()
    )
    return _with_bands(out)


def clean_rents(df: pl.DataFrame) -> pl.DataFrame:
    """Drop implausible annual rents, dedup, add bands."""
    out = df.filter(pl.col("price").is_between(RENT_MIN, RENT_MAX)).unique()
    return _with_bands(out)


def clean(
    months: int = 6,
    *,
    interim_dir: Path = INTERIM_DIR,
    out_dir: Path = PROCESSED_DIR,
) -> dict[str, Path]:
    """Clean the sale + rent interim parquet for a window; write processed parquet."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for purpose, cleaner in (("for_sale", clean_sales), ("for_rent", clean_rents)):
        src = interim_dir / f"bayut_{purpose}_last{months}m.parquet"
        if not src.exists():
            log.warning("missing %s; skipping", src)
            continue
        raw = pl.read_parquet(src)
        cleaned = cleaner(raw)
        dst = out_dir / f"{purpose}_clean_last{months}m.parquet"
        cleaned.write_parquet(dst)
        log.info("cleaned %s: %d -> %d rows -> %s", purpose, raw.height, cleaned.height, dst)
        results[purpose] = dst
    return results
