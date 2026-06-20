"""Tests for the UAE Real Estate (RapidAPI) client parsing/normalisation.

All offline — no network. These pin the envelope/field tolerance so the probe
only has to confirm the real key names, not the plumbing.
"""

from __future__ import annotations

from datetime import date

from dddxb.ingest.uae_realestate import (
    NORMALIZED_COLUMNS,
    PROVIDERS,
    _build_transactions_request,
    _coerce_date,
    _extract_records,
    _first,
    _num,
    _row_in_window,
    normalize_transactions,
)


def test_build_transactions_request_post_provider():
    p = PROVIDERS["uae-real-estate2"]
    method, path, params, body = _build_transactions_request(
        p, purpose="for-sale", location_ids=["5643"],
        start_date="2025-12-18", end_date="2026-06-18",
    )
    assert method == "POST" and path == "transactions"
    assert params == {"page": 0}  # pages from 0
    assert body["purpose"] == "for-sale"
    assert body["locations_ids"] == ["5643"]  # array, provider-specific key
    assert body["start_date"] == "2025-12-18" and body["end_date"] == "2026-06-18"


def test_build_transactions_request_get_provider():
    p = PROVIDERS["uae-real-estate3"]
    method, path, params, body = _build_transactions_request(
        p, purpose="for-rent", location_ids="1509", time_period="6m",
    )
    assert method == "GET" and body is None
    assert params["purpose"] == "for-rent"
    assert params["location_ids"] == "1509"  # comma-string in query
    assert params["page"] == 1  # pages from 1
    assert params["time_period"] == "6m"  # preset, no date range


def test_extract_records_tolerates_envelopes():
    assert _extract_records([{"a": 1}, "skip"]) == [{"a": 1}]
    assert _extract_records({"hits": [{"a": 1}]}) == [{"a": 1}]
    assert _extract_records({"transactions": [{"a": 1}]}) == [{"a": 1}]
    assert _extract_records({"data": {"results": [{"a": 1}]}}) == [{"a": 1}]
    assert _extract_records({"nope": 1}) == []
    # real provider envelopes (verified 2026-06-20)
    assert _extract_records({"success": True, "data": {"locations": [{"id": 1509}]}}) == [
        {"id": 1509}
    ]
    assert _extract_records({"success": True, "data": {"properties": [{"id": "11567965"}]}}) == [
        {"id": "11567965"}
    ]


def test_first_is_case_insensitive_and_skips_empty():
    assert _first({"Price": 100}, "price") == 100
    assert _first({"price": "", "amount": 250}, "price", "amount") == 250
    assert _first({"x": None}, "x", "y") is None


def test_num_strips_currency_and_commas():
    assert _num("AED 1,250,000") == 1250000.0
    assert _num(2000000) == 2000000.0
    assert _num(None) is None
    assert _num("n/a") is None


def test_coerce_date_handles_formats():
    assert _coerce_date("2026-03-01") == date(2026, 3, 1)
    assert _coerce_date("01-03-2026") == date(2026, 3, 1)
    assert _coerce_date("2026-03-01T12:00:00Z") == date(2026, 3, 1)
    assert _coerce_date(1772323200) == date(2026, 3, 1)  # epoch seconds
    assert _coerce_date(1772323200000) == date(2026, 3, 1)  # epoch millis
    assert _coerce_date("") is None


def test_row_in_window_uses_any_date_field():
    start = date(2025, 12, 18)
    assert _row_in_window({"transaction_date": "2026-03-01"}, start) is True
    assert _row_in_window({"instance_date": "01-03-2026"}, start) is True
    assert _row_in_window({"contract_start_date": "2025-06-01"}, start) is False
    assert _row_in_window({"unrelated": "x"}, start) is False


def test_normalize_maps_fields_and_is_schema_stable():
    records = [
        {
            "_microlocality": "Al Jaddaf",
            "transaction_date": "2026-03-01",
            "price": "AED 1,000,000",
            "area": "Al Jaddaf",
            "property_type": "Apartment",
            "size": "850",
            "beds": 1,
        }
    ]
    frame = normalize_transactions(records, purpose="for-sale")
    assert frame.columns == list(NORMALIZED_COLUMNS)
    row = frame.row(0, named=True)
    assert row["microlocality"] == "Al Jaddaf"
    assert row["purpose"] == "for-sale"
    assert row["date"] == date(2026, 3, 1)
    assert row["price"] == 1000000.0
    assert row["size"] == 850.0

    empty = normalize_transactions([], purpose="for-rent")
    assert empty.columns == list(NORMALIZED_COLUMNS)
    assert empty.height == 0
