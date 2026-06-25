"""dddxb dashboard — microlocality investment metrics.

    uv run streamlit run src/dddxb/dashboard/app.py

Reads processed parquet (run ``python -m dddxb.features --months 12`` first) and the
4-year sale-history sample. Tabs: Overview, Microlocality detail, Cohort explorer, Ask.
"""

from __future__ import annotations

import os

import plotly.express as px
import streamlit as st

from dddxb.dashboard import chat, data, samples

st.set_page_config(page_title="dddxb — Dubai property analytics", layout="wide")

PCT_COLS = ("gross_yield", "net_yield", "ann_appr", "cagr", "total_return")
# Secrets bridged from st.secrets (Streamlit Cloud) into os.environ so the chatbot's
# config.get_secret() resolves them like a local export.
_BRIDGED_SECRETS = ("ANTHROPIC_API_KEY",)


def _secret(name: str):
    """Read a Streamlit secret without exploding when no secrets file exists."""
    try:
        return st.secrets.get(name)
    except Exception:
        return None


def _bridge_secrets() -> None:
    for name in _BRIDGED_SECRETS:
        val = _secret(name)
        if val and not os.environ.get(name):
            os.environ[name] = str(val)


# Cap chatbot questions per browser session to bound API spend on a shared deploy.
# Override with a `max_chat_messages` secret.
DEFAULT_CHAT_LIMIT = 20


def _chat_limit() -> int:
    val = _secret("max_chat_messages")
    try:
        return max(1, int(val))
    except (TypeError, ValueError):
        return DEFAULT_CHAT_LIMIT


def _gate() -> bool:
    """Password gate when ``app_password`` is set as a secret; open otherwise (local)."""
    expected = _secret("app_password")
    if not expected:
        return True
    if st.session_state.get("_authed"):
        return True
    st.title("🔒 dddxb dashboard")
    pw = st.text_input("Password", type="password")
    if pw == expected:
        st.session_state["_authed"] = True
        st.rerun()
    elif pw:
        st.error("Incorrect password.")
    return False


def _pct(df, cols):
    """Round fractional metric columns to percentages for display (pandas frame in/out)."""
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = (out[c] * 100).round(2)
    return out


def _no_data(window: int) -> None:
    st.warning(
        f"No ranking found for the {window}-month window. Run the pipeline first:\n\n"
        f"```\nuv run python -m dddxb.features --months {window}\n```"
    )


def main() -> None:
    _bridge_secrets()
    if not _gate():
        return
    st.title("🏙️ dddxb — Dubai property investment analytics")
    st.caption(
        "Per-microlocality rental yield, price appreciation, and total return. "
        "Appreciation & CAGR come from the 4-year date-stratified history (reliable); "
        "short 3/6-month windows are noisy. Methodology: `docs/methodology.md`."
    )

    windows = data.available_windows()
    if not windows:
        _no_data(12)
        st.stop()

    with st.sidebar:
        st.header("Filters")
        window = st.selectbox("Window (months)", windows, index=0)
        show_thin = st.checkbox("Include thin cohorts", value=False)
        top_n = st.slider("Top N", min_value=5, max_value=40, value=15)
        if not data.history_available():
            st.info("4-year history not found yet — appreciation uses the half-window "
                    "approximation until the `--history` pull lands.")

    ranking = data.load_ranking(window)
    cohorts = data.load_cohorts(window)
    if ranking is None:
        _no_data(window)
        st.stop()

    tab_overview, tab_yield, tab_validate, tab_detail, tab_cohort, tab_ask = st.tabs(
        ["📊 Overview", "💰 Yield", "✅ Validate", "📍 Microlocality", "🧩 Cohorts", "💬 Ask"]
    )

    with tab_overview:
        _overview(ranking, top_n)
    with tab_yield:
        _yield(ranking, top_n)
    with tab_validate:
        _validate(ranking, window)
    with tab_detail:
        _detail(ranking, cohorts)
    with tab_cohort:
        _cohorts(cohorts, show_thin)
    with tab_ask:
        _ask(ranking, cohorts)


