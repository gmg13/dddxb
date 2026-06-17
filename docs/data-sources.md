# Data sources

Leads for transactions, rentals, and listings. **Verify access, licensing, and field availability before relying on any of these** — use the claudeOS `/ingest-kb` skill to catalogue and pin them down properly.

## Transactions (primary)
- **DLD via Dubai Pulse** (`dubaipulse.gov.ae`) — official Dubai Land Department open datasets: sales transactions, with date, price, area/community, property type, size, rooms. Likely the backbone dataset.
- **DXBinteract** — derived analytics/visualization over DLD data; useful for sanity-checking, not as a raw source.

## Rentals
- **Ejari registrations (via Dubai Pulse / DLD)** — registered tenancy contracts → actual annual rents for yield calculations.

## Listings (current market)
- **Property Finder**, **Bayut**, **Dubizzle** — live asking prices and rents. Access via their data products or scraping (check terms). Captures the market beyond registered transactions.

## Geometry
- Community / sub-community boundary polygons for Dubai (for defining and mapping microlocalities) — source TBD; Dubai Pulse spatial layers are a candidate.

## Notes
- Prefer official/stable sources (reference, don't re-host) for transactions; snapshot volatile listing data with retrieval dates.
- Record schema, licensing, and update cadence for each source as it's adopted.
