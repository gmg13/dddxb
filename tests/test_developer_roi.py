"""Tests for developer attribution + per-developer ROI analysis."""

from __future__ import annotations

import polars as pl

from dddxb.analysis import developer_roi as dr
from dddxb.dashboard import samples


def test_developer_tier_lookup():
    assert samples.developer_tier("Emaar") == 1
    assert samples.developer_tier("Binghatti") == 2
    assert samples.developer_tier("Unknown Co") is None
    assert samples.developer_tier(None) is None


def test_community_fallback_only_for_single_dev_communities():
    # branded token wins regardless of community
    assert samples.developer_for("Azizi Venice", None, community="Dubai South") == "Azizi"
    # single-developer community fallback when nothing branded matches
    assert samples.developer_for("Mogul", "Mogul", community="Discovery Gardens") == "Nakheel"
    assert samples.developer_for("Rosewell", None, community="Town Square") == "Nshama"
    # mixed community: no blanket fallback
    assert samples.developer_for("Random Tower", None, community="Motor City") is None
    # community-name tokens no longer false-positive (JVC -> Nakheel bug)
    assert samples.developer_for("JVC District 10", None) is None


def test_target_microlocalities_exactly_n_distinct():
    rank = pl.DataFrame({
        "microlocality": [f"m{i}" for i in range(1, 13)],
        # m1..m5 lead net_yield; m4..m8 lead appreciation (overlap m4,m5)
        "net_yield": [0.09, 0.08, 0.07, 0.065, 0.06, 0.05, 0.04, 0.03,
                      0.02, 0.01, 0.005, 0.001],
        "ann_appr": [0.01, 0.02, 0.03, 0.40, 0.39, 0.38, 0.37, 0.36,
                     None, None, None, None],
        "total_return": [0.10, 0.10, 0.10, 0.45, 0.44, 0.43, 0.41, 0.39,
                         0.20, 0.15, 0.12, 0.05],
    })
    out = dr.target_microlocalities(rank, n=10)
    assert len(out) == 10
    assert len(set(out)) == 10
    assert {"m1", "m6"} <= set(out)  # top yield + top appreciation both included


def test_developer_table_bed_matched_yield_and_volume_floor():
    sales = pl.DataFrame({
        "microlocality": ["X"] * 8,
        "developer": ["A"] * 5 + ["B"] * 3,  # B below min_vol
        "bed_band": ["1BR"] * 8,
        "price": [1_000_000.0] * 5 + [800_000.0] * 3,
    })
    rents = pl.DataFrame({
        "microlocality": ["X"] * 4,
        "bed_band": ["1BR"] * 4,
        "price": [55_000.0, 60_000.0, 60_000.0, 65_000.0],  # median 60k
    })
    t = dr.developer_table(sales, rents, "X", min_vol=4)
    assert t["developer"].to_list() == ["A"]  # B dropped (3 < 4)
    row = t.row(0, named=True)
    # gross = 60k / 1.0M = 0.06; net = 0.06 * 0.8 = 0.048
    assert abs(row["net_yield"] - 0.048) < 1e-9
    assert abs(row["est_annual_rent"] - 60_000) < 1e-6
    assert row["n_sales"] == 5


def test_developer_examples_names_top_building_and_yield():
    sales = pl.DataFrame({
        "microlocality": ["X"] * 7,
        "developer": ["A"] * 7,
        # Big Tower: 5 sales @ 1BR; Small Tower: 2 sales @ 1BR
        "area": ["X -> Dev A -> Big Tower"] * 5 + ["X -> Dev A -> Small Tower"] * 2,
        "bed_band": ["1BR"] * 7,
        "price": [1_000_000.0] * 5 + [900_000.0] * 2,
    })
    rents = pl.DataFrame({
        "microlocality": ["X"] * 3,
        "bed_band": ["1BR"] * 3,
        "price": [58_000.0, 60_000.0, 62_000.0],  # median 60k
    })
    ex = dr.developer_examples(sales, rents, "X", "A", n_buildings=2)
    buildings = ex["building"].to_list()
    assert "Big Tower" in buildings and "Small Tower" in buildings
    big = ex.filter(pl.col("building") == "Big Tower").row(0, named=True)
    # 60k / 1.0M * 0.8 = 0.048
    assert abs(big["net_yield"] - 0.048) < 1e-9
    assert abs(big["bed_rent"] - 60_000) < 1e-6
    assert big["n"] == 5


def test_developer_examples_empty_when_developer_absent():
    sales = pl.DataFrame({
        "microlocality": ["X"], "developer": ["A"], "area": ["X -> D -> T"],
        "bed_band": ["1BR"], "price": [1_000_000.0],
    })
    rents = pl.DataFrame({"microlocality": ["X"], "bed_band": ["1BR"], "price": [60_000.0]})
    assert dr.developer_examples(sales, rents, "X", "Nobody").is_empty()


def test_select_four_top_then_constrained_bluechips():
    table = pl.DataFrame({
        "microlocality": ["X"] * 5,
        "developer": ["D1", "D2", "D3", "D4", "D5"],
        "tier": [None, 1, 2, 1, 2],
        "n_sales": [10, 10, 10, 10, 10],
        "dubai_props": [2, 100, 50, 3, 200],  # D4 fails the >=4 gate
        "med_price": [1_000_000.0] * 5,
        "est_annual_rent": [70_000.0] * 5,
        "gross_yield": [0.07, 0.06, 0.05, 0.04, 0.03],
        "net_yield": [0.07, 0.06, 0.05, 0.04, 0.03],
    })
    out = dr.select_four(table)
    rows = {r["developer"]: r["role"] for r in out.iter_rows(named=True)}
    assert rows["D1"] == "top" and rows["D2"] == "top"  # best 2 by net_yield
    assert rows["D3"] == "blue-chip" and rows["D5"] == "blue-chip"  # tier1/2 & >=4 props
    assert "D4" not in rows  # excluded: only 3 Dubai props