def _overview(ranking, top_n: int) -> None:
    rk = ranking.to_pandas()

    st.subheader("🏆 Top 5 microlocalities (by total return)")
    st.caption("Total return = net rental yield + annualized price appreciation (4y "
               "log-linear CAGR). Confidence = R² of the price-trend fit; low R² means "
               "the community's prices are noisy/mixed — lean on the Validate tab.")
    top5 = rk.sort_values("total_return", ascending=False).head(5)
    for _, row in top5.iterrows():
        cols = st.columns([2, 1, 1, 1, 1])
        conf = ""
        if "appr_r2" in row and row["appr_r2"] is not None:
            r2 = row["appr_r2"]
            badge = "🟢" if r2 >= 0.6 else ("🟡" if r2 >= 0.4 else "🔴")
            conf = f"{badge} R²={r2:.2f}"
        cols[0].markdown(f"**{row['microlocality']}**  \n{conf}")
        cols[1].metric("Net yield", f"{row['net_yield'] * 100:.1f}%")
        cols[2].metric("Appreciation", f"{row['ann_appr'] * 100:.1f}%"
                       if row.get("ann_appr") is not None else "—")
        cols[3].metric("Total return", f"{row['total_return'] * 100:.1f}%")
        cols[4].metric("Med AED/sqft", f"{row['med_psf']:,.0f}")
    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("Microlocalities", len(rk))
    c2.metric("Median net yield", f"{rk['net_yield'].median() * 100:.2f}%")
    if "ann_appr" in rk:
        c3.metric("Median appreciation", f"{rk['ann_appr'].median() * 100:.2f}%")

    st.subheader("Yield vs. appreciation")
    st.caption("Bubble size = transaction volume; colour = total return. Top-right = "
               "high yield *and* growth.")
    if "ann_appr" in rk.columns:
        size = rk[["n_sales", "n_rents"]].min(axis=1).clip(lower=1)
        fig = px.scatter(
            rk.assign(volume=size, net_yield_pct=rk["net_yield"] * 100,
                      ann_appr_pct=rk["ann_appr"] * 100, total_return_pct=rk["total_return"] * 100),
            x="net_yield_pct", y="ann_appr_pct", size="volume", color="total_return_pct",
            hover_name="microlocality", color_continuous_scale="Viridis",
            labels={"net_yield_pct": "Net yield (%)", "ann_appr_pct": "Annualized appreciation (%)",
                    "total_return_pct": "Total return (%)"},
        )
        st.plotly_chart(fig, width="stretch")

    st.subheader(f"Top {top_n} by total return")
    ranked = _pct(rk.sort_values("total_return", ascending=False).head(top_n), PCT_COLS)
    fig2 = px.bar(ranked, x="total_return", y="microlocality", orientation="h",
                  labels={"total_return": "Total return (%)", "microlocality": ""})
    fig2.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig2, width="stretch")

    st.subheader("Ranking")
    st.dataframe(ranked, width="stretch", hide_index=True)
    st.download_button("Download ranking (CSV)", ranked.to_csv(index=False),
                       file_name="microlocality_ranking.csv", mime="text/csv")


