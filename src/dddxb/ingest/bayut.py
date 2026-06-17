"""Bayut current-listings client.

IMPORTANT: there is no official *public* Bayut listings-data API. Bayut's only
official API is the Leads API (advertiser lead capture, not listings). The only
official route to listings data is a negotiated enterprise data-licensing
agreement with Bayut / dubizzle Group. See ``docs/data-sources.md`` for how to
obtain access.

The practical route is the unofficial RapidAPI "BayutAPI". This client targets
that schema and stays inert until ``BAYUT_API_KEY`` is set in the environment
(load it from ``.env``, which is gitignored). The exact endpoint/params depend on
the RapidAPI provider you subscribe to — confirm them against your provider's docs
before the first real pull.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RAW_DIR = Path("data/raw/bayut")

# Unofficial RapidAPI provider host. Override with BAYUT_API_HOST if you subscribe
# to a different provider.
DEFAULT_HOST = "bayut-api1.p.rapidapi.com"


class BayutClientError(RuntimeError):
    """Raised when the Bayut client is misconfigured (e.g. missing API key)."""


def _require_key() -> str:
    key = os.environ.get("BAYUT_API_KEY")
    if not key:
        raise BayutClientError(
            "BAYUT_API_KEY is not set. Listings acquisition is deferred until a key "
            "is available — see docs/data-sources.md for how to obtain one."
        )
    return key


def search_listings(
    *,
    purpose: str = "for-sale",
    location_ids: list[int] | None = None,
    page: int = 0,
    hits_per_page: int = 25,
    host: str | None = None,
) -> dict:
    """Fetch one page of listings from the (unofficial) RapidAPI BayutAPI.

    Raises ``BayutClientError`` if no API key is configured. Endpoint and parameter
    names should be confirmed against your RapidAPI provider's documentation.
    """
    key = _require_key()
    host = host or os.environ.get("BAYUT_API_HOST", DEFAULT_HOST)
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": host}
    params: dict[str, object] = {
        "purpose": purpose,
        "page": page,
        "hitsPerPage": hits_per_page,
    }
    if location_ids:
        params["locationExternalIDs"] = ",".join(str(i) for i in location_ids)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"https://{host}/properties/list", headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


def snapshot_path(when: date | None = None, dest_dir: Path = RAW_DIR) -> Path:
    """Path for a dated listings snapshot parquet."""
    when = when or date.today()
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / f"listings_{when.isoformat()}.parquet"
