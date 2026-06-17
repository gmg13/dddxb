"""Lightweight environment/credential loading for ingest.

Reads a local ``.env`` (gitignored) without adding a dependency. Use this to
supply secrets such as ``DUBAI_PULSE_API_KEY`` / ``DUBAI_PULSE_API_SECRET`` and
``BAYUT_API_KEY`` without exporting them in the shell.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(".env")


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Load ``KEY=VALUE`` lines from ``path`` into os.environ (no overwrite).

    Ignores blanks, ``#`` comments, and ``export`` prefixes. Existing env vars
    take precedence, so shell exports still win.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require(*names: str) -> tuple[str, ...]:
    """Return the values of the named env vars, raising if any are missing."""
    load_dotenv()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            f"missing required environment variable(s): {', '.join(missing)}. "
            "Set them in .env (gitignored) or export them; see docs/data-sources.md."
        )
    return tuple(os.environ[n] for n in names)
