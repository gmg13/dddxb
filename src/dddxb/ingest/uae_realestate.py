"""Client for the UAE Real Estate APIs on RapidAPI (DLD transactions + Ejari rents).

Two interchangeable third-party providers resell the same DLD data as JSON, with
slightly different shapes. A small :class:`Provider` registry abstracts the
differences so we can fall back between them:

- ``uae-real-estate2`` (bayutapi.com) — **default**. ``POST /transactions`` with a
  JSON body supporting precise ``start_date``/``end_date`` filtering; pages from 0.
- ``uae-real-estate3`` (bayutapi.dev) — ``GET /transactions`` with ``time_period``
  presets; pages from 1. (Its transactions backend was returning 502 on 2026-06-20.)

Both are **non-geo-blocked** (no UAE network needed) and **unofficial** (they
harvest Bayut/DLD data) — run :func:`probe` and a fidelity spot-check before
trusting figures.

Auth is a single RapidAPI key in ``X-RapidAPI-Key`` — one key works across every
API you subscribe to. Resolve it off-disk where possible: ``export RAPIDAPI_KEY``
or a ``RAPIDAPI_KEY_CMD`` secret-manager command, with ``.env`` as fallback.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import polars as pl

from dddxb.ingest import config
from dddxb.ingest.sources import window_start

log = logging.getLogger(__name__)

KEY_ENV = "RAPIDAPI_KEY"
KEY_ENV_FALLBACK = "BAYUT_API_KEY"

RAW_DIR = Path("data/raw/bayut_dld")
INTERIM_DIR = Path("data/interim")


@dataclass(frozen=True)
class Provider:
    """Per-provider endpoint shape (RapidAPI host + path/method differences)."""

    name: str
    host: str
    locations_path: str
    transactions_path: str
    transactions_method: str  # "GET" | "POST"
    listings_path: str
    listings_method: str  # "GET" | "POST"
    location_param: str  # request key for location ids
    period_param: str  # preset-window param name
    supports_date_range: bool  # start_date/end_date filtering
    page_base: int  # first page index (0 or 1)


PROVIDERS: dict[str, Provider] = {
    "uae-real-estate2": Provider(
        name="uae-real-estate2",
        host="uae-real-estate2.p.rapidapi.com",
        locations_path="locations_search",
        transactions_path="transactions",
        transactions_method="POST",
        listings_path="properties_search",
        listings_method="POST",
        location_param="locations_ids",
        period_param="time_frame",
        supports_date_range=True,
        page_base=0,
    ),
    "uae-real-estate3": Provider(
        name="uae-real-estate3",
        host="uae-real-estate3.p.rapidapi.com",
        locations_path="autocomplete",
        transactions_path="transactions",
        transactions_method="GET",
        listings_path="search-property",
        listings_method="GET",
        location_param="location_ids",
        period_param="time_period",
        supports_date_range=False,
        page_base=1,
    ),
}
DEFAULT_PROVIDER = "uae-real-estate2"  # up + precise date filtering

# Candidate keys for the record list inside a response envelope. Providers nest
# under data.{locations,properties,transactions,...}; _extract_records recurses
# into `data` and matches these (verified 2026-06-20 for autocomplete/listings).
_RECORD_KEYS = (
    "records", "data", "result", "results", "rows", "items",
    "hits", "transactions", "properties", "locations", "list",
)
# Candidate field names, finalised after the first probe of a live endpoint.
_DATE_KEYS = (
    "date", "transaction_date", "instance_date", "contract_date",
    "contract_start_date", "registration_date", "created_at", "evidence_date",
)
_PRICE_KEYS = ("price", "amount", "actual_worth", "value", "transaction_price", "annual_amount", "rent")
_AREA_KEYS = ("area", "location", "community", "area_name", "area_name_en", "neighbourhood", "sub_community")
_TYPE_KEYS = ("category", "property_type", "type", "category_name", "property_sub_type")
_SIZE_KEYS = ("size", "area_sqft", "builtup_area", "size_sqft", "sqft", "procedure_area")
_BEDS_KEYS = ("beds", "bedrooms", "rooms", "bedroom", "no_of_rooms")
_LOCID_KEYS = ("externalID", "external_id", "id", "location_id", "locationId", "value")

NORMALIZED_COLUMNS = (
    "microlocality", "purpose", "date", "price", "area", "property_type", "size", "beds",
)


class UAERealEstateError(RuntimeError):
    """Raised when the client is misconfigured or the call budget is exhausted."""


def _require_key() -> str:
    # Prefer a shell export or a *_CMD secret-manager command over an on-disk .env.
    key = config.get_secret(KEY_ENV) or config.get_secret(KEY_ENV_FALLBACK)
    if not key:
        raise UAERealEstateError(
            f"{KEY_ENV} (or {KEY_ENV_FALLBACK}) is not set. Subscribe to a UAE Real "
            "Estate API on RapidAPI (Basic is free), then either `export "
            f"{KEY_ENV}=…`, set `{KEY_ENV}_CMD` to a secret-manager command, or put "
            "it in .env; see docs/data-sources.md."
        )
    return key


_SUPPORTED_PERIODS = (1, 3, 6, 12, 36)


def _snap_period(months: int) -> str:
    """Snap a month count up to the nearest supported preset (1/3/6/12/36m)."""
    for period in _SUPPORTED_PERIODS:
        if months <= period:
            return f"{period}m"
    return f"{_SUPPORTED_PERIODS[-1]}m"


def _build_transactions_request(
    provider: Provider,
    *,
    purpose: str,
    location_ids: list[str] | str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    time_period: str | None = None,
    page: int | None = None,
    extra: dict | None = None,
) -> tuple[str, str, dict, dict | None]:
    """Build ``(method, path, params, json_body)`` for a transactions request.

    Pure (no I/O) so the per-provider GET-query vs POST-body shaping is testable.
    """
    page = provider.page_base if page is None else page
    filters: dict[str, object] = {"purpose": purpose}
    if location_ids:
        if provider.transactions_method == "POST":
            filters[provider.location_param] = (
                [location_ids] if isinstance(location_ids, str) else list(location_ids)
            )
        else:
            filters[provider.location_param] = (
                location_ids if isinstance(location_ids, str)
                else ",".join(str(i) for i in location_ids)
            )
    if provider.supports_date_range and (start_date or end_date):
        if start_date:
            filters["start_date"] = start_date
        if end_date:
            filters["end_date"] = end_date
    elif time_period and provider.period_param:
        filters[provider.period_param] = time_period
    if extra:
        filters.update(extra)

    if provider.transactions_method == "POST":
        return "POST", provider.transactions_path, {"page": page}, filters
    return "GET", provider.transactions_path, {**filters, "page": page}, None


class UAERealEstateClient:
    """Provider-aware RapidAPI client with throttling, 429 backoff, call budget."""

    def __init__(
        self,
        *,
        provider: str | Provider = DEFAULT_PROVIDER,
        key: str | None = None,
        min_interval: float = 0.25,
        max_calls: int | None = None,
    ):
        self.provider = provider if isinstance(provider, Provider) else PROVIDERS[provider]
        self._headers = {
            "X-RapidAPI-Key": key or _require_key(),
            "X-RapidAPI-Host": self.provider.host,
        }
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, read=60.0))
        self._min_interval = min_interval
        self._last = 0.0
        self.calls = 0
        self.max_calls = max_calls

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json_body: dict | None = None) -> object:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise UAERealEstateError(
                f"call budget exhausted ({self.max_calls}); aborting to protect quota"
            )
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = f"https://{self.provider.host}/{path.lstrip('/')}"
        attempts = 5
        for attempt in range(attempts):
            resp = self._client.request(
                method, url, headers=self._headers, params=params, json=json_body
            )
            self._last = time.monotonic()
            self.calls += 1
            # 429 (rate limit) and 5xx (provider hiccups — this API 500s
            # intermittently) are retryable with backoff; 4xx are not.
            retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
            if retryable and attempt < attempts - 1:
                back = 2 ** attempt
                log.warning("%s on %s; retry %d/%d after %ss",
                            resp.status_code, path, attempt + 1, attempts - 1, back)
                time.sleep(back)
                continue
            resp.raise_for_status()
            return resp.json()
        raise UAERealEstateError(f"exhausted retries on {path}")

    # -- reads ----------------------------------------------------------------
    def autocomplete(self, query: str) -> object:
        """Location search → records carrying location ids."""
        return self._request("GET", self.provider.locations_path, params={"query": query})

    def location_id(self, query: str) -> str | None:
        """Best-match location id for a microlocality name, or None."""
        records = _extract_records(self.autocomplete(query))
        if not records:
            return None
        lid = _first(records[0], *_LOCID_KEYS)
        return str(lid) if lid is not None else None

    def transactions(
        self,
        *,
        purpose: str = "for-sale",
        location_ids: list[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        time_period: str | None = None,
        page: int | None = None,
        extra: dict | None = None,
    ) -> object:
        """One page of DLD transactions (for-sale) / Ejari rents (for-rent)."""
        method, path, params, body = _build_transactions_request(
            self.provider, purpose=purpose, location_ids=location_ids,
            start_date=start_date, end_date=end_date, time_period=time_period,
            page=page, extra=extra,
        )
        return self._request(method, path, params=params, json_body=body)

    def iter_transactions(
        self,
        *,
        purpose: str,
        location_ids: list[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        time_period: str | None = None,
        max_pages: int = 200,
    ):
        """Yield transaction records across pages, stopping on a short/empty page."""
        base = self.provider.page_base
        page_size: int | None = None
        for i in range(max_pages):
            rows = _extract_records(
                self.transactions(
                    purpose=purpose, location_ids=location_ids, start_date=start_date,
                    end_date=end_date, time_period=time_period, page=base + i,
                )
            )
            if not rows:
                break
            yield from rows
            if page_size is None:
                page_size = len(rows)
            if len(rows) < page_size:
                break

    def close(self) -> None:
        self._client.close()


# -- parsing helpers ----------------------------------------------------------
def _extract_records(payload: object) -> list[dict]:
    """Pull the record list out of a response, tolerating common envelopes."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in _RECORD_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        for value in payload.values():  # e.g. {"data": {"locations": [...]}}
            if isinstance(value, dict):
                nested = _extract_records(value)
                if nested:
                    return nested
    return []


