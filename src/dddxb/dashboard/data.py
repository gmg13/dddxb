"""Cached data loaders for the dashboard.

Everything here reads parquet written by the pipeline (``python -m dddxb.features``
and the ``--history`` ingest). For a deployed app where the live ``data/`` tree is
absent, loaders fall back to a small committed snapshot in ``data/published/``
(refresh it with ``python scripts/publish_data.py``). Loaders return ``None`` when
neither is present so the UI can show the exact command to run instead of a traceback.

Frames are returned as polars; convert to pandas at the Plotly/Streamlit boundary.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import streamlit as st

from dddxb.clean.transactions import INTERIM_DIR, PROCESSED_DIR
from dddxb.features.metrics import monthly_psf

HISTORY_YEARS = 4
PUBLISHED_DIR = Path("data/published")


def _resolve(primary: Path) -> Path | None:
    """Live pipeline output if present, else the published snapshot, else None."""
    if primary.exists():
        return primary
    snapshot = PUBLISHED_DIR / primary.name
    return snapshot if snapshot.exists() else None


def _ranking_path(window: int) -> Path:
    return Path(PROCESSED_DIR) / f"microlocality_ranking_last{window}m.parquet"


def _cohort_path(window: int) -> Path:
    return Path(PROCESSED_DIR) / f"cohort_metrics_last{window}m.parquet"


def _history_path(years: int = HISTORY_YEARS) -> Path:
    return Path(INTERIM_DIR) / f"bayut_sale_history_{years}y.parquet"


def available_windows() -> list[int]:
    """Windows (months) for which a ranking is available, longest first."""
    return [w for w in (24, 12, 6, 3, 1) if _resolve(_ranking_path(w)) is not None]


@st.cache_data(show_spinner=False)
def load_ranking(window: int) -> pl.DataFrame | None:
    path = _resolve(_ranking_path(window))
    return pl.read_parquet(path) if path else None


@st.cache_data(show_spinner=False)
def load_cohorts(window: int) -> pl.DataFrame | None:
    path = _resolve(_cohort_path(window))
    return pl.read_parquet(path) if path else None


@st.cache_data(show_spinner=False)
def load_clean_sales(window: int) -> pl.DataFrame | None:
    path = _resolve(Path(PROCESSED_DIR) / f"for_sale_clean_last{window}m.parquet")
    return pl.read_parquet(path) if path else None


@st.cache_data(show_spinner=False)
def load_clean_rents(window: int) -> pl.DataFrame | None:
    path = _resolve(Path(PROCESSED_DIR) / f"for_rent_clean_last{window}m.parquet")
    return pl.read_parquet(path) if path else None


@st.cache_data(show_spinner=False)
def load_monthly_psf(years: int = HISTORY_YEARS, min_per_month: int = 5) -> pl.DataFrame | None:
    """Monthly median AED/sqft series per microlocality from the history sample."""
    path = _resolve(_history_path(years))
    if not path:
        return None
    return monthly_psf(pl.read_parquet(path), min_per_month=min_per_month)


def history_available(years: int = HISTORY_YEARS) -> bool:
    return _resolve(_history_path(years)) is not None
