"""Refresh the committed dashboard snapshot in ``data/published/``.

The deployed app has no live ``data/`` tree, so it falls back to a small snapshot of
the derived outputs the dashboard needs (rankings, cohort metrics, cleaned
transactions for the Validate tab, and the 4y price-history sample). Raw microdata is
never published. Run after re-running the pipeline:

    uv run python -m dddxb.features --months 12
    uv run python scripts/publish_data.py

Then commit ``data/published/`` and push — Streamlit Cloud redeploys automatically.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from dddxb.clean.transactions import INTERIM_DIR, PROCESSED_DIR
from dddxb.dashboard.data import HISTORY_YEARS, PUBLISHED_DIR

WINDOW = 12  # the window the published app serves

SOURCES = [
    Path(PROCESSED_DIR) / f"microlocality_ranking_last{WINDOW}m.parquet",
    Path(PROCESSED_DIR) / f"cohort_metrics_last{WINDOW}m.parquet",
    Path(PROCESSED_DIR) / f"for_sale_clean_last{WINDOW}m.parquet",
    Path(PROCESSED_DIR) / f"for_rent_clean_last{WINDOW}m.parquet",
    Path(INTERIM_DIR) / f"bayut_sale_history_{HISTORY_YEARS}y.parquet",
]


def main() -> int:
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    missing = [str(p) for p in SOURCES if not p.exists()]
    if missing:
        raise SystemExit("missing pipeline outputs (run features first):\n  " + "\n  ".join(missing))
    total = 0
    for src in SOURCES:
        dst = PUBLISHED_DIR / src.name
        shutil.copy2(src, dst)
        total += dst.stat().st_size
        print(f"published {dst}  ({dst.stat().st_size / 1024:.0f} KB)")
    print(f"\n{len(SOURCES)} files, {total / 1024 / 1024:.1f} MB total → {PUBLISHED_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