def _yield(ranking, top_n: int) -> None:
    st.subheader(f"💰 Top {top_n} yield plays (highest net rental yield)")
    st.caption("Ranked by **net rental yield** = gross rent ÷ price × (1 − 20% opex). "
               "The appreciation / total-return columns show how the **capital-gain** "
               "side looks for each play — high yield doesn't always come with high "
               "price growth, so check both before committing.")
    rk = ranking.to_pandas().sort_values("net_yield", ascending=False).head(top_n)

    c1, c2, c3 = st.columns(3)
    c1.metric("Best net yield", f"{rk['net_yield'].max() * 100:.2f}%")
    c2.metric("Median net yield (top)", f"{rk['net_yield'].median() * 100:.2f}%")
    if "ann_appr" in rk.columns:
        c3.metric("Median appreciation (top)", f"{rk['ann_appr'].median() * 100:.2f}%")

    st.subheader("Yield vs. capital gain, side by side")
    value_cols = [c for c in ("net_yield", "ann_appr") if c in rk.columns]
    melt = rk.melt(id_vars="microlocality", value_vars=value_cols,
                   var_name="metric", value_name="val")
    melt["val"] = (melt["val"] * 100).round(2)
    melt["metric"] = melt["metric"].map({"net_yield": "Net yield",
                                         "ann_appr": "Appreciation (capital gain)"})
    order = rk.sort_values("net_yield")["microlocality"].tolist()  # highest at top
    fig = px.bar(melt, x="val", y="microlocality", color="metric", orientation="h",
                 barmode="group", labels={"val": "% per year", "microlocality": "", "metric": ""})
    fig.update_yaxes(categoryorder="array", categoryarray=order)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Detail")
    cols = ["microlocality", "n_sales", "n_rents", "gross_yield", "net_yield",
            "ann_appr", "total_return", "appr_r2", "med_psf"]
    table = _pct(rk[[c for c in cols if c in rk.columns]], PCT_COLS)
    if "appr_r2" in table.columns:
        table["appr_r2"] = table["appr_r2"].round(2)
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption("`appr_r2` is the price-trend fit confidence (0–1); low values mean the "
               "capital-gain figure is noisy — open the Microlocality tab for its 4-year "
               "price trend, or the Validate tab for sample buildings.")
    st.download_button("Download yield plays (CSV)", table.to_csv(index=False),
                       file_name="yield_plays.csv", mime="text/csv")


def _validate(ranking, window: int) -> None:
    st.subheader("Validate the numbers — sample big-developer properties")
    st.caption("For each top-5 microlocality: actual DLD-transacted units from the "
               "highest-volume developments (the big branded projects, not small "
               "standalone buildings). Developer tagged best-effort by name. "
               "*Implied yield* = development median annual rent ÷ median sale price — "
               "compare it to the microlocality's headline net yield.")
    sales = data.load_clean_sales(window)
    rents = data.load_clean_rents(window)
    if sales is None:
        st.warning(f"No cleaned sales for the {window}m window — run "
                   f"`uv run python -m dddxb.features --months {window}`.")
        return

    top5 = ranking.sort("total_return", descending=True).head(5)
    for i, row in enumerate(top5.iter_rows(named=True)):
        name = row["microlocality"]
        head = (f"{name} — net yield {row['net_yield'] * 100:.1f}%, "
                f"appreciation {row['ann_appr'] * 100:.1f}%"
                if row.get("ann_appr") is not None else f"{name}")
        with st.expander(head, expanded=(i == 0)):
            samp = samples.sample_properties(sales, rents, name)
            if samp.is_empty():
                st.info("No development with enough transactions to sample.")
                continue
            disp = samp.to_pandas()
            disp["dev_impl_yield"] = (disp["dev_impl_yield"] * 100).round(2)
            disp["price"] = disp["price"].map(lambda v: f"{v:,.0f}")
            disp = disp.rename(columns={
                "dev_n_sales": "dev #sales", "dev_med_psf": "dev med psf",
                "dev_impl_yield": "implied yield %", "psf": "unit psf"})
            st.dataframe(disp, width="stretch", hide_index=True)


def _detail(ranking, cohorts) -> None:
    names = ranking["microlocality"].to_list()
    name = st.selectbox("Microlocality", names)
    row = ranking.filter(ranking["microlocality"] == name).to_pandas().iloc[0]

    cols = st.columns(5)
    cols[0].metric("Net yield", f"{row['net_yield'] * 100:.2f}%")
    if "ann_appr" in row:
        cols[1].metric("Appreciation (ann.)", f"{row['ann_appr'] * 100:.2f}%")
    if "cagr" in row and row["cagr"] is not None:
        cols[2].metric("4y CAGR", f"{row['cagr'] * 100:.2f}%")
    cols[3].metric("Total return", f"{row['total_return'] * 100:.2f}%")
    cols[4].metric("Median AED/sqft", f"{row['med_psf']:,.0f}")

    st.subheader("Price trend — monthly median AED/sqft")
    monthly = data.load_monthly_psf()
    if monthly is None:
        st.info("Run the `--history` ingest to populate the 4-year price trend.")
    else:
        g = monthly.filter(monthly["microlocality"] == name).sort("ym").to_pandas()
        if g.empty:
            st.info(f"No monthly history for {name} yet.")
        else:
            g["rolling_3m"] = g["psf"].rolling(3, min_periods=1).mean()
            fig = px.line(g, x="ym", y=["psf", "rolling_3m"],
                          labels={"ym": "Month", "value": "AED/sqft", "variable": ""})
            st.plotly_chart(fig, width="stretch")

    if cohorts is not None:
        st.subheader("Cohorts here")
        sub = cohorts.filter(cohorts["microlocality"] == name).to_pandas()
        st.dataframe(_pct(sub, PCT_COLS), width="stretch", hide_index=True)


