"""One audit, end to end.

Load, build the graph, measure, judge, prescribe, and pull out the handful of
specifics somebody can act on this afternoon: who the connectors are, which
rooms are graveyards, and what has moved since the first half of the window.

The half-over-half comparison earns its place.  A single set of numbers tells
a client where they stand; the change tells them whether what they are
already doing is working, and it is the most persuasive thing in the report
for a community that is quietly sliding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from . import benchmarks, metrics, model, prescribe
from .interactions import derive
from .metrics import CONNECTOR_PARTNERS, CONNECTOR_WEEKS, GRAVEYARD_DAYS, Facts
from .schema import Community

#: a measure has to move by more than this for the report to mention it
MATERIAL_CHANGE = 0.10


@dataclass
class Person:
    actor: str
    name: str
    messages: int
    partners: int
    weeks: int
    staff: bool = False


@dataclass
class Change:
    key: str
    label: str
    before: float
    after: float
    good: str = "high"

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def relative(self) -> Optional[float]:
        if not self.before:
            return None
        return (self.after - self.before) / abs(self.before)

    @property
    def better(self) -> bool:
        return (self.delta > 0) if self.good == "high" else (self.delta < 0)


@dataclass
class Audit:
    community: Community
    measures: dict
    assessment: dict
    plan: dict
    connectors: list = field(default_factory=list)
    quiet_regulars: list = field(default_factory=list)
    graveyards: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    weekly: list = field(default_factory=list)
    benchmark_source: str = benchmarks.SOURCE

    @property
    def binding(self):
        return self.assessment.get("binding")

    @property
    def headline(self) -> str:
        b = self.binding
        if b is None:
            return "Every gate is holding. Keep measuring."
        worst = b.worst
        if worst is None:
            return f"{b.gate.name} is the binding constraint."
        return f"{b.gate.name} is the binding constraint: {worst.measure.label.lower()}."


def run(community: Community, window_days: int = 0,
        benchmark_file: str = "") -> Audit:
    if benchmark_file:
        benchmarks.load(benchmark_file)
    scoped = community.window(window_days) if window_days else community
    graph = derive(scoped)
    measured = metrics.measure(scoped, graph)
    assessment = model.assess(measured)
    plan = prescribe.prescribe(assessment)

    facts = Facts(scoped, graph)
    audit = Audit(
        community=scoped, measures=measured, assessment=assessment, plan=plan,
        connectors=_connectors(facts),
        quiet_regulars=_quiet_regulars(facts),
        graveyards=_graveyards(facts),
        changes=_changes(scoped),
        weekly=_weekly(facts),
        benchmark_source=benchmarks.SOURCE,
    )
    return audit


def _people(facts: Facts) -> list:
    partners = facts.strict.partners()
    out = []
    for actor, count in facts.per_actor.items():
        member = facts.community.members.get(actor)
        out.append(Person(
            actor=actor,
            name=(member.name if member and member.name else actor),
            messages=count,
            partners=len(partners.get(actor, ())),
            weeks=len(facts.weeks_active.get(actor, ())),
            staff=actor in facts.staff,
        ))
    return out


def _connectors(facts: Facts, limit: int = 12) -> list:
    """The members already doing the work of holding the place together.

    Named, because the useful version of "recruit moderators" is a list.
    """
    found = [
        p for p in _people(facts)
        if not p.staff and p.partners >= CONNECTOR_PARTNERS
        and p.weeks >= CONNECTOR_WEEKS
    ]
    found.sort(key=lambda p: (p.partners, p.weeks, p.messages), reverse=True)
    return found[:limit]


def _quiet_regulars(facts: Facts, limit: int = 12) -> list:
    """People who turn up but know nobody.  The cheapest growth available.

    They have already cleared the hardest gate by coming back. What they have
    not done is meet anyone, which is a thing an introduction fixes.
    """
    found = [
        p for p in _people(facts)
        if not p.staff and p.weeks >= 2 and p.partners <= 1 and p.messages >= 3
    ]
    found.sort(key=lambda p: (p.weeks, p.messages), reverse=True)
    return found[:limit]


def _graveyards(facts: Facts) -> list:
    end = facts.community.span()[1]
    last: dict = {}
    for e in facts.messages:
        if e.surface:
            last[e.surface] = max(last.get(e.surface, e.at), e.at)
    out = []
    for surface in facts.community.surfaces:
        if not surface:
            continue
        seen = last.get(surface)
        days = None if seen is None else (end - seen).days
        if days is None or days >= GRAVEYARD_DAYS:
            out.append((surface, days))
    out.sort(key=lambda pair: (-1 if pair[1] is None else -pair[1]))
    return out


def _weekly(facts: Facts) -> list:
    """Distinct non-staff posters per week, oldest first.

    The one thing on the report that is a picture rather than a number. A
    community sliding gently downhill is obvious in this shape and invisible
    in any single measurement.
    """
    by_week: dict = {}
    for e in facts.messages:
        if e.actor in facts.members:
            iso = e.at.isocalendar()
            by_week.setdefault((iso[0], iso[1]), set()).add(e.actor)
    return [
        {"week": f"{year}-W{week:02d}", "posters": len(who)}
        for (year, week), who in sorted(by_week.items())
    ]


def _changes(community: Community) -> list:
    """What moved between the first half of the window and the second.

    Both halves are measured with the same code, so a difference is a
    difference in the community rather than in the method.
    """
    if not community.events:
        return []
    start, end = community.span()
    if (end - start) < timedelta(days=28):
        return []
    middle = start + (end - start) / 2

    halves = []
    for lo, hi in ((start, middle), (middle, end)):
        part = Community(
            name=community.name, platform=community.platform,
            members=community.members, surfaces=list(community.surfaces),
        )
        part.events = [e for e in community.events if lo <= e.at <= hi]
        part.finalise()
        if not part.events:
            return []
        halves.append(metrics.measure(part, derive(part)))

    before, after = halves
    out = []
    for key, first in before.items():
        second = after.get(key)
        if second is None or not first.measured or not second.measured:
            continue
        change = Change(key, first.label, first.value, second.value, first.good)
        scale = max(abs(first.value), 1e-9)
        if abs(change.delta) / scale >= MATERIAL_CHANGE:
            out.append(change)
    out.sort(key=lambda c: (c.better, -abs(c.relative or 0)))
    return out


def to_dict(audit: Audit) -> dict:
    """The audit as plain data, for storing and for building benchmarks."""
    start, end = audit.community.span()
    return {
        "community": audit.community.name,
        "platform": audit.community.platform,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "size": {
            "messages": len(audit.community.messages),
            "people": len(audit.community.humans()),
            "surfaces": len(audit.community.surfaces),
        },
        "headline": audit.headline,
        "binding": None if audit.binding is None else audit.binding.gate.key,
        "gates": {
            r.gate.key: {"verdict": r.verdict, "score": r.score}
            for r in audit.assessment["gates"] + [audit.assessment["structure"]]
        },
        "measures": {
            key: {
                "label": m.label, "value": m.value, "unit": m.unit,
                "good": m.good, "unavailable": m.unavailable,
            }
            for key, m in audit.measures.items()
        },
        "plan": [i.key for i in audit.plan["plan"]],
        "housekeeping": [i.key for i in audit.plan["housekeeping"]],
        "connectors": [
            {"name": p.name, "partners": p.partners, "weeks": p.weeks,
             "messages": p.messages}
            for p in audit.connectors
        ],
        "quiet_regulars": [
            {"name": p.name, "weeks": p.weeks, "messages": p.messages}
            for p in audit.quiet_regulars
        ],
        "graveyards": [{"surface": s, "quiet_days": d} for s, d in audit.graveyards],
        "weekly": audit.weekly,
        "changes": [
            {"measure": c.key, "before": c.before, "after": c.after,
             "relative": c.relative, "better": c.better}
            for c in audit.changes
        ],
        "benchmarks": audit.benchmark_source,
    }
