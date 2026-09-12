"""Turning one platform's export into the facts that matter.

Each adapter's job is small and the same: emit `schema.Event`s, keep the
structure, throw the content away.  Adding a platform means writing one
function, which is the whole reason the instrument is built this way round.
"""

from __future__ import annotations

import os

from . import csvfile, discord, slack

ADAPTERS = {
    "csv": csvfile,
    "discord": discord,
    "slack": slack,
}


def detect(path: str) -> str:
    """Guess the format from what is on disk.  Explicit beats this."""
    if os.path.isdir(path):
        return "slack" if slack.looks_like(path) else "discord"
    if path.lower().endswith((".csv", ".tsv")):
        return "csv"
    if path.lower().endswith(".json"):
        return "slack" if slack.looks_like(path) else "discord"
    raise ValueError(f"cannot tell what {path!r} is; pass --format")


def load(path: str, fmt: str = "", **kw):
    fmt = fmt or detect(path)
    if fmt not in ADAPTERS:
        raise ValueError(f"no adapter for {fmt!r}; have {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[fmt].load(path, **kw)
