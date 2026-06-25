# Deploying the dashboard (Streamlit Community Cloud, free)

The dashboard deploys to [Streamlit Community Cloud](https://share.streamlit.io) at
zero cost. It auto-redeploys on every push to the configured branch.

## What's already wired for deploy

- **`streamlit_app.py`** (repo root) — the entrypoint Streamlit Cloud runs. Puts
  `src/` on the path and calls `dddxb.dashboard.app:main` (no package install, so the
  heavy geo/ML deps are skipped).
- **`requirements.txt`** — minimal runtime deps (streamlit, plotly, polars, pandas,
  pyarrow, numpy, anthropic). This is what Streamlit Cloud installs.
- **`data/published/`** — a ~1.6 MB committed snapshot of the derived outputs the app
  needs (rankings, cohort metrics, cleaned transactions for the Validate tab, 4y price
  history). The loaders use the live `data/` tree when present (local dev) and fall
  back to this snapshot on the deploy. Refresh it with:

  ```bash
  uv run python -m dddxb.features --months 12   # regenerate outputs
  uv run python scripts/publish_data.py          # copy into data/published/
  git add data/published && git commit -m "Refresh published data" && git push
  ```

## One-time setup

1. Push this repo to GitHub (done — `master`).
2. Go to https://share.streamlit.io → **New app** → pick this repo, branch `master`,
   main file `streamlit_app.py`.
3. In **Advanced settings → Secrets**, paste (see `.streamlit/secrets.toml.example`):

   ```toml
   app_password = "choose-a-shared-password"
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```

   - `app_password` gates the whole app (anyone with the link must enter it). Remove it
     to make the app fully public.
   - `ANTHROPIC_API_KEY` powers the **Ask** chatbot. Omit it to disable chat (the rest
     of the app still works). Note: everyone who has the password shares this key's
     usage — set a spend limit in the Anthropic console.
4. **Deploy.** First build takes a couple of minutes; subsequent pushes redeploy
   automatically. The app sleeps after inactivity and cold-starts (~30s) on next visit.

## Notes

- Secrets live only in Streamlit Cloud's settings — never commit `.streamlit/secrets.toml`
  (it's gitignored). `app.py` bridges `st.secrets` into the env so the chatbot's
  `config.get_secret()` resolves the key the same way as a local `export`.
- The published snapshot contains transaction-level rows (Validate tab). Keep the app
  password-gated unless you've confirmed republishing that data publicly is acceptable
  under the upstream data provider's terms.
