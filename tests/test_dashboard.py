"""Smoke tests for the dashboard data layer + chat plumbing (no Streamlit runtime)."""

from __future__ import annotations

import polars as pl

from dddxb.dashboard import chat, samples


def test_build_system_prompt_embeds_tables():
    ranking = pl.DataFrame({"microlocality": ["Al Jaddaf"], "net_yield": [0.03],
                            "total_return": [0.05]})
    cohorts = pl.DataFrame({"microlocality": ["Al Jaddaf"], "property_type": ["apartments"],
                            "bed_band": ["1BR"], "thin": [False]})
    prompt = chat.build_system_prompt(ranking, cohorts)
    assert "Al Jaddaf" in prompt
    assert "MICROLOCALITY RANKING" in prompt and "COHORT METRICS" in prompt


def test_build_system_prompt_handles_missing():
    prompt = chat.build_system_prompt(None, None)
    assert "run the pipeline first" in prompt


def test_trend_text_filters_and_reports():
    monthly = pl.DataFrame({
        "microlocality": ["Dubai Marina", "Dubai Marina", "Business Bay"],
        "ym": ["2025-01", "2025-02", "2025-01"],
        "n": [10, 12, 8], "psf": [2000.0, 2050.0, 1800.0],
    })
    out = chat._trend_text(monthly, "Dubai Marina")
    assert "2025-01" in out and "2025-02" in out and "2050" in out
    assert "Business Bay" not in out

    missing = chat._trend_text(monthly, "Nowhere")
    assert "No history" in missing and "Dubai Marina" in missing  # lists available

    assert "run the --history" in chat._trend_text(None, "x")


def test_api_key_missing_raises(monkeypatch):
    monkeypatch.setattr(chat, "api_key", lambda: None)
    try:
        chat.answer([{"role": "user", "content": "hi"}], ranking=None, cohorts=None, monthly=None)
    except chat.ChatUnavailable as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ChatUnavailable")


def test_split_area_segments():
    assert samples.split_area("Business Bay -> Al Habtoor City -> Amna") == (
        "Business Bay", "Al Habtoor City", "Amna")
    # 4 segments: development is 2nd, building is last
    assert samples.split_area("JLT -> Cluster P -> Armada -> Tower 3") == (
        "JLT", "Cluster P", "Tower 3")
    # arrow-less: development falls back to the single segment
    assert samples.split_area("Stella Maris") == ("Stella Maris", "Stella Maris", "Stella Maris")
    assert samples.split_area(None) == (None, None, None)


def test_developer_for_tags_known_and_unknown():
    assert samples.developer_for("DAMAC Towers by Paramount", "Tower D") == "DAMAC"
    assert samples.developer_for("Creek Bay", "Tower 1") == "Emaar"
    assert samples.developer_for("Binghatti Aquarise", "") == "Binghatti"
    assert samples.developer_for("Some Standalone Building", "Block A") is None


def _sale(area, beds, price, size, d):
    return {"microlocality": "Business Bay", "purpose": "for-sale", "date": d, "price": price,
            "area": area, "property_type": "apartments", "size": size, "psf": price / size,
            "bed_band": beds, "size_band": "medium"}


def test_sample_properties_ranks_by_volume_and_implied_yield():
    from datetime import date
    big = [_sale("Business Bay -> Peninsula -> Peninsula One", "1BR", 1_800_000, 700, date(2026, 6, 1))
           for _ in range(8)]
    small = [_sale("Business Bay -> Tiny Tower -> Tiny Tower", "1BR", 1_500_000, 700, date(2026, 5, 1))
             for _ in range(2)]  # below min_dev_sales
    sales = pl.DataFrame(big + small)
    rents = pl.DataFrame([
        {"microlocality": "Business Bay", "purpose": "for-rent", "date": date(2026, 6, 1),
         "price": 120_000, "area": "Business Bay -> Peninsula -> Peninsula One",
         "property_type": "apartments", "size": 700.0, "bed_band": "1BR", "size_band": "medium"}
    ])
    out = samples.sample_properties(sales, rents, "Business Bay", n_devs=5, per_dev=3, min_dev_sales=5)
    assert set(out["development"].unique().to_list()) == {"Peninsula"}  # small dev dropped
    assert out["developer"][0] == "Select Group"
    # implied yield = 120k / 1.8M (stored rounded to 4dp)
    assert abs(out["dev_impl_yield"][0] - 120_000 / 1_800_000) < 1e-3


def test_appreciation_ols_recovers_trend():
    from datetime import date

    from dddxb.features.metrics import appreciation_ols
    rows = []
    for k in range(24):
        total = 2024 * 12 + k
        y, m = divmod(total, 12)
        psf = 1000 * (1.01 ** k)
        for j in range(8):
            rows.append({"microlocality": "Test", "purpose": "for-sale", "date": date(y, m + 1, 15),
                         "price": psf * 1000, "area": f"Test Tower {j}",
                         "property_type": "apartments", "size": 1000.0, "beds": "1"})
    appr = appreciation_ols(pl.DataFrame(rows))
    row = appr.row(0, named=True)
    assert abs(row["cagr"] - (1.01 ** 12 - 1)) < 0.01  # +1%/mo -> ~12.7% annualized
    assert row["appr_r2"] > 0.99  # clean exponential -> near-perfect log-linear fit
