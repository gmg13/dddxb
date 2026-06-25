"""Lightweight environment/credential loading for ingest.

Reads a local ``.env`` (gitignored) without adding a dependency. Use this to
supply secrets such as ``DUBAI_PULSE_API_KEY`` / ``DUBAI_PULSE_API_SECRET`` and
``BAYUT_API_KEY`` without exporting them in the shell.
"""

from __future__ import annotations

import os
import shlex
import subprocess
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


def get_secret(name: str) -> str | None:
    """Resolve a secret, preferring sources that don't persist it on disk.

    Precedence (first hit wins):
      1. ``$NAME`` already in the environment — e.g. a shell ``export``; never
         touches disk.
      2. ``$NAME_CMD`` — a command whose stdout is the secret, so it can live in a
         password manager (``op read …``, ``pass show …``, ``gopass …``) and never
         be written to a file.
      3. The ``.env`` file — convenience fallback only.

    Returns None if unset. Prefer (1) or (2); ``.env`` is the least-preferred.
    """
    value = os.environ.get(name)
    if value:
        return value
    cmd = os.environ.get(f"{name}_CMD")
    if cmd:
        try:
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True, check=True, timeout=30
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(f"{name}_CMD failed to produce a secret: {exc}") from exc
        return result.stdout.strip() or None
    load_dotenv()
    return os.environ.get(name) or None


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


# Proxy env vars, in priority order. ``DDDXB_PROXY`` is our own knob; the
# conventional ``HTTPS_PROXY``/``HTTP_PROXY`` are honoured as fallbacks so a
# shell-wide proxy also works.
PROXY_ENV = ("DDDXB_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def resolve_proxy(explicit: str | None = None) -> str | None:
    """Return a proxy URL to route Dubai Pulse requests through, or None.

    Used for the UAE residential-proxy route: the keyless CSV host is gated to
    UAE telco (Etisalat/du) networks, so the download must exit through a UAE
    residential IP. Precedence: explicit arg > ``.env``/shell vars in
    ``PROXY_ENV`` order. A typical value looks like
    ``http://user:pass@gate.example.com:7000`` (select country=AE in the
    provider's username params).
    """
    if explicit:
        return explicit
    load_dotenv()
    for name in PROXY_ENV:
        value = os.environ.get(name)
        if value:
            return value
    return None
