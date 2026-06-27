"""Top developers by rent & net ROI, per high-value microlocality.

The DLD transactions carry no developer field; developers are inferred from the
``area`` hierarchy via :func:`dddxb.dashboard.samples.developer_for` (cleaned keyword
map + curated single-developer-community fallback). The headline metric is **net
rental yield** = median annual rent / median sale price x (1 - opex), matching the
repo's microlocality ranking. Appreciation is a per-microlocality figure (DLD data
cannot reliably attribute it by developer), so it is reported as context, not ranked.

Pure functions over the cleaned ``for_{sale,rent}_clean_last{N}m`` parquet — no
re-ingest. See ``scripts/developer_roi.py`` for the report writer.
"""

from __future__ import annotations

import polars as pl

from dddxb.dashboard.samples import _with_parts, developer_for, developer_tier, split_area

OPEX = 0.20  # operating-cost haircut on gross yield, matching the ranking's net_yield


def tag_developers(df: pl.DataFrame) -> pl.DataFrame:
    """Add a ``developer`` column inferred from ``area`` (+ community fallback)."""

    def _tag(row: dict) -> str | None:
        _community, development, building = split_area(row["area"])
        return developer_for(development, building, community=row["microlocality"])

    return df.with_columns(
        pl.struct(["area", "microlocality"])
        .map_elements(_tag, return_dtype=pl.Utf8)
        .alias("developer")
    )


def dubai_property_counts(sales_tagged: pl.DataFrame) -> pl.DataFrame:
    """Dubai-wide tagged sale count per developer (the '>=4 properties' gate)."""
    return (
        sales_tagged.filter(pl.col("developer").is_not_null())
        .group_by("developer")
        .agg(pl.len().alias("dubai_props"))
    )


def target_microlocalities(ranking: pl.DataFrame, n: int = 10) -> list[str]:
    """Top 5 by net yield ∪ top 5 by appreciation, backfilled by total return to ``n``."""
    top_yield = ranking.sort("net_yield", descending=True).head(5)["microlocality"].to_list()
    top_appr = (
        ranking.filter(pl.col("ann_appr").is_not_null())
        .sort("ann_appr", descending=True)
        .head(5)["microlocality"]
        .to_list()
    )
    out: list[str] = []
    for m in top_yield + top_appr:
        if m not in out:
            out.append(m)
    if len(out) < n:
        for m in ranking.sort("total_return", descending=True)["microlocality"].to_list():
            if m not in out:
                out.append(m)
            if len(out) >= n:
                break
    return out[:n]


def community_rent_by_bed(rents: pl.DataFrame, microlocality: str) -> pl.DataFrame:
    """Median annual rent per bedroom band across the whole community.

    Rent is location/bed driven, not builder driven, so the community-level rent for a
    bed band is the right basis for a developer's *new* sale stock — which typically has
    no rental track record yet (the sale and rent markets are different buildings)."""
    return (
        rents.filter(pl.col("microlocality") == microlocality)
        .group_by("bed_band")
        .agg(pl.col("price").median().alias("bed_rent"))
    )


