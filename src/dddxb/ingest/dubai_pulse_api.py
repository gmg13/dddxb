"""Dubai Pulse *open data* API client (api.dubaipulse.gov.ae).

This is the free open-data API — NOT the commercial DLD API Gateway
(dubailand.gov.ae, AED 30k/yr/API). Credentials are a free API Key + Secret
emailed by Dubai Pulse on dataset grant; see ``docs/data-sources.md`` for how
to register.

Auth (verified 2026-06-18): the token endpoint is an Apigee
``client_credential`` service that expects **HTTP Basic auth** (key:secret),
``grant_type=client_credentials`` as a query param, and an empty form body. The
returned bearer token goes in ``Authorization: Bearer <token>`` and is short-lived.

The data endpoint's exact pagination params, filter syntax, and JSON envelope are
confirmed at runtime with ``probe()`` once a key is available — fields below
handle the common shapes and are easy to adjust.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from dddxb.ingest import config
from dddxb.ingest.sources import DubaiPulseDataset, window_start

log = logging.getLogger(__name__)

API_HOST = "api.dubaipulse.gov.ae"
TOKEN_URL = f"https://{API_HOST}/oauth/client_credential/accesstoken"
INTERIM_DIR = Path("data/interim")

KEY_ENV = "DUBAI_PULSE_API_KEY"
SECRET_ENV = "DUBAI_PULSE_API_SECRET"


class DubaiPulseAPIClient:
    """Thin client handling token acquisition/refresh and paginated reads."""

    def __init__(self, key: str | None = None, secret: str | None = None):
        if key is None or secret is None:
            key, secret = config.require(KEY_ENV, SECRET_ENV)
        self._key = key
        self._secret = secret
        self._token: str | None = None
        self._expires_at = 0.0
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))

    # -- auth -----------------------------------------------------------------
    def _token_value(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = self._client.post(
            TOKEN_URL,
            params={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(self._key, self._secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content="",
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 1800))
        log.info("obtained Dubai Pulse token (expires in %ss)", data.get("expires_in", "?"))
        return self._token

    def _get(self, path: str, params: dict) -> httpx.Response:
        resp = self._client.get(
            f"https://{API_HOST}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token_value()}"},
        )
        resp.raise_for_status()
        return resp

    # -- reads ----------------------------------------------------------------
    def probe(self, dataset: DubaiPulseDataset, limit: int = 3) -> dict:
        """Fetch a tiny sample to reveal the JSON envelope + field names.

        Run this first with real credentials to confirm the record shape, the
        actual date-column name, and the pagination/filter parameters before a
        full pull.
        """
        return self._get(dataset.api_path, {"Limit": limit}).json()

    def fetch_window(
        self,
        dataset: DubaiPulseDataset,
        *,
        months: int = 6,
        today: date | None = None,
        page_size: int = 1000,
        max_pages: int = 10_000,
        out_dir: Path = INTERIM_DIR,
    ) -> Path:
        """Page through the dataset and write rows within the window to parquet.

        Applies a client-side date filter on ``dataset.date_column`` as the
        correctness backstop. If ``probe()`` confirms a server-side filter param,
        pass it via ``page_size``/params tuning to avoid downloading everything.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        start = window_start(months, today)
        out = out_dir / f"{dataset.name}_last{months}m.parquet"

        kept: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            payload = self._get(dataset.api_path, {"Limit": page_size, "Offset": offset}).json()
            rows = _extract_records(payload)
            if not rows:
                break
            kept.extend(r for r in rows if _row_in_window(r, dataset.date_column, start))
            offset += len(rows)
            if len(rows) < page_size:
                break

        frame = pl.DataFrame(kept) if kept else pl.DataFrame()
        frame.write_parquet(out)
        log.info("wrote %s: %d rows in window >= %s (scanned offset=%d)", out, frame.height, start, offset)
        return out

    def close(self) -> None:
        self._client.close()


def _extract_records(payload: object) -> list[dict]:
    """Pull the record list out of the response, tolerating common envelopes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "data", "result", "results", "rows", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _row_in_window(row: dict, date_column: str, start: date) -> bool:
    raw = row.get(date_column)
    if not raw:
        return False
    text = str(raw)[:10].replace("/", "-")
    parts = text.split("-")
    try:
        if len(parts[0]) == 4:  # YYYY-MM-DD
            y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
        else:  # DD-MM-YYYY
            d, m, y = (int(parts[0]), int(parts[1]), int(parts[2]))
        return date(y, m, d) >= start
    except (ValueError, IndexError):
        return False
