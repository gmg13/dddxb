# Data sources

Verified access notes for transactions, rentals, and listings. Status as of
**2026-06-18**. The ingest code for these lives in `src/dddxb/ingest/`.

> Network note: from the current dev machine, `www.dubaipulse.gov.ae` (the keyless
> CSV-dump host) is **firewalled** (TCP connect times out), while
> `api.dubaipulse.gov.ae` (the open-data API host) **is reachable**. Other UAE sites
> (e.g. `dubailand.gov.ae`) work, so this is a host-specific block, not a geo-block.
> The project therefore defaults to the **API route** (`--via api`).

## Transactions (primary) — DLD via Dubai Pulse *open data*

Official Dubai Land Department sales transactions: date, price (`actual_worth`),
area/community, property type, size, rooms. Two equivalent free routes:

- **Open API** (default): `https://api.dubaipulse.gov.ae/open/dld/dld_transactions-open-api`
  — bearer-token auth, free key (see registration below). Reachable from here.
- **CSV full dump** (keyless): `https://www.dubaipulse.gov.ae/dataset/3b25a6f5-9077-49d7-8a1e-bc6d5dea88fd/resource/a37511b0-ea36-485d-bccd-2d6cb24507e7/download/transactions.csv`
  — full history; filter to the window after download. Host currently firewalled here.

Dataset page: <https://www.dubaipulse.gov.ae/data/dld-transactions/dld_transactions-open>

## Rentals — Ejari registered contracts via Dubai Pulse *open data*

Registered tenancy contracts → actual annual rents for yield. Same two routes:

- **Open API**: `https://api.dubaipulse.gov.ae/open/dld/dld_rent_contracts-open-api`
- **CSV full dump**: `https://www.dubaipulse.gov.ae/dataset/00768c45-f014-4cc6-937d-2b17dcab53fb/resource/765b5a69-ca16-4bfd-9852-74612f3c4ea6/download/rent_contracts.csv`

Dataset page: <https://www.dubaipulse.gov.ae/data/dld-registration/dld_rent_contracts-open>

### How to get the free Dubai Pulse open-data API key

1. Create a free account at <https://www.dubaipulse.gov.ae/> (Digital Dubai / UAE Pass).
2. Open each dataset's **`-open-api`** page (linked above) and request/grant access.
   Open datasets are free; the grant is self-serve.
3. Dubai Pulse emails an **API Key** and an **API Secret** (two separate emails).
4. Put them in `.env` (gitignored) as:
   ```
   DUBAI_PULSE_API_KEY=...
   DUBAI_PULSE_API_SECRET=...
   ```
5. Run the pull (probe first to confirm schema, then fetch the window):
   ```
   uv run python -m dddxb.ingest --via api --probe
   uv run python -m dddxb.ingest --via api --months 6
   ```

Auth mechanics (verified): token endpoint
`https://api.dubaipulse.gov.ae/oauth/client_credential/accesstoken?grant_type=client_credentials`
is an Apigee service taking **HTTP Basic auth** (key:secret) + an empty form body;
the returned bearer token is short-lived and sent as `Authorization: Bearer <token>`.

### ⚠️ Not the same as the DLD API Gateway

`dubailand.gov.ae/en/eservices/api-gateway/` is a **commercial** gateway (Mollak,
Ejari lifecycle, Trakheesi, Rental Index, …) priced at **AED 30,000 + VAT per API
per year** and gated on a Dubai trade licence + system registration. Its "Ejari" API
manages contract lifecycle and "Rental Index" returns aggregated indices — neither
provides bulk historical microdata. **Do not use it for this project**; the free
Dubai Pulse open data above is the correct source.

## Listings (current market) — Bayut / Property Finder / Dubizzle

Live asking prices/rents. **There is no official public Bayut _listings_ data API.**

- **Official route (enterprise data licensing):** contact Bayut / dubizzle Group's
  business / data team to negotiate a commercial listings-data agreement. Likely
  paid and may require partner/agency status. This is the only *official* path to
  listings data. (Bayut's documented official API is the **Leads API** — advertiser
  lead capture only, not listings — so it does not serve this use case.)
- **Practical route (unofficial RapidAPI "BayutAPI"):** create a RapidAPI account →
  subscribe to BayutAPI (free tier ~750 calls/mo, paid tiers above) → copy the
  `X-RapidAPI-Key` → set `BAYUT_API_KEY` (and optionally `BAYUT_API_HOST`) in `.env`.
  Unofficial, not endorsed by Bayut, with ToS/anti-bot/rate-limit risk — suitable for
  a snapshot, not a production feed. Client: `src/dddxb/ingest/bayut.py` (inert until a
  key is set); confirm endpoint/params against your provider's docs before first use.
- **Property Finder / Dubizzle:** no official API; scraping violates ToS. Not pursued.

## Derived / sanity-check

- **DXBinteract** — analytics/visualisation over DLD data; useful for cross-checking
  computed figures, not as a raw source.

## Geometry (not yet ingested)

- Community / sub-community boundary polygons for microlocality mapping and choropleths
  — source TBD; Dubai Pulse spatial layers are the candidate. Cataloged for a later pass.

## Immutability calls

- DLD/Dubai Pulse open datasets — **stable government URLs → reference, don't re-host.**
  Raw pulls land in `data/raw/` (gitignored, never committed); regenerate via the CLI.
- Listings — **volatile → snapshot with retrieval dates** under `data/raw/bayut/`.
