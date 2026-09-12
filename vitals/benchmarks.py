"""Where the lines are drawn, and how honest those lines are.

Every threshold below is a judgement, not a finding.  They are set from what
is broadly known about online communities -- that most members of any large
community never post, that an unanswered first contribution rarely gets a
second, that a place which goes quiet when the host is away was never a
community -- and they are deliberately kept in one small file so they can be
replaced by real percentiles as audits accumulate.

That replacement is the point.  Ten audited communities produce a benchmark
set nobody else has, and from then on a report can say "your first-post
response rate is in the bottom quartile of the communities we have measured",
which is a far stronger sentence than anything a judgement can support.

`load()` reads an override file so a measured benchmark set can take over
without touching the code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Band:
    """Where a measure stops being a problem and starts being a strength."""

    weak: float
    strong: float
    #: what is typically seen, for the report to quote.  None where a typical
    #: value would be an invention.
    typical: Optional[float] = None
    note: str = ""

    def score(self, value: float, good: str = "high") -> float:
        """0 where the measure is failing, 1 where it is fine, linear between."""
        low, high = (self.weak, self.strong) if good == "high" else (self.strong, self.weak)
        if high == low:
            return 1.0 if value >= high else 0.0
        raw = (value - low) / (high - low)
        raw = max(0.0, min(1.0, raw))
        return raw if good == "high" else 1.0 - raw


#: provisional bands.  See the module docstring before quoting any of these
#: at a client as though they were measured.
BANDS = {
    "never_spoke": Band(0.97, 0.80, typical=0.90,
                        note="most members of any community never post; the "
                             "question is whether that share is 90% or 99%"),
    "activation": Band(0.03, 0.15, typical=0.08),
    "time_to_first_post": Band(72.0, 12.0, typical=24.0,
                               note="read next to `never_spoke`: this counts "
                                    "only people who did eventually post, so a "
                                    "silent community can post a flattering wait"),
    "first_post_answered": Band(0.40, 0.85, typical=0.60,
                                note="the strongest single predictor in the "
                                     "instrument and the cheapest thing to fix"),
    "first_post_answered_by_member": Band(0.10, 0.50, typical=0.25),
    "returned": Band(0.25, 0.55, typical=0.40),
    "retention_30d": Band(0.05, 0.35, typical=0.15),
    "weekly_active_trend": Band(-0.20, 0.10, typical=0.0),
    "member_to_member": Band(0.20, 0.60, typical=0.45),
    "median_partners": Band(3.0, 12.0, typical=6.0),
    "orbit": Band(0.50, 0.15, typical=0.30),
    "conversational": Band(0.15, 0.55, typical=0.35),
    "connectors": Band(3.0, 15.0, typical=8.0),
    "staff_voice": Band(0.50, 0.15, typical=0.30),
    "absence_resilience": Band(0.60, 0.90, typical=0.80),
    "bus_factor": Band(3.0, 15.0, typical=8.0),
    "concentration": Band(0.90, 0.65, typical=0.75),
    "graveyards": Band(0.30, 0.05, typical=0.15),
    "rooms_per_person": Band(10.0, 3.0, typical=5.0),
}

#: set to a path and every band comes from measured data instead
SOURCE = "judgement"


def load(path: str) -> dict:
    """Replace the provisional bands from a JSON file of measured percentiles.

    Expected shape: ``{"measure_key": {"weak": 0.4, "strong": 0.85,
    "typical": 0.6, "note": "..."}}``.  Keys not present keep their
    provisional band, and unknown keys are an error rather than a silent
    no-op, because a typo in a benchmark file should not quietly leave a
    client being judged against a guess.
    """
    global SOURCE
    with open(path) as fh:
        data = json.load(fh)
    unknown = sorted(set(data) - set(BANDS))
    if unknown:
        raise KeyError(f"benchmark file names measures that do not exist: {unknown}")
    for key, band in data.items():
        BANDS[key] = Band(
            float(band["weak"]), float(band["strong"]),
            typical=band.get("typical"), note=band.get("note", ""),
        )
    SOURCE = path
    return BANDS
