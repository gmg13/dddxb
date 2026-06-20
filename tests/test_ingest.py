"""Tests for the ingest stage: window math and CSV→parquet filtering."""

from __future__ import annotations

from datetime import date

import duckdb

from dddxb.ingest.config import get_secret
from dddxb.ingest.dubai_pulse import filter_recent
from dddxb.ingest.dubai_pulse_api import _extract_records, _row_in_window
from dddxb.ingest.sources import DubaiPulseDataset, window_start


def test_get_secret_prefers_env_then_cmd(monkeypatch):
    # 1. shell export wins
    monkeypatch.setenv("DDDXB_TESTSECRET", "from-env")
    assert get_secret("DDDXB_TESTSECRET") == "from-env"
    # 2. *_CMD used when the var itself is unset (secret stays off-disk)
    monkeypatch.delenv("DDDXB_TESTSECRET", raising=False)
    monkeypatch.setenv("DDDXB_TESTSECRET_CMD", "printf cmd-secret")
    assert get_secret("DDDXB_TESTSECRET") == "cmd-secret"
    # 3. unset everything -> None
    monkeypatch.delenv("DDDXB_TESTSECRET_CMD", raising=False)
    assert get_secret("DDDXB_TESTSECRET_MISSING") is None


def test_window_start_six_months():
    assert window_start(6, date(2026, 6, 18)) == date(2025, 12, 18)
    # crossing a year boundary with day clamping
    assert window_start(1, date(2026, 3, 31)) == date(2026, 2, 28)


def test_extract_records_tolerates_envelopes():
    assert _extract_records([{"a": 1}]) == [{"a": 1}]
    assert _extract_records({"records": [{"a": 1}]}) == [{"a": 1}]
    assert _extract_records({"data": [{"a": 1}]}) == [{"a": 1}]
    assert _extract_records({"unexpected": 1}) == []


def test_row_in_window_both_date_orders():
    start = window_start(6, date(2026, 6, 18))
    assert _row_in_window({"d": "2026-03-01"}, "d", start) is True
    assert _row_in_window({"d": "01-03-2026"}, "d", start) is True
    assert _row_in_window({"d": "2025-06-01"}, "d", start) is False
    assert _row_in_window({"d": ""}, "d", start) is False


def test_filter_recent_keeps_only_window(tmp_path):
    csv = tmp_path / "transactions.csv"
    csv.write_text(
        "instance_date,area_name_en,actual_worth\n"
        "01-03-2026,Al Jaddaf,1000000\n"  # in window
        "15-06-2026,Business Bay,2000000\n"  # in window
        "10-01-2025,Marina,3000000\n"  # before window
    )
    dataset = DubaiPulseDataset(
        name="transactions",
        csv_url="",
        api_path="",
        date_column="instance_date",
        date_format="%d-%m-%Y",
        description="",
    )
    out = filter_recent(
        dataset, csv, months=6, today=date(2026, 6, 18), out_dir=tmp_path
    )
    rows = duckdb.sql(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    assert rows == 2