def _cohorts(cohorts, show_thin: bool) -> None:
    if cohorts is None:
        st.info("No cohort metrics found — run the pipeline.")
        return
    df = cohorts if show_thin else cohorts.filter(~cohorts["thin"])
    pdf = df.to_pandas()
    c1, c2 = st.columns(2)
    ptypes = sorted(pdf["property_type"].dropna().unique().tolist())
    beds = sorted(pdf["bed_band"].dropna().unique().tolist())
    sel_p = c1.multiselect("Property type", ptypes, default=ptypes)
    sel_b = c2.multiselect("Bed band", beds, default=beds)
    pdf = pdf[pdf["property_type"].isin(sel_p) & pdf["bed_band"].isin(sel_b)]

    st.subheader("Net yield heatmap (microlocality × bed band)")
    if not pdf.empty:
        pivot = pdf.pivot_table(index="microlocality", columns="bed_band",
                                values="net_yield", aggfunc="median") * 100
        fig = px.imshow(pivot, text_auto=".1f", aspect="auto", color_continuous_scale="Viridis",
                        labels={"color": "Net yield (%)"})
        st.plotly_chart(fig, width="stretch")

    st.subheader("Cohort table")
    table = _pct(pdf.sort_values("total_return", ascending=False), PCT_COLS)
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button("Download cohorts (CSV)", table.to_csv(index=False),
                       file_name="cohort_metrics.csv", mime="text/csv")


def _ask(ranking, cohorts) -> None:
    st.subheader("Ask about the data")
    st.caption("Grounded in the ranking + cohort tables; trend questions pull the 4-year "
               "AED/sqft series. Powered by Claude.")
    if chat.api_key() is None:
        st.warning(
            "Set `ANTHROPIC_API_KEY` to enable the chatbot — export it, set "
            "`ANTHROPIC_API_KEY_CMD` to a secret-manager command, or add it to `.env`."
        )
        return

    monthly = data.load_monthly_psf()
    if "chat" not in st.session_state:
        st.session_state.chat = []
    for msg in st.session_state.chat:
        st.chat_message(msg["role"]).markdown(msg["content"])

    limit = _chat_limit()
    asked = sum(1 for m in st.session_state.chat if m["role"] == "user")
    st.caption(f"Questions this session: {asked}/{limit}")
    if asked >= limit:
        st.info(f"Session question limit reached ({limit}). Refresh the page to start over.")
        return

    if prompt := st.chat_input("e.g. Which microlocality has the best total return for 1BR?"):
        st.session_state.chat.append({"role": "user", "content": prompt})
        st.chat_message("user").markdown(prompt)
        with st.chat_message("assistant"), st.spinner("Thinking…"):
            try:
                reply = chat.answer(st.session_state.chat, ranking=ranking,
                                    cohorts=cohorts, monthly=monthly)
            except chat.ChatUnavailable as exc:
                reply = f"⚠️ {exc}"
            except Exception as exc:  # noqa: BLE001 - surface API errors in the UI
                reply = f"⚠️ Chat error: {exc}"
            st.markdown(reply)
        st.session_state.chat.append({"role": "assistant", "content": reply})
        st.rerun()


if __name__ == "__main__":
    main()
