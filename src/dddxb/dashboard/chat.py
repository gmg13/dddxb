"""Grounded Q&A over the investment metrics, via Claude.

The dataset is tiny (tens of microlocalities, ~60 cohorts), so we put the whole
ranking + cohort tables in the system prompt — answers stay grounded in the real
numbers rather than hallucinated. A ``get_trend`` tool lets Claude pull a
microlocality's 4-year monthly AED/sqft series on demand for trend questions.

Needs ANTHROPIC_API_KEY (export > ANTHROPIC_API_KEY_CMD > .env — see
dddxb.ingest.config.get_secret). The model defaults to claude-sonnet-4-6 and can
be overridden with DDDXB_CHAT_MODEL.
"""

from __future__ import annotations

import os

import polars as pl

from dddxb.ingest.config import get_secret

KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
You are a Dubai property investment analyst embedded in the dddxb dashboard. Answer \
questions using ONLY the data tables below — never invent numbers. If the data does \
not cover something, say so plainly.

Definitions (see docs/methodology.md):
- gross_yield = median annual rent / median sale price; net_yield = gross x (1 - 0.20 opex).
- ann_appr = annualized price appreciation from the 4-year date-stratified monthly
  AED/sqft series (RELIABLE). cagr = full-span compound annual growth.
- total_return = net_yield + ann_appr.
- A cohort is (microlocality, property_type, bed_band); "thin" cohorts are low-sample
  and excluded from headline ranking — flag that caveat if asked about them.

Report yields/appreciation as percentages (the table values are fractions, e.g.
0.045 = 4.5%). Be concise and numeric. For trend/history questions, call get_trend.

=== MICROLOCALITY RANKING ===
{ranking}

=== COHORT METRICS ===
{cohorts}
"""

TREND_TOOL = {
    "name": "get_trend",
    "description": (
        "Return the monthly median AED/sqft series for a microlocality over the last "
        "4 years, to answer questions about price trends, momentum, or appreciation timing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "microlocality": {
                "type": "string",
                "description": "Exact microlocality name as it appears in the ranking table.",
            }
        },
        "required": ["microlocality"],
    },
}


class ChatUnavailable(RuntimeError):
    """Raised when the chatbot cannot run (e.g. missing API key)."""


def api_key() -> str | None:
    return get_secret(KEY_ENV)


def _frame_to_text(df: pl.DataFrame | None, max_rows: int = 80) -> str:
    if df is None or df.is_empty():
        return "(no data — run the pipeline first)"
    with pl.Config(tbl_rows=max_rows, tbl_cols=-1, float_precision=4, tbl_width_chars=200):
        return str(df.head(max_rows))


def build_system_prompt(ranking: pl.DataFrame | None, cohorts: pl.DataFrame | None) -> str:
    return SYSTEM_PROMPT.format(
        ranking=_frame_to_text(ranking),
        cohorts=_frame_to_text(cohorts, max_rows=120),
    )


def _trend_text(monthly: pl.DataFrame | None, microlocality: str) -> str:
    if monthly is None or monthly.is_empty():
        return "No 4-year history available — run the --history ingest."
    g = monthly.filter(pl.col("microlocality") == microlocality).sort("ym")
    if g.is_empty():
        names = ", ".join(sorted(monthly["microlocality"].unique().to_list()))
        return f"No history for '{microlocality}'. Available: {names}"
    rows = [f"{r['ym']}: {r['psf']:.0f} AED/sqft (n={r['n']})" for r in g.iter_rows(named=True)]
    return "Monthly median AED/sqft:\n" + "\n".join(rows)


def answer(
    history: list[dict],
    *,
    ranking: pl.DataFrame | None,
    cohorts: pl.DataFrame | None,
    monthly: pl.DataFrame | None,
    model: str | None = None,
) -> str:
    """Run one chat turn. ``history`` is a list of {role, content} message dicts."""
    key = api_key()
    if not key:
        raise ChatUnavailable(
            "ANTHROPIC_API_KEY not set. Export it, set ANTHROPIC_API_KEY_CMD to a "
            "secret-manager command, or add it to .env (gitignored)."
        )
    # Imported lazily so the rest of the dashboard works without the SDK installed.
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    model = model or os.environ.get("DDDXB_CHAT_MODEL", DEFAULT_MODEL)
    system = build_system_prompt(ranking, cohorts)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    # Tool-use loop: keep resolving get_trend calls until Claude returns prose.
    for _ in range(5):
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system,
            tools=[TREND_TOOL], messages=messages,
        )
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use" and block.name == "get_trend":
                text = _trend_text(monthly, block.input.get("microlocality", ""))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})
        messages.append({"role": "user", "content": results})
    return "Sorry — I couldn't resolve that question against the data."
