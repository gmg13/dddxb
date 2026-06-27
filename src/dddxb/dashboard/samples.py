"""Big-developer sample properties, to validate the headline numbers.

The cleaned transactions keep the development hierarchy in ``area`` as
``"Community -> Master-development -> Building"``. We surface, per microlocality,
the highest-transaction-volume developments (big branded projects dominate volume;
tiny standalone buildings have a handful), tag the developer best-effort by keyword,
and show real transacted units plus an implied gross yield so the ryield/price
figures can be eyeballed against actual buildings.
"""

from __future__ import annotations

import polars as pl

# Major Dubai developers → lowercase tokens found in development/building names.
# Best-effort and extensible; first match wins, so order specific → generic.
# NOTE: community-name tokens (e.g. "jumeirah village", "downtown") are deliberately
# kept OUT of here — they false-positive every third-party building in that community
# onto one developer. Single-developer communities are handled by COMMUNITY_MASTER
# below, applied only as a last-resort fallback once no branded token matched.
DEVELOPER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Emaar": ("emaar", "burj khalifa", "dubai hills", "creek", "beachfront",
              "arabian ranches", "address", "vida", "the cove", "emaar south",
              "elvira", "forte", "act one", "grande", "st. regis", "st regis",
              "park heights", "collective", "mulberry", "executive heights"),
    "DAMAC": ("damac", "paramount", "cavalli", "safa", "akoya", "aykon", "canal heights",
              "zada", "merano", "reva", "prive"),
    "Nakheel": ("nakheel", "palm jumeirah", "the crescent", "shoreline"),
    "Sobha": ("sobha", "hartland", "creek vistas", "one park avenue", "the crest", "solis"),
    "Meraas": ("meraas", "city walk", "bluewaters", "port de la mer", "la mer", "jbr",
               "jumeirah beach residence", "central park"),
    "Select Group": ("select", "marina gate", "peninsula", "studio one", "no.9", "jumeirah living"),
    "Binghatti": ("binghatti",),
    "Samana": ("samana",),
    "Azizi": ("azizi", "riviera", "mina by azizi"),
    "Danube": ("danube",),
    "Ellington": ("ellington", "hillgate"),
    "Omniyat": ("omniyat", "the opus", "one palm", "langham"),
    "Dubai Properties": ("executive towers", "bay square", "mudon", "dubai wharf",
                         "manazel", "remraam"),
    "Nshama": ("nshama",),
    "Deyaar": ("deyaar",),
    "Iman": ("by iman",),
    "Vision": ("by vision",),
    "Karma": ("by karma",),
    "Tiger": ("tiger",),
}

# Developer tier (1 = top-tier established, 2 = active mid-size). Gates the "blue-chip"
# additions in the developer-ROI analysis. Curated, best-effort, adjustable.
DEVELOPER_TIER: dict[str, int] = {
    "Emaar": 1, "Nakheel": 1, "DAMAC": 1, "Meraas": 1, "Sobha": 1,
    "Dubai Properties": 1, "Omniyat": 1, "Select Group": 1, "Ellington": 1,
    "Nshama": 1, "Union Properties": 1,
    "Binghatti": 2, "Azizi": 2, "Danube": 2, "Samana": 2, "Tiger": 2,
    "Deyaar": 2, "Iman": 2, "Vision": 2, "Karma": 2,
}

# Single-developer master communities: stock not matched by a branded token above is
# safely attributed to the master developer. Only communities with effectively one
# developer belong here — NOT mixed communities (JVC, Motor City, …) where a blanket
# fallback would over-attribute to the master.
COMMUNITY_MASTER: dict[str, str] = {
    "town square": "Nshama",
    "discovery gardens": "Nakheel",
    "the greens": "Emaar",
    "international city": "Nakheel",
}


def split_area(area: str | None) -> tuple[str | None, str | None, str | None]:
    """``"Community -> Development -> Building"`` → (community, development, building).

    Development is the 2nd segment (fallback: the only/first); building is the last.
    """
    if not area:
        return None, None, None
    parts = [p.strip() for p in str(area).split("->") if p.strip()]
    if not parts:
        return None, None, None
    community = parts[0]
    development = parts[1] if len(parts) >= 2 else parts[0]
    building = parts[-1]
    return community, development, building