def _first(row: dict, *keys: str):
    """First present, non-empty value among ``keys`` (case-insensitive)."""
    lowered = {k.lower(): v for k, v in row.items()}
    for key in keys:
        for candidate in (key, key.lower()):
            value = row.get(candidate, lowered.get(candidate))
            if value not in (None, "", []):
                return value
    return None


def _num(value) -> float | None:
    """Coerce a price/size value to float, stripping currency/commas."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def _coerce_date(value) -> date | None:
    """Parse a date from ISO, DD-MM-YYYY, or epoch (s/ms)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (OSError, ValueError, OverflowError):
            return None
    text = str(value)[:10].replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None
    try:
        if len(parts[0]) == 4:  # YYYY-MM-DD
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        else:  # DD-MM-YYYY
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        return date(y, m, d)
    except ValueError:
        return None


def _dig(row: object, *path: str):
    """Walk a nested dict by key path; None if any hop is missing."""
    cur = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _pick(*values):
    """First non-empty value."""
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _row_in_window(row: dict, start: date) -> bool:
    parsed = _coerce_date(_pick(_dig(row, "date"), _first(row, *_DATE_KEYS)))
    return parsed is not None and parsed >= start


def _normalize_record(r: dict, *, purpose: str) -> dict:
    """One raw transaction → canonical row.

    Handles the uae-real-estate2 nested shape (``amount``/``date`` top-level,
    ``property.{type,beds,builtup_area.sqft}``, ``location.full_location``) and
    falls back to flat field-name guesses for other providers. Verified against
    live data 2026-06-22.
    """
    flat_area = _first(r, *_AREA_KEYS)
    beds = _pick(_dig(r, "property", "beds"), _first(r, *_BEDS_KEYS))
    return {
        "microlocality": r.get("_microlocality"),
        "purpose": purpose,
        "date": _coerce_date(_pick(_dig(r, "date"), _first(r, *_DATE_KEYS))),
        "price": _num(_pick(_dig(r, "amount"), _first(r, *_PRICE_KEYS))),
        "area": _pick(
            _dig(r, "location", "full_location"),
            _dig(r, "location", "location"),
            flat_area if isinstance(flat_area, str) else None,
        ),
        "property_type": _pick(_dig(r, "property", "type"), _first(r, *_TYPE_KEYS)),
        "size": _num(_pick(
            _dig(r, "property", "builtup_area", "sqft"),
            _dig(r, "property", "plot_area", "sqft"),
            _first(r, *_SIZE_KEYS),
        )),
        "beds": str(beds) if beds not in (None, "") else None,
    }


