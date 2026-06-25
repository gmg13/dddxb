"""Streamlit Community Cloud entrypoint.

Set this file as the app's "Main file path" in Streamlit Cloud. It puts the ``src``
layout on the import path (so we don't have to install the package and drag in heavy
geo/ML deps) and runs the dashboard. Locally you can still use:

    uv run streamlit run src/dddxb/dashboard/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dddxb.dashboard.app import main  # noqa: E402

main()
