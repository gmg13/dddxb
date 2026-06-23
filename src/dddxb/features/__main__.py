"""Run the clean -> features pipeline and write ranked outputs.

    uv run python -m dddxb.features --months 6      # uses data/interim/*last6m
    uv run python -m dddxb.features --months 12

Reads the ingest interim parquet, cleans it, builds cohort metrics + a
microlocality ranking, writes them to data/processed/, and prints the top rows.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import polars as pl

from dddxb.clean.transactions import PROCESSED_DIR, clean
from dddxb.features.metrics import build_cohort_metrics, rank_microlocalities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dddxb.features", description=__doc__)
    parser.add_argument("--months", type=int, default=6, help="window matching the ingest suffix")
    parser.add_argument("--top", type=int, default=15, help="rows to print")
    parser.add_argument("--min-cohort", type=int, default=5)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cleaned = clean(args.months)
    if "for_sale" not in cleaned or "for_rent" not in cleaned:
        parser.error("need both cleaned sale and rent frames; run the ingest first")
    sales = pl.read_parquet(cleaned["for_sale"])
    rents = pl.read_parquet(cleaned["for_rent"])

    cohorts = build_cohort_metrics(sales, rents, months=args.months, min_cohort=args.min_cohort)
    ranking = rank_microlocalities(cohorts)

    out_dir = Path(PROCESSED_DIR)
    cohorts.write_parquet(out_dir / f"cohort_metrics_last{args.months}m.parquet")
    ranking.write_parquet(out_dir / f"microlocality_ranking_last{args.months}m.parquet")

    pct = ["gross_yield", "net_yield", "ann_appr", "total_return"]
    with pl.Config(tbl_rows=args.top, tbl_cols=-1, float_precision=3):
        print(f"\n=== Microlocality ranking (last {args.months}m, non-thin cohorts) ===")
        print(ranking.with_columns([(pl.col(c) * 100).round(2) for c in pct]).head(args.top))
        print(f"\n=== Top cohorts by total return (last {args.months}m) ===")
        print(
            cohorts.filter(~pl.col("thin"))
            .with_columns([(pl.col(c) * 100).round(2) for c in pct])
            .head(args.top)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
