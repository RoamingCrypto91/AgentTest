"""The model: five gates a person passes through, and where they leak.

A scorecard tells you a community is a 63 out of 100.  Nobody can act on
that.  What can be acted on is knowing *which* of a small number of things
is failing, and knowing that fixing a later one is wasted while an earlier
one leaks.

So the model is a chain, not a dashboard.  Someone arrives, says something,
comes back, comes to know people, starts doing the work, and eventually holds
the place up.  Each step has a gate, and each gate has a mechanism that makes
it fail.  The instrument reports the *earliest* failing gate and calls it the
binding constraint, because that is the only place where effort pays.

The stages are not invented here.  They are the reader-to-leader progression
(Preece and Shneiderman), legitimate peripheral participation (Lave and
Wenger), and the four components of sense of community -- membership,
influence, shared emotional connection, fulfilment of needs (McMillan and
Chavis) -- arranged so that each one can be measured from message metadata.
What is new is only the arrangement and the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .benchmarks import BANDS

HOLDING = "holding"
STRAINED = "strained"
FAILING = "failing"
UNKNOWN = "unknown"

#: gate scores at or above this are holding; below the lower one, failing
HOLDING_AT = 0.62
FAILING_BELOW = 0.34


@dataclass(frozen=True)
class Gate:
    key: str
    name: str
    #: the question the gate answers, in the words a client would use
    question: str
    #: why it fails, stated as a mechanism rather than a platitude
    mechanism: str
    #: measures that decide the verdict
    primary: tuple
    #: measures that colour it in but do not decide it
    supporting: tuple = ()


GATES = (
    Gate(
        "arrival", "Arrival",
        "Does a new member ever say anything, and does anybody answer?",
        "A first contribution that nobody acknowledges is the cheapest way to "
        "lose a member and the most common thing wrong with a quiet community. "
        "The person concludes, correctly, that nobody was waiting for them.",
        primary=("never_spoke", "activation", "first_post_answered"),
        supporting=("time_to_first_post",),
    ),
    Gate(
        "return", "Return",
        "Is there a reason to come back next week?",
        "A single visit is an event, not a habit. Habits need a cue that "
        "recurs on a predictable rhythm; without one, attention decays to "
        "nothing and the member never consciously decides to leave.",
        primary=("returned", "retention_30d"),
        supporting=("weekly_active_trend",),
    ),
    Gate(
        "relationship", "Relationship",
        "Does anybody here know anybody other than you?",
        "This is the difference between an audience standing in a room "
        "together and a community. If every thread of connection runs through "
        "the host, the members have nothing holding them except the host, and "
        "the place is a broadcast with a comment section.",
        primary=("member_to_member", "orbit", "median_partners"),
        supporting=("conversational",),
    ),
    Gate(
        "contribution", "Contribution",
        "Is anyone doing the work besides you?",
        "People who answer others acquire standing, and standing is what makes "
        "leaving expensive. Where staff answer everything, there is no standing "
        "available, so nobody earns any, so nobody stays for it.",
        primary=("connectors", "staff_voice", "first_post_answered_by_member"),
    ),
    Gate(
        "stewardship", "Stewardship",
        "Does the place survive a fortnight of you being busy?",
        "Until the answer is yes, the community is a job. This is the gate "
        "that decides whether what you built is an asset or an obligation.",
        primary=("absence_resilience", "bus_factor"),
    ),
)

#: not a stage anyone passes through, but conditions that quietly sabotage
#: every stage at once
STRUCTURE = Gate(
    "structure", "Structure",
    "Does the shape of the place work against it?",
    "Empty rooms are evidence to a newcomer that nobody lives here, and a "
    "server with more rooms than conversations manufactures that evidence. "
    "Concentration is the other half: activity carried by a handful of people "
    "is one bad week away from silence.",
    primary=("graveyards", "rooms_per_person", "concentration"),
)


@dataclass
class Finding:
    """One measure, judged."""

    measure: object
    score: Optional[float] = None
    band: object = None

    @property
    def verdict(self) -> str:
        if self.score is None:
            return UNKNOWN
        if self.score >= HOLDING_AT:
            return HOLDING
        if self.score < FAILING_BELOW:
            return FAILING
        return STRAINED


@dataclass
class GateResult:
    gate: Gate
    findings: list = field(default_factory=list)
    supporting: list = field(default_factory=list)

    @property
    def score(self) -> Optional[float]:
        scored = [f.score for f in self.findings if f.score is not None]
        return sum(scored) / len(scored) if scored else None

    @property
    def verdict(self) -> str:
        s = self.score
        if s is None:
            return UNKNOWN
        if s >= HOLDING_AT:
            return HOLDING
        if s < FAILING_BELOW:
            return FAILING
        return STRAINED

    @property
    def worst(self) -> Optional[Finding]:
        scored = [f for f in self.findings if f.score is not None]
        return min(scored, key=lambda f: f.score) if scored else None


def judge(measures: dict, gate: Gate) -> GateResult:
    result = GateResult(gate)
    for keys, into in ((gate.primary, result.findings),
                       (gate.supporting, result.supporting)):
        for key in keys:
            m = measures.get(key)
            if m is None:
                continue
            band = BANDS.get(key)
            score = band.score(m.value, m.good) if (band and m.measured) else None
            into.append(Finding(m, score, band))
    return result


def assess(measures: dict) -> dict:
    """Judge every gate, and name the one that matters."""
    gates = [judge(measures, g) for g in GATES]
    structure = judge(measures, STRUCTURE)
    return {
        "gates": gates,
        "structure": structure,
        "binding": binding_constraint(gates),
        "unknowns": [m for m in measures.values() if not m.measured],
    }


def binding_constraint(gates: list) -> Optional[GateResult]:
    """The earliest gate that is not holding.

    Earliest, not worst. A community whose Relationship gate is catastrophic
    and whose Arrival gate is merely strained should still fix Arrival first,
    because everyone being poured into the relationship stage is arriving
    through the leak.
    """
    for result in gates:
        if result.verdict in (FAILING, STRAINED):
            return result
    return None
