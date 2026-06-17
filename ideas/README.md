# Ideas

Each idea is a short spec for an analysis or capability — enough to turn into a plan, no more. Capture loosely, refine when you pick one up.

## Format

Filename: `NNNN-short-slug.md` (zero-padded, incrementing). Use this skeleton:

```markdown
# NNNN — Title

**Status:** idea | exploring | building | done
**Created:** YYYY-MM-DD

## Problem
What investor question are we answering, and why it matters.

## Key questions
The concrete questions, with the time windows / segments they apply to.

## Metric definitions
Precise formulas. Pin down every ambiguous term before computing.

## Data needed
Sources, fields, and granularity required (link docs/data-sources.md).

## Method
Pipeline sketch: ingest → clean → features → analysis/forecast. Note where
parallel subagents fit.

## Outputs
Tables, charts, or rankings the analysis should produce.

## Open questions
Unknowns / decisions to resolve.
```

## Index

- [0001 — Rental ROI & total capital gains by microlocality](0001-rental-roi-by-microlocality.md)
