"""CLI for the ingest stage.

Examples:
    # Free Dubai Pulse open API (needs DUBAI_PULSE_API_KEY/SECRET in .env):
    uv run python -m dddxb.ingest --via api --datasets transactions,rent_contracts --months 6
    uv run python -m dddxb.ingest --via api --probe          # sample to confirm schema

    # CSV full dump (needs reachable www.dubaipulse.gov.ae):
    uv run python -m dddxb.ingest --via csv --datasets transactions --refresh

    # Bayut listings (needs BAYUT_API_KEY):
    uv run python -m dddxb.ingest --include-listings
"""

from __future__ import annotations

import argparse
import json
import logging

from dddxb.ingest import bayut, dubai_pulse
from dddxb.ingest.sources import DUBAI_PULSE_DATASETS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dddxb.ingest", description=__doc__)
    parser.add_argument(
        "--via", choices=("api", "csv"), default="api", help="acquisition route (default: api)"
    )
    parser.add_argument(
        "--datasets",
        default="transactions,rent_contracts",
        help="comma-separated Dubai Pulse datasets "
        f"(available: {', '.join(DUBAI_PULSE_DATASETS)})",
    )
    parser.add_argument("--months", type=int, default=6, help="trailing window length")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="(api route) fetch a tiny sample per dataset and print it, then exit",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="(csv route) re-download even if cached"
    )
    parser.add_argument(
        "--include-listings",
        action="store_true",
        help="also pull Bayut listings (requires BAYUT_API_KEY)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    names = [n.strip() for n in args.datasets.split(",") if n.strip()]
    unknown = [n for n in names if n not in DUBAI_PULSE_DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {unknown}; available: {list(DUBAI_PULSE_DATASETS)}")
    datasets = [DUBAI_PULSE_DATASETS[n] for n in names]

    if args.via == "api":
        from dddxb.ingest.dubai_pulse_api import DubaiPulseAPIClient

        client = DubaiPulseAPIClient()
        try:
            for dataset in datasets:
                if args.probe:
                    print(f"--- probe: {dataset.name} ---")
                    print(json.dumps(client.probe(dataset), indent=2, default=str)[:4000])
                else:
                    client.fetch_window(dataset, months=args.months)
        finally:
            client.close()
    else:  # csv
        for dataset in datasets:
            csv_path = dubai_pulse.download_dataset(dataset, refresh=args.refresh)
            dubai_pulse.filter_recent(dataset, csv_path, months=args.months)

    if args.include_listings:
        # Inert until BAYUT_API_KEY is configured; raises a clear error otherwise.
        bayut.search_listings(purpose="for-sale", page=0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
