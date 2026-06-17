"""Registry of external data sources and a shared time-window helper.

Source URLs and access notes are documented in ``docs/data-sources.md``.
Dubai Pulse open CSVs are full historical dumps with no key required; we cache
the raw dump and filter to a recent window downstream.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DubaiPulseDataset:
    """A Dubai Pulse open dataset, reachable as a CSV dump or via the open API."""

    name: str  # short key used for filenames, e.g. "transactions"
    csv_url: str  # keyless full-dump download URL (host www.dubaipulse.gov.ae)
    api_path: str  # open-API path on api.dubaipulse.gov.ae, e.g. "open/dld/..."
    date_column: str  # column used to filter to a recent window
    date_format: str  # strptime format for the date column (confirmed on first run)
    description: str


# Verified 2026-06-18. Both are keyless open CSV dumps from Dubai Pulse (DLD).
# The date_column / date_format are best-effort and validated against the real
# schema at runtime (filter_recent logs the detected schema and date range).
DUBAI_PULSE_DATASETS: dict[str, DubaiPulseDataset] = {
    "transactions": DubaiPulseDataset(
        name="transactions",
        csv_url=(
            "https://www.dubaipulse.gov.ae/dataset/"
            "3b25a6f5-9077-49d7-8a1e-bc6d5dea88fd/resource/"
            "a37511b0-ea36-485d-bccd-2d6cb24507e7/download/transactions.csv"
        ),
        api_path="open/dld/dld_transactions-open-api",
        date_column="instance_date",
        date_format="%d-%m-%Y",
        description="DLD property sales transactions (full historical dump).",
    ),
    "rent_contracts": DubaiPulseDataset(
        name="rent_contracts",
        csv_url=(
            "https://www.dubaipulse.gov.ae/dataset/"
            "00768c45-f014-4cc6-937d-2b17dcab53fb/resource/"
            "765b5a69-ca16-4bfd-9852-74612f3c4ea6/download/rent_contracts.csv"
        ),
        api_path="open/dld/dld_rent_contracts-open-api",
        date_column="contract_start_date",
        date_format="%d-%m-%Y",
        description="Ejari registered tenancy contracts (full historical dump).",
    ),
}


def window_start(months: int, today: date | None = None) -> date:
    """Return the inclusive start date of the trailing ``months``-month window.

    e.g. ``window_start(6, date(2026, 6, 18)) -> date(2025, 12, 18)``.
    """
    today = today or date.today()
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
