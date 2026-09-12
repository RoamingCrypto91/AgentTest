"""Small statistics, written out so every number in a report is traceable."""

from __future__ import annotations

from typing import Optional, Sequence


def median(xs: Sequence) -> Optional[float]:
    vals = sorted(x for x in xs if x is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2


def mean(xs: Sequence) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def quantile(xs: Sequence, q: float) -> Optional[float]:
    """Linear-interpolated quantile.  q in [0, 1]."""
    vals = sorted(x for x in xs if x is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    pos = q * (len(vals) - 1)
    low = int(pos)
    frac = pos - low
    if low + 1 >= len(vals):
        return float(vals[-1])
    return vals[low] * (1 - frac) + vals[low + 1] * frac


def share(part: float, whole: float) -> Optional[float]:
    """A proportion, or None when there is nothing to take a proportion of."""
    if not whole:
        return None
    return part / whole


def gini(xs: Sequence) -> Optional[float]:
    """Inequality of contribution: 0 is everyone equal, 1 is one person.

    Reported because a busy-looking community can be six people, and six
    people is a group chat with a waiting room attached.
    """
    vals = sorted(float(x) for x in xs if x is not None and x >= 0)
    n = len(vals)
    total = sum(vals)
    if n < 2 or total <= 0:
        return None
    weighted = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * weighted) / (n * total) - (n + 1) / n


def bus_factor(counts: Sequence, fraction: float = 0.5) -> Optional[int]:
    """How few people account for `fraction` of the talking."""
    vals = sorted((float(c) for c in counts), reverse=True)
    total = sum(vals)
    if total <= 0:
        return None
    running = 0.0
    for i, v in enumerate(vals, start=1):
        running += v
        if running >= fraction * total:
            return i
    return len(vals)


def top_share(counts: Sequence, n: int = 10) -> Optional[float]:
    vals = sorted((float(c) for c in counts), reverse=True)
    total = sum(vals)
    if total <= 0:
        return None
    return sum(vals[:n]) / total


def trend(series: Sequence) -> Optional[float]:
    """Change from the first third of a series to the last, as a ratio.

    Deliberately crude.  A regression slope on twelve noisy weekly counts
    invites more confidence than the data supports; "the last third is 40%
    down on the first" is a sentence a client can act on.
    """
    vals = [float(v) for v in series]
    if len(vals) < 6:
        return None
    third = max(1, len(vals) // 3)
    first = mean(vals[:third])
    last = mean(vals[-third:])
    if not first:
        return None
    return (last - first) / first