def normalize_transactions(records: list[dict], *, purpose: str) -> pl.DataFrame:
    """Map raw records to the canonical schema the clean/features stages expect.

    Always returns the full :data:`NORMALIZED_COLUMNS` set so empty pulls still
    write a valid, schema-stable parquet.
    """
    rows = [_normalize_record(r, purpose=purpose) for r in records]
    if not rows:
        return pl.DataFrame({c: [] for c in NORMALIZED_COLUMNS})
    return pl.DataFrame(rows).select(NORMALIZED_COLUMNS)


def pull(
    microlocalities: list[str],
    *,
    months: int = 6,
    purposes: tuple[str, ...] = ("for-sale", "for-rent"),
    client: UAERealEstateClient | None = None,
    max_pages: int = 200,
    out_dir: Path = INTERIM_DIR,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Path]:
    """Pull transactions per microlocality × purpose; write normalised parquet.

    Saves raw records to ``raw_dir`` as JSONL (lossless audit trail) and the
    normalised window to ``out_dir`` as parquet. Returns ``{purpose: parquet_path}``.
    """
    own = client is None
    client = client or UAERealEstateClient()
    start = window_start(months)
    # Use the time_frame/time_period preset for both purposes: uae-real-estate2's
    # for-rent endpoint 500s on start_date/end_date, while the preset works for
    # both. The client-side window filter below adds precision. (presets:
    # 1m/3m/6m/12m/36m — snap up to the next supported preset.)
    time_period = _snap_period(months)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    try:
        loc_map: dict[str, str | None] = {}
        for name in microlocalities:
            loc_map[name] = client.location_id(name)
            log.info("location %r -> %s", name, loc_map[name])

        for purpose in purposes:
            slug = purpose.replace("-", "_")
            kept: list[dict] = []
            for name, lid in loc_map.items():
                if lid is None:
                    log.warning("no location id for %r; skipping", name)
                    continue
                try:
                    for row in client.iter_transactions(
                        purpose=purpose, location_ids=[lid],
                        time_period=time_period, max_pages=max_pages,
                    ):
                        if _row_in_window(row, start):
                            row["_microlocality"] = name
                            kept.append(row)
                except (httpx.HTTPStatusError, UAERealEstateError) as exc:
                    # Keep partial data and move on — the provider 5xx's at times.
                    log.warning("partial: %s/%s stopped early (%s)", name, purpose, exc)

            raw_path = raw_dir / f"{slug}_last{months}m.jsonl"
            with raw_path.open("w") as fh:
                for r in kept:
                    fh.write(json.dumps(r, default=str) + "\n")
            out = out_dir / f"bayut_{slug}_last{months}m.parquet"
            normalize_transactions(kept, purpose=purpose).write_parquet(out)
            log.info("wrote %s: %d rows (raw: %s; calls so far=%d)",
                     out, len(kept), raw_path, client.calls)
            results[purpose] = out
    finally:
        if own:
            client.close()
    return results
