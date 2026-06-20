"""CLI for the UAE Real Estate (RapidAPI) ingest route.

Examples:
    # Confirm the response schema with ~3 calls before any real pull:
    uv run python -m dddxb.ingest.uae_realestate_cli --probe

    # Smoke-test on a couple of microlocalities (Basic free tier is enough):
    uv run python -m dddxb.ingest.uae_realestate_cli \
        --microlocalities "Al Jaddaf,Business Bay" --months 6 --max-calls 400

Needs RAPIDAPI_KEY (or BAYUT_API_KEY) in .env — see docs/data-sources.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import httpx

from dddxb.ingest.uae_realestate import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    UAERealEstateClient,
    UAERealEstateError,
    pull,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dddxb.ingest.uae_realestate_cli", description=__doc__)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER,
                        help=f"RapidAPI provider (default: {DEFAULT_PROVIDER})")
    parser.add_argument("--probe", action="store_true",
                        help="fetch a tiny sample (autocomplete + 1 page sale/rent) and print it")
    parser.add_argument("--query", default="Dubai", help="(probe) autocomplete query string")
    parser.add_argument("--microlocalities", default="Al Jaddaf,Business Bay",
                        help="comma-separated community names to pull")
    parser.add_argument("--months", type=int, default=6, help="trailing window length")
    parser.add_argument("--purposes", default="for-sale,for-rent",
                        help="comma-separated: for-sale (sales) and/or for-rent (Ejari rents)")
    parser.add_argument("--max-pages", type=int, default=200,
                        help="page cap per microlocality×purpose (runaway guard)")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="hard cap on total API calls this run (quota guard)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        client = UAERealEstateClient(provider=args.provider, max_calls=args.max_calls)
    except UAERealEstateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.probe:
            sample = {
                "autocomplete": client.autocomplete(args.query),
                "transactions_for_sale": client.transactions(purpose="for-sale", page=1),
                "transactions_for_rent": client.transactions(purpose="for-rent", page=1),
            }
            print(json.dumps(sample, indent=2, default=str)[:6000])
        else:
            micro = [m.strip() for m in args.microlocalities.split(",") if m.strip()]
            purposes = tuple(s.strip() for s in args.purposes.split(",") if s.strip())
            pull(micro, months=args.months, purposes=purposes,
                 client=client, max_pages=args.max_pages)
    except UAERealEstateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"error: API returned {exc.response.status_code}: "
              f"{exc.response.text[:300]}", file=sys.stderr)
        return 1
    finally:
        print(f"\nAPI calls used this run: {client.calls}")
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