def developer_table(
    sales_tagged: pl.DataFrame,
    rents: pl.DataFrame,
    microlocality: str,
    *,
    min_vol: int = 4,
    dubai_counts: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Per-developer estimated rent & net-yield table for one microlocality.

    Net yield is **bed-band matched**: for each developer, community median rent(bed) ÷
    developer median price(bed), weighted by the developer's sale count per bed. This
    lets new-launch developers (no rentals yet) still be scored. Keeps developers with
    >= ``min_vol`` sales. Columns: microlocality, developer, tier, n_sales, dubai_props,
    med_price, est_annual_rent, gross_yield, net_yield (sorted net_yield desc).
    """
    cols = ["microlocality", "developer", "tier", "n_sales", "dubai_props",
            "med_price", "est_annual_rent", "gross_yield", "net_yield"]
    s = sales_tagged.filter(
        (pl.col("microlocality") == microlocality) & pl.col("developer").is_not_null()
    )
    rent_bed = community_rent_by_bed(rents, microlocality)
    if s.is_empty() or rent_bed.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    # Per developer × bed band: median sale price + transaction weight.
    dev_bed = (
        s.group_by(["developer", "bed_band"])
        .agg(pl.len().alias("n"), pl.col("price").median().alias("bed_price"))
        .join(rent_bed, on="bed_band", how="inner")
        .with_columns((pl.col("bed_rent") / pl.col("bed_price")).alias("bed_gross"))
    )
    if dev_bed.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    # Aggregate beds → developer, weighting price / rent / yield by sale count.
    t = (
        dev_bed.with_columns(
            (pl.col("bed_gross") * pl.col("n")).alias("_wg"),
            (pl.col("bed_rent") * pl.col("n")).alias("_wr"),
            (pl.col("bed_price") * pl.col("n")).alias("_wp"),
        )
        .group_by("developer")
        .agg(
            pl.col("n").sum().alias("n_sales"),
            pl.col("_wg").sum(),
            pl.col("_wr").sum(),
            pl.col("_wp").sum(),
        )
        .filter(pl.col("n_sales") >= min_vol)
        .with_columns(
            (pl.col("_wg") / pl.col("n_sales")).alias("gross_yield"),
            (pl.col("_wr") / pl.col("n_sales")).alias("est_annual_rent"),
            (pl.col("_wp") / pl.col("n_sales")).alias("med_price"),
        )
        .with_columns((pl.col("gross_yield") * (1 - OPEX)).alias("net_yield"))
        .with_columns(
            pl.lit(microlocality).alias("microlocality"),
            pl.col("developer")
            .map_elements(developer_tier, return_dtype=pl.Int64)
            .alias("tier"),
        )
    )
    if dubai_counts is not None:
        t = t.join(dubai_counts, on="developer", how="left")
    else:
        t = t.with_columns(pl.lit(None, dtype=pl.UInt32).alias("dubai_props"))
    return t.select(cols).sort("net_yield", descending=True)


def developer_examples(
    sales_tagged: pl.DataFrame,
    rents: pl.DataFrame,
    microlocality: str,
    developer: str,
    *,
    n_buildings: int = 2,
) -> pl.DataFrame:
    """Concrete buildings backing a developer's headline yield in a microlocality.

    Picks the developer's top ``n_buildings`` distinct buildings by sale volume; for
    each, takes its modal bed band, the median sale price for that building+bed, and the
    matched community rent → implied net yield. These bracket the bed-mix-weighted
    developer figure rather than equalling it. Columns: microlocality, developer,
    building, bed_band, n, med_price, bed_rent, net_yield (net_yield desc).
    """
    cols = ["microlocality", "developer", "building", "bed_band", "n",
            "med_price", "bed_rent", "net_yield"]
    s = _with_parts(
        sales_tagged.filter(
            (pl.col("microlocality") == microlocality) & (pl.col("developer") == developer)
        )
    )
    rent_bed = community_rent_by_bed(rents, microlocality)
    if s.is_empty() or rent_bed.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    top_buildings = (
        s.group_by("building")
        .agg(pl.len().alias("building_n"))
        .sort("building_n", descending=True)
        .head(n_buildings)["building"]
        .to_list()
    )
    # Per building × bed: volume + median price; keep each building's modal bed band.
    per_bed = (
        s.filter(pl.col("building").is_in(top_buildings))
        .group_by(["building", "bed_band"])
        .agg(pl.len().alias("n"), pl.col("price").median().alias("med_price"))
        .sort("n", descending=True)
        .group_by("building", maintain_order=True)
        .first()
    )
    out = (
        per_bed.join(rent_bed, on="bed_band", how="inner")
        .with_columns(
            pl.lit(microlocality).alias("microlocality"),
            pl.lit(developer).alias("developer"),
            (pl.col("bed_rent") / pl.col("med_price") * (1 - OPEX)).alias("net_yield"),
        )
    )
    if out.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})
    return out.select(cols).sort("net_yield", descending=True)


def select_four(table: pl.DataFrame) -> pl.DataFrame:
    """Top 2 developers by net yield + 2 blue-chips (tier 1/2, >=4 Dubai props).

    Tags each row ``role`` in {"top", "blue-chip"}. Returns <=4 rows; fewer if the
    microlocality lacks enough qualifying developers (not padded).
    """
    if table.is_empty():
        return table.with_columns(pl.lit(None, dtype=pl.Utf8).alias("role"))
    table = table.sort("net_yield", descending=True)
    top = table.head(2)
    top_names = top["developer"].to_list()
    blue = (
        table.filter(
            (~pl.col("developer").is_in(top_names))
            & pl.col("tier").is_in([1, 2])
            & (pl.col("dubai_props") >= 4)
        )
        .head(2)
    )
    top = top.with_columns(pl.lit("top").alias("role"))
    blue = blue.with_columns(pl.lit("blue-chip").alias("role"))
    return pl.concat([top, blue], how="vertical")