def developer_for(
    development: str | None,
    building: str | None = None,
    community: str | None = None,
) -> str | None:
    """Best-effort big-developer tag, or None.

    Branded tokens in development/building win first; if nothing matches and the
    ``community`` is a known single-developer master community, attribute to its
    master developer (COMMUNITY_MASTER) as a last resort.
    """
    text = f"{development or ''} {building or ''}".lower()
    if text.strip():
        for developer, tokens in DEVELOPER_KEYWORDS.items():
            if any(tok in text for tok in tokens):
                return developer
    if community:
        return COMMUNITY_MASTER.get(community.strip().lower())
    return None


def developer_tier(developer: str | None) -> int | None:
    """Tier (1/2) for a developer name, or None if unknown/untiered."""
    return DEVELOPER_TIER.get(developer) if developer else None


def _with_parts(df: pl.DataFrame) -> pl.DataFrame:
    parts = pl.col("area").str.split(" -> ")
    return df.with_columns(
        parts.list.get(1, null_on_oob=True)
        .fill_null(parts.list.get(0, null_on_oob=True))
        .str.strip_chars()
        .alias("development"),
        parts.list.last().str.strip_chars().alias("building"),
    )


def sample_properties(
    sales: pl.DataFrame,
    rents: pl.DataFrame | None,
    microlocality: str,
    *,
    n_devs: int = 5,
    per_dev: int = 3,
    min_dev_sales: int = 5,
) -> pl.DataFrame:
    """Sample transacted units from the top developments in ``microlocality``.

    Returns one row per sample unit with the development-level summary repeated:
    developer, development, dev_n_sales, dev_med_psf, dev_impl_yield (median annual
    rent ÷ median sale price for the development), then building/beds/size/price/
    psf/date for the individual transaction. Empty frame if no data.
    """
    cols = ["developer", "development", "dev_n_sales", "dev_med_psf", "dev_impl_yield",
            "building", "beds", "size", "price", "psf", "date"]
    if sales is None or sales.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    s = _with_parts(sales.filter(pl.col("microlocality") == microlocality))
    if s.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    dev_summary = (
        s.group_by("development")
        .agg(pl.len().alias("dev_n_sales"),
             pl.col("psf").median().alias("dev_med_psf"),
             pl.col("price").median().alias("_med_price"))
        .filter(pl.col("dev_n_sales") >= min_dev_sales)
        .sort("dev_n_sales", descending=True)
        .head(n_devs)
    )

    r = _with_parts(rents.filter(pl.col("microlocality") == microlocality)) if rents is not None \
        else None

    out_rows: list[dict] = []
    for dev in dev_summary.iter_rows(named=True):
        name = dev["development"]
        med_price = dev["_med_price"]
        impl_yield = None
        if r is not None and med_price:
            rr = r.filter(pl.col("development") == name)
            if not rr.is_empty():
                impl_yield = float(rr["price"].median()) / float(med_price)
        units = (s.filter(pl.col("development") == name)
                 .sort("date", descending=True).head(per_dev))
        for u in units.iter_rows(named=True):
            out_rows.append({
                "developer": developer_for(name, u["building"]) or "—",
                "development": name,
                "dev_n_sales": dev["dev_n_sales"],
                "dev_med_psf": round(dev["dev_med_psf"], 0),
                "dev_impl_yield": round(impl_yield, 4) if impl_yield is not None else None,
                "building": u["building"],
                "beds": u.get("bed_band") or u.get("beds"),
                "size": round(u["size"], 0) if u.get("size") is not None else None,
                "price": u["price"],
                "psf": round(u["psf"], 0) if u.get("psf") is not None else None,
                "date": u["date"],
            })
    return pl.DataFrame(out_rows) if out_rows else pl.DataFrame(schema={c: pl.Utf8 for c in cols})
