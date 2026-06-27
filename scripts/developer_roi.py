"""Generate the 'top developers by rent & ROI per microlocality' report.

Reads the cleaned 12-month sales/rents + the microlocality ranking, infers developers
from building names, and writes a markdown + CSV report under ``reports/``.

    uv run python scripts/developer_roi.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dddxb.analysis.developer_roi import (
    developer_examples,
    developer_table,
    dubai_property_counts,
    select_four,
    tag_developers,
    target_microlocalities,
)
from dddxb.dashboard.data import load_clean_rents, load_clean_sales, load_ranking

WINDOW = 12
OUT_DIR = Path("reports")
THIN_SALES = 50  # microlocalities below this get a low-confidence flag on the sale side


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.2f}%"


def _aed(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}"


def _k(x: float | None) -> str:
    """Compact AED: 1,020,000 -> '1.02M', 816,000 -> '816k'."""
    if x is None:
        return "—"
    return f"{x / 1_000_000:.2f}M" if x >= 1_000_000 else f"{x / 1_000:.0f}k"


def _example_line(developer: str, ex: pl.DataFrame) -> str:
    """One markdown bullet naming the buildings behind a developer's yield."""
    parts = [
        f"**{r['building']}** ({r['bed_band']}) {_k(r['med_price'])} → ~{_k(r['bed_rent'])} "
        f"rent → **{_pct(r['net_yield'])}**"
        for r in ex.iter_rows(named=True)
    ]
    return f"- _{developer}:_ " + " · ".join(parts)


def main() -> None:
    ranking = load_ranking(WINDOW)
    sales = load_clean_sales(WINDOW)
    rents = load_clean_rents(WINDOW)
    if ranking is None or sales is None or rents is None:
        raise SystemExit(
            "Missing inputs. Run: uv run python -m dddxb.features --months 12"
        )

    sales = tag_developers(sales)  # rents need no tagging — yield uses community rents
    dubai_counts = dubai_property_counts(sales)

    targets = target_microlocalities(ranking, n=10)
    rk = ranking.filter(pl.col("microlocality").is_in(targets))
    order = rk.sort("total_return", descending=True)["microlocality"].to_list()
    rk_by = {r["microlocality"]: r for r in rk.iter_rows(named=True)}

    all_rows: list[pl.DataFrame] = []
    lines: list[str] = [
        "# Top developers by rent & ROI, per high-value microlocality",
        "",
        "**Universe:** top 5 microlocalities by net rental yield ∪ top 5 by capital "
        "appreciation, backfilled by total return to a precise 10.",
        "",
        "**ROI = net rental yield**, bed-band matched: community median rent(bed) ÷ "
        "developer median price(bed) × (1 − 20% opex), weighted by the developer's bed "
        "mix. Rent is location/bed driven (the sale stock — new launches — and the rent "
        "stock — older completed buildings — are largely different buildings), so the "
        "yield differentiator across developers is mostly **price**. **Top** = best 2 "
        "developers by net yield (≥4 sales). **Blue-chip** = 2 additional established "
        "names (tier 1/2, ≥4 properties Dubai-wide) for recognizable validation.",
        "",
        "**Caveats:** developers are *inferred from building names* (DLD has no developer "
        "field); single-developer master communities (Town Square→Nshama, The "
        "Greens→Emaar, Discovery Gardens/International City→Nakheel) use a community "
        "fallback. `est_annual_rent` is the community rent for the developer's bed mix, "
        "not its own realized rent. Appreciation is per-microlocality, not per-developer. "
        "Thin-sales microlocalities are flagged.",
        "",
        "Under each table, _examples_ name the developer's highest-volume buildings with "
        "the building's **own** median sale price vs the community rent for that bed — "
        "these bracket the bed-mix-weighted developer figure rather than equalling it.",
        "",
    ]
    example_rows: list[pl.DataFrame] = []

    for ml in order:
        meta = rk_by[ml]
        thin = (meta.get("n_sales") or 0) < THIN_SALES
        flag = "  ⚠️ **thin sales — low confidence**" if thin else ""
        r2 = meta.get("appr_r2")
        r2_txt = f"{r2:.2f}" if r2 is not None else "n/a"
        lines += [
            f"## {ml}{flag}",
            "",
            f"Net yield **{_pct(meta['net_yield'])}** · appreciation "
            f"**{_pct(meta.get('ann_appr'))}** (R²={r2_txt}) · total return "
            f"**{_pct(meta['total_return'])}** · {meta.get('n_sales')} sales / "
            f"{meta.get('n_rents')} rents",
            "",
        ]

        tbl = developer_table(sales, rents, ml, dubai_counts=dubai_counts)
        chosen = select_four(tbl)
        if chosen.is_empty():
            lines += ["_No developer cleared the volume floor (≥4 sales)._", ""]
            continue

        all_rows.append(chosen)
        lines += [
            "| Developer | Tier | Role | Sales | Dubai props | Med price (AED) "
            "| Est. annual rent (AED) | Net yield |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for row in chosen.iter_rows(named=True):
            tier = f"T{row['tier']}" if row["tier"] is not None else "—"
            lines.append(
                f"| {row['developer']} | {tier} | {row['role']} | {row['n_sales']} "
                f"| {row['dubai_props'] or '—'} | {_aed(row['med_price'])} | "
                f"{_aed(row['est_annual_rent'])} | {_pct(row['net_yield'])} |"
            )
        lines.append("")
        lines.append("Examples:")
        for dev in chosen["developer"].to_list():
            ex = developer_examples(sales, rents, ml, dev)
            if not ex.is_empty():
                example_rows.append(ex)
                lines.append(_example_line(dev, ex))
        lines.append("")

    OUT_DIR.mkdir(exist_ok=True)
    md_path = OUT_DIR / "developer_roi_by_microlocality.md"
    csv_path = OUT_DIR / "developer_roi_by_microlocality.csv"
    ex_path = OUT_DIR / "developer_roi_examples.csv"
    md_path.write_text("\n".join(lines))
    if all_rows:
        pl.concat(all_rows, how="vertical").write_csv(csv_path)
    if example_rows:
        pl.concat(example_rows, how="vertical").write_csv(ex_path)

    print(f"Wrote {md_path} ({len(order)} microlocalities)")
    print(f"Wrote {csv_path}")
    print(f"Wrote {ex_path}")


if __name__ == "__main__":
    main()
