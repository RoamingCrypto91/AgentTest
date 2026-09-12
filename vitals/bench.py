"""Turning past audits into measured benchmarks.

This is the part that compounds.  Every audit run produces a set of numbers
for one community; a pile of them produces the distribution, and once the
distribution exists a report can say "your first-post response rate is in the
bottom quartile of the communities we have measured" instead of quoting a
judgement.  Nobody who has not done the audits can say that.

    vitals benchmark audits/*.json -o benchmarks.json
    vitals audit export.json --benchmarks benchmarks.json

The bands are set at the lower and upper quartiles: at or below the lower
quartile is failing, at or above the upper is fine.  That is a choice, and it
is stated in the file it writes so that a report can quote its own basis.
"""

from __future__ import annotations

import json
from typing import Iterable

from . import stats

#: fewer than this and a quartile is a rumour
MINIMUM_AUDITS = 5


def collect(paths: Iterable) -> dict:
    """measure key -> the values seen across a pile of stored audits."""
    seen: dict = {}
    count = 0
    for path in paths:
        with open(path) as fh:
            blob = json.load(fh)
        count += 1
        for key, m in (blob.get("measures") or {}).items():
            if m.get("value") is None:
                continue
            seen.setdefault(key, []).append(float(m["value"]))
    seen["__audits__"] = [count]
    return seen


def bands(seen: dict, minimum: int = MINIMUM_AUDITS) -> dict:
    """Quartile bands, for the measures that have enough data behind them."""
    out: dict = {}
    for key, values in seen.items():
        if key.startswith("__") or len(values) < minimum:
            continue
        low = stats.quantile(values, 0.25)
        high = stats.quantile(values, 0.75)
        mid = stats.median(values)
        if low is None or high is None or low == high:
            continue
        out[key] = {
            "weak": low, "strong": high, "typical": mid,
            "note": f"lower and upper quartile of {len(values)} audited communities",
        }
    return out


def write(paths: Iterable, out_path: str, minimum: int = MINIMUM_AUDITS) -> dict:
    seen = collect(paths)
    built = bands(seen, minimum)
    audits = seen.get("__audits__", [0])[0]
    if not built:
        raise ValueError(
            f"{audits} audit(s) is not enough to set a band for any measure; "
            f"{minimum} are needed. Keep the JSON from every audit you run."
        )
    with open(out_path, "w") as fh:
        json.dump(built, fh, indent=2, sort_keys=True)
    return built
