# Data sources

Verified access notes for transactions, rentals, and listings. Status as of
**2026-06-20**. The ingest code for these lives in `src/dddxb/ingest/`.

> Network note (updated 2026-06-20): `www.dubaipulse.gov.ae` **301-redirects all
> traffic to `data.dubai`** (an Etisalat-internal mirror at `213.42.53.227`) unless
> the request originates on a **UAE telco (Etisalat/du) network**; `data.dubai`
> drops the TLS handshake for any other source. A commercial VPN does **not** help
> — its datacenter IP (e.g. NordVPN/M247) is treated as outside the telco network.
> The gate is by **ISP/ASN, not geolocation**. `api.dubaipulse.gov.ae` is reachable
> globally (returns `401`, not a redirect) but needs a key from the gated portal.
> To skip the gate entirely the project supports a **non-geo-blocked RapidAPI route**
> (see below), and — for a UAE residential proxy — a proxy-aware CSV route
> (`--via csv --proxy …`).

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

## Non-geo-blocked alternative — UAE Real Estate API (RapidAPI)

The Dubai Pulse CSV-dump and registration hosts (`www.dubaipulse.gov.ae`,
`data.dubai`) are gated to **UAE telco (Etisalat/du) networks** — a commercial
VPN's datacenter IP gets 301-redirected to a dead `data.dubai` and fails. The
**API gateway** `api.dubaipulse.gov.ae` is reachable globally but needs a key
minted through that gated portal.

To avoid the geo-gate entirely, this project supports a third-party API that
resells the same DLD data over RapidAPI with no UAE-network requirement:

- **Product:** "UAE Real Estate" — host **`uae-real-estate3.p.rapidapi.com`**
  (bayutapi.dev). Serves DLD sales **transactions** (`purpose=for-sale`) and
  Ejari **rental contracts** (`purpose=for-rent`) as filtered JSON, plus current
  **listings** (`/search-property`) and location **`/autocomplete`**.
- **Auth:** one RapidAPI key in the `X-RapidAPI-Key` header (a single key serves
  every API on your RapidAPI account). **Prefer not to keep secrets on disk:**
  `export RAPIDAPI_KEY=…` for the session, or set `RAPIDAPI_KEY_CMD` to a
  secret-manager command (`op read …`, `pass show …`) so the key never lands in a
  file; `.env` is the convenience fallback (see `.env.example`). Client:
  `src/dddxb/ingest/uae_realestate.py`; CLI: `python -m dddxb.ingest.uae_realestate_cli`.
- **Plans:** Basic **free** (900 req/mo) · Pro **$20** (30,000 req, $0.003 overage)
  · Ultra $60 (100k) · Mega $200 (500k). A microlocality-driven 6–12 month pull
  uses ~1.5–3k calls, so **Pro is ample**; **start on Basic** to probe + validate
  at $0 — upgrading is the same key, no code change.
- **Workflow:** `--probe` (≈3 calls) confirms the exact field names/page size →
  smoke-test 1–2 microlocalities on Basic → **fidelity spot-check** vs a public
  reference (DXBinteract) before trusting figures → then the full pull.
- ⚠️ **Third-party / unofficial.** Validate fidelity before analysis; respect ToS;
  keep dated snapshots. The normaliser maps several likely field names and is
  finalised from the probe output.

### Verified live 2026-06-22 (provider `uae-real-estate2`, the default)

- `POST /transactions` returns `{results:[...], count, page}`; records are nested:
  `amount`, `date` (YYYY-MM-DD), `category`, `property.{type,beds,builtup_area.sqft}`,
  `location.full_location` ("Al Jaddaf -> Azizi David"), `contract.*`. Pages from 0.
- Use the **`time_frame` preset** (1/3/6/12/36m) for **both** purposes. The for-rent
  endpoint **500s on `start_date`/`end_date`** and also **500s intermittently per
  page** — the client retries 5xx with backoff and the pull keeps partial data, so
  rent coverage for some locations can be incomplete (logged as `partial:`).
- ⚠️ **Rent `date` is the contract date (often future), not the registration date.**
  `time_frame` scopes by registration recency server-side, but the exposed date is
  the tenancy term — good for *current market rent level* (yield), not a trailing
  registration window. Confirm semantics in the fidelity check; see
  `ideas/0001` "yield basis" open question.
- Smoke test (Al Jaddaf + Business Bay, 6m): clean canonical parquet, zero nulls;
  sale median ~1.5M AED, rent median ~86k AED/yr — both plausible. One ~200M AED
  sale = likely a bulk deal → rely on cohort medians/outlier rules.

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
