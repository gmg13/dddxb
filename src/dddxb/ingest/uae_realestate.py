"""Client for the UAE Real Estate API on RapidAPI.

Host ``uae-real-estate3.p.rapidapi.com`` (the bayutapi.dev product). Serves DLD
sales **transactions** and Ejari **rental contracts** as filtered JSON — a
non-geo-blocked alternative to the Dubai Pulse open data, whose CSV/registration
hosts are gated to UAE telco networks.

Third-party / unofficial: run :func:`probe` and a fidelity spot-check against a
public reference before trusting figures for analysis.

Auth is a single RapidAPI key in the ``X-RapidAPI-Key`` header — one key works
across every API on your RapidAPI account. Set ``RAPIDAPI_KEY`` (or the
``BAYUT_API_KEY`` fallback) in ``.env`` (gitignored).

The exact response envelope, field names, page size, and ``time_period`` semantics
are confirmed at runtime with :func:`probe`; the normaliser maps several likely
field names and is finalised once the probe output is seen. A client-side date
filter is always applied as the correctness backstop.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import polars as pl

from dddxb.ingest import config
from dddxb.ingest.sources import window_start

log = logging.getLogger(__name__)

HOST = "uae-real-estate3.p.rapidapi.com"
KEY_ENV = "RAPIDAPI_KEY"
KEY_ENV_FALLBACK = "BAYUT_API_KEY"

RAW_DIR = Path("data/raw/bayut_dld")
INTERIM_DIR = Path("data/interim")

# Candidate keys for the record list inside a response envelope.
_RECORD_KEYS = (
    "records", "data", "result", "results", "rows", "items",
    "hits", "transactions", "properties", "list",
)
# Candidate field names, finalised after the first probe.
_DATE_KEYS = (
    "date", "transaction_date", "instance_date", "contract_date",
    "contract_start_date", "registration_date", "created_at", "evidence_date",
)
_PRICE_KEYS = ("price", "amount", "actual_worth", "value", "transaction_price", "annual_amount", "rent")
_AREA_KEYS = ("area", "location", "community", "area_name", "area_name_en", "neighbourhood", "sub_community")
_TYPE_KEYS = ("category", "property_type", "type", "category_name", "property_sub_type")
_SIZE_KEYS = ("size", "area_sqft", "builtup_area", "size_sqft", "sqft", "procedure_area")
_BEDS_KEYS = ("beds", "bedrooms", "rooms", "bedroom", "no_of_rooms")
_LOCID_KEYS = ("id", "externalID", "external_id", "location_id", "locationId", "value")

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
            f"{KEY_ENV} (or {KEY_ENV_FALLBACK}) is not set. Subscribe to the UAE Real "
            "Estate API on RapidAPI (Basic is free), then either `export "
            f"{KEY_ENV}=…`, set `{KEY_ENV}_CMD` to a secret-manager command, or put "
            "it in .env; see docs/data-sources.md."
        )
    return key


class UAERealEstateClient:
    """Thin RapidAPI client with throttling, 429 backoff, and a call budget."""

    def __init__(
        self,
        *,
        key: str | None = None,
        host: str = HOST,
        min_interval: float = 0.25,
        max_calls: int | None = None,
    ):
        self._host = host
        self._headers = {"X-RapidAPI-Key": key or _require_key(), "X-RapidAPI-Host": host}
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, read=60.0))
        self._min_interval = min_interval
        self._last = 0.0
        self.calls = 0
        self.max_calls = max_calls

    def _get(self, path: str, params: dict) -> object:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise UAERealEstateError(
                f"call budget exhausted ({self.max_calls}); aborting to protect quota"
            )
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = f"https://{self._host}/{path.lstrip('/')}"
        for attempt in range(4):
            resp = self._client.get(url, headers=self._headers, params=params)
            self._last = time.monotonic()
            self.calls += 1
            if resp.status_code == 429:
                back = 2 ** attempt
                log.warning("429 rate limited on %s; backing off %ss", path, back)
                time.sleep(back)
                continue
            resp.raise_for_status()
            return resp.json()
        raise UAERealEstateError("repeated 429s; slow down (raise min_interval) or upgrade plan")

    # -- reads ----------------------------------------------------------------
    def autocomplete(self, query: str) -> object:
        """Location autocomplete → records carrying location ids."""
        return self._get("autocomplete", {"query": query})

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
        time_period: str | None = None,
        page: int = 1,
        extra: dict | None = None,
    ) -> object:
        """One page of DLD transactions (purpose=for-sale) / Ejari rents (for-rent)."""
        params: dict[str, object] = {"purpose": purpose, "page": page}
        if location_ids:
            params["location_ids"] = (
                location_ids if isinstance(location_ids, str)
                else ",".join(str(i) for i in location_ids)
            )
        if time_period:
            params["time_period"] = time_period
        if extra:
            params.update(extra)
        return self._get("transactions", params)

    def iter_transactions(
        self,
        *,
        purpose: str,
        location_ids: list[str] | str | None = None,
        time_period: str | None = None,
        max_pages: int = 200,
    ):
        """Yield transaction records across pages, stopping on a short/empty page."""
        page_size: int | None = None
        for page in range(1, max_pages + 1):
            rows = _extract_records(
                self.transactions(
                    purpose=purpose, location_ids=location_ids,
                    time_period=time_period, page=page,
                )
            )
            if not rows:
                break
            yield from rows
            if page_size is None:
                page_size = len(rows)
            if len(rows) < page_size:
                break

    def search_property(
        self, *, purpose: str = "for-sale", location_ids: list[str] | str | None = None, page: int = 1
    ) -> object:
        """One page of current asking listings (optional live-market layer)."""
        params: dict[str, object] = {"purpose": purpose, "page": page}
        if location_ids:
            params["location_ids"] = (
                location_ids if isinstance(location_ids, str)
                else ",".join(str(i) for i in location_ids)
            )
        return self._get("search-property", params)

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
        # single nested object, e.g. {"data": {"hits": [...]}}
        for value in payload.values():
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


def _row_in_window(row: dict, start: date) -> bool:
    parsed = _coerce_date(_first(row, *_DATE_KEYS))
    return parsed is not None and parsed >= start


def normalize_transactions(records: list[dict], *, purpose: str) -> pl.DataFrame:
    """Map raw records to the canonical schema the clean/features stages expect.

    Best-effort until the probe confirms exact field names; unmapped fields land
    as null. Always returns the full :data:`NORMALIZED_COLUMNS` set so empty pulls
    still write a valid, schema-stable parquet.
    """
    rows = [
        {
            "microlocality": r.get("_microlocality"),
            "purpose": purpose,
            "date": _coerce_date(_first(r, *_DATE_KEYS)),
            "price": _num(_first(r, *_PRICE_KEYS)),
            "area": _first(r, *_AREA_KEYS),
            "property_type": _first(r, *_TYPE_KEYS),
            "size": _num(_first(r, *_SIZE_KEYS)),
            "beds": _first(r, *_BEDS_KEYS),
        }
        for r in records
    ]
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

    Saves the raw records to ``raw_dir`` as JSONL (lossless audit trail) and the
    normalised window to ``out_dir`` as parquet. Returns ``{purpose: parquet_path}``.
    """
    own = client is None
    client = client or UAERealEstateClient()
    start = window_start(months)
    time_period = f"{months}m"
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
                for row in client.iter_transactions(
                    purpose=purpose, location_ids=[lid],
                    time_period=time_period, max_pages=max_pages,
                ):
                    if _row_in_window(row, start):
                        row["_microlocality"] = name
                        kept.append(row)

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
