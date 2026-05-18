"""Load KEY=VALUE lines from agent/.env (gitignored) into os.environ."""

from __future__ import annotations

import os
from pathlib import Path

# Straight and “smart” quotes often wrap pasted API keys and break httpx headers.
_WRAP_QUOTES = '"\'"\u201c\u201d\u2018\u2019«»'


def strip_wrapping_quotes(value: str) -> str:
    """Remove surrounding straight or curly quotes from an env value."""
    v = value.strip()
    changed = True
    while changed and v:
        changed = False
        for q in _WRAP_QUOTES:
            if v.startswith(q):
                v = v[len(q) :].lstrip()
                changed = True
            if v.endswith(q):
                v = v[: -len(q)].rstrip()
                changed = True
    return v


def load_env_file(path: Path | None = None) -> bool:
    path = path or Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = strip_wrapping_quotes(value)
        if key and key not in os.environ:
            os.environ[key] = value
    return True
