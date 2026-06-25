"""Acquire DLD open datasets from Dubai Pulse and filter to a recent window.

Workflow per dataset:
  1. ``download_dataset`` streams the full keyless CSV dump into ``data/raw/dld/``
     (cached; skipped on re-run unless ``refresh=True`` — these dumps are large).
  2. ``filter_recent`` uses DuckDB to read the CSV, keep rows within the trailing
     N-month window, and write typed parquet to ``data/interim/``. The full file is
     never loaded into Python memory.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import duckdb
import httpx

from dddxb.ingest.config import resolve_proxy
from dddxb.ingest.sources import DubaiPulseDataset, window_start

log = logging.getLogger(__name__)

RAW_DIR = Path("data/raw/dld")
INTERIM_DIR = Path("data/interim")

# Generous read timeout — these dumps can take minutes to stream.
_TIMEOUT = httpx.Timeout(60.0, read=600.0)


def download_dataset(
    dataset: DubaiPulseDataset,
    *,
    refresh: bool = False,
    dest_dir: Path = RAW_DIR,
    proxy: str | None = None,
) -> Path:
    """Stream the dataset's CSV dump to ``dest_dir/<name>.csv``.

    Cached: if the file already exists and ``refresh`` is False, returns it as-is.

    The keyless CSV host is gated to UAE telco networks. Pass ``proxy`` (or set
    ``DDDXB_PROXY`` in ``.env``) to route the download through a UAE residential
    proxy so it exits on an Etisalat/du IP; otherwise the host 301-redirects to
    the internal ``data.dubai`` mirror and the download fails.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{dataset.name}.csv"
    if dest.exists() and not refresh:
        log.info("cached, skipping download: %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
        return dest

    proxy = resolve_proxy(proxy)
    log.info("downloading %s -> %s%s", dataset.csv_url, dest,
             " (via proxy)" if proxy else "")
    tmp = dest.with_name(dest.name + ".part")
    done = 0
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, proxy=proxy) as client:
        with client.stream("GET", dataset.csv_url) as resp:
            final = str(resp.url)
            if "data.dubai" in final or "dubaipulse" not in final:
                raise RuntimeError(
                    f"redirected to {final!r} — the request did not exit on a UAE "
                    "telco IP. Set DDDXB_PROXY to a UAE residential proxy "
                    "(country=AE); see docs/data-sources.md."
                )
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if total and done % (50 << 20) < (1 << 20):
                        log.info("  %.0f%% (%.1f / %.1f MB)", 100 * done / total,
                                 done / 1e6, total / 1e6)
    tmp.replace(dest)
    log.info("downloaded %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def filter_recent(
    dataset: DubaiPulseDataset,
    csv_path: Path,
    *,
    months: int = 6,
    today: date | None = None,
    out_dir: Path = INTERIM_DIR,
) -> Path:
    """Filter ``csv_path`` to the trailing ``months``-month window; write parquet.

    Returns the parquet path. Logs the detected schema, the parsed date range of
    the full file, and the kept row count so the window can be verified.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = window_start(months, today)
    out = out_dir / f"{dataset.name}_last{months}m.parquet"

    con = duckdb.connect()
    try:
        reader = "read_csv_auto(?, sample_size=200000, ignore_errors=true)"
        schema = con.execute(f"DESCRIBE SELECT * FROM {reader}", [str(csv_path)]).fetchall()
        cols = {row[0]: row[1] for row in schema}
        log.info("%s schema: %s", dataset.name, {k: v for k, v in cols.items()})

        date_col = dataset.date_column
        if date_col not in cols:
            raise KeyError(
                f"date column {date_col!r} not in {csv_path.name}; "
                f"available columns: {list(cols)}"
            )

        col_type = cols[date_col].upper()
        if any(t in col_type for t in ("CHAR", "TEXT", "STRING")):
            date_expr = f'TRY_CAST(strptime("{date_col}", \'{dataset.date_format}\') AS DATE)'
        else:
            date_expr = f'TRY_CAST("{date_col}" AS DATE)'

        lo, hi, nbad = con.execute(
            f"SELECT min({date_expr}), max({date_expr}), "
            f"count(*) FILTER (WHERE {date_expr} IS NULL) FROM {reader}",
            [str(csv_path)],
        ).fetchone()
        log.info("%s parsed date range: %s .. %s (unparsed=%s)", dataset.name, lo, hi, nbad)
        if lo is None:
            raise ValueError(
                f"no rows parsed a valid date from {date_col!r} using "
                f"format {dataset.date_format!r}; check sources.py."
            )

        out_literal = str(out).replace("'", "''")
        con.execute(
            f"COPY (SELECT * FROM {reader} WHERE {date_expr} >= DATE '{start.isoformat()}') "
            f"TO '{out_literal}' (FORMAT PARQUET)",
            [str(csv_path)],
        )
        n = con.execute("SELECT count(*) FROM read_parquet(?)", [str(out)]).fetchone()[0]
    finally:
        con.close()

    log.info("wrote %s: %d rows in window >= %s", out, n, start)
    return out
