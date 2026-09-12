"""The measurements.

Each one exists because a specific thing can go wrong with a community, and
each is computable from message metadata alone on any platform.  Nothing here
is a vanity metric: message volume, member count and "engagement rate" are
absent on purpose, because all three can rise while the place dies.

Measures are grouped by the gate they test.  See docs/MODEL.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from . import stats
from .interactions import Graph
from .schema import MESSAGE, Community

#: a newcomer's first message has to land within this long of arriving to
#: count as activation
ACTIVATION_WINDOW = timedelta(days=7)
#: and someone has to answer it within this long to count as answered
ANSWER_WINDOW = timedelta(hours=24)
#: members below this are noise, not regulars
REGULAR_MESSAGES = 3
#: a connector talks to at least this many distinct people
CONNECTOR_PARTNERS = 5
CONNECTOR_WEEKS = 3
#: a surface with nothing in it for this long reads as abandoned
GRAVEYARD_DAYS = 14
#: someone whose first trace is within this of the export's start might be a
#: long-timer rather than a newcomer, so they are left out of arrival measures
NEWCOMER_GRACE = timedelta(days=1)


@dataclass
class Measure:
    key: str
    label: str
    value: Optional[float] = None
    unit: str = "share"
    #: what the number is out of, in words, for the report
    basis: str = ""
    #: why there is no number, when there isn't one
    unavailable: str = ""
    #: higher is better, unless this says otherwise
    good = "high"

    @property
    def measured(self) -> bool:
        return self.value is not None


def _week(ts) -> tuple:
    iso = ts.isocalendar()
    return (iso[0], iso[1])


@dataclass
class Facts:
    """Everything derived once and reused by every measure."""

    community: Community
    graph: Graph
    strict: Graph = field(init=False)
    seen: Graph = field(init=False)
    humans: set = field(init=False)
    staff: set = field(init=False)
    members: set = field(init=False)
    messages: list = field(init=False)
    per_actor: dict = field(init=False)
    first_message: dict = field(init=False)
    weeks_active: dict = field(init=False)
    newcomers: set = field(init=False)

    def __post_init__(self) -> None:
        c = self.community
        self.strict = self.graph.strict()
        self.seen = self.graph.acknowledging()
        self.humans = c.humans()
        self.staff = c.staff()
        self.members = self.humans - self.staff
        self.messages = [e for e in c.messages if e.actor in self.humans]
        self.per_actor = {}
        self.first_message = {}
        self.weeks_active = {}
        for e in self.messages:
            self.per_actor[e.actor] = self.per_actor.get(e.actor, 0) + 1
            self.first_message.setdefault(e.actor, e)
            self.weeks_active.setdefault(e.actor, set()).add(_week(e.at))
        self.newcomers = self._newcomers()

    def _newcomers(self) -> set:
        """People who arrived during the observed window, as best we can tell."""
        c = self.community
        if not c.events:
            return set()
        start = c.span()[0]
        if c.has_roster:
            return {
                a for a in self.humans
                if (c.members[a].joined_at or start) >= start
                and a not in self.staff
            }
        return {
            a for a in self.members
            if a in self.first_message
            and self.first_message[a].at >= start + NEWCOMER_GRACE
        }

    def actives(self) -> set:
        return {a for a in self.per_actor if a in self.members}

    def regulars(self) -> set:
        return {a for a in self.actives() if self.per_actor[a] >= REGULAR_MESSAGES}

    def weekly_actives(self) -> list:
        by_week: dict = {}
        for e in self.messages:
            if e.actor in self.members:
                by_week.setdefault(_week(e.at), set()).add(e.actor)
        return [len(v) for _, v in sorted(by_week.items())]


# ---------------------------------------------------------------------------
# gate 1 -- arriving, and saying something
# ---------------------------------------------------------------------------


def never_spoke(f: Facts) -> Measure:
    m = Measure("never_spoke", "Members who have never posted", unit="share",
                basis="of members on the roster")
    m.good = "low"
    if not f.community.has_roster:
        m.unavailable = ("this export has no join dates, so the silent majority "
                         "is invisible; every arrival measure below is computed "
                         "only over people who did speak")
        return m
    roster = {a for a, mem in f.community.members.items()
              if mem.joined_at and not mem.bot and a not in f.staff}
    if not roster:
        m.unavailable = "no non-staff members with a join date"
        return m
    m.value = stats.share(len([a for a in roster if a not in f.per_actor]), len(roster))
    return m


def activation(f: Facts) -> Measure:
    """Of the people who arrived, how many ever said anything, quickly."""
    m = Measure("activation", "Arrivals who posted within 7 days", unit="share",
                basis="of people who arrived during the window")
    c = f.community
    if not c.has_roster:
        m.unavailable = "needs join dates; use `never_spoke` once you have them"
        return m
    end = c.span()[1]
    arrived = [
        a for a in f.newcomers
        if (c.members[a].joined_at is not None
            and c.members[a].joined_at + ACTIVATION_WINDOW <= end)
    ]
    if not arrived:
        m.unavailable = "nobody arrived early enough in the window to judge"
        return m
    spoke = 0
    for a in arrived:
        first = f.first_message.get(a)
        joined = c.members[a].joined_at
        if first is not None and first.at - joined <= ACTIVATION_WINDOW:
            spoke += 1
    m.value = stats.share(spoke, len(arrived))
    return m


def time_to_first_post(f: Facts) -> Measure:
    m = Measure("time_to_first_post", "Median wait before a first post",
                unit="hours", basis="among arrivals who did post")
    m.good = "low"
    c = f.community
    if not c.has_roster:
        m.unavailable = "needs join dates"
        return m
    waits = []
    for a in f.newcomers:
        joined = c.members[a].joined_at
        first = f.first_message.get(a)
        if joined and first is not None and first.at >= joined:
            waits.append((first.at - joined).total_seconds() / 3600)
    m.value = stats.median(waits)
    return m


def _answered(f: Facts, graph: Graph, by_members_only: bool = False) -> Optional[float]:
    """Share of newcomers' first posts that somebody answered."""
    answered = considered = 0
    end = f.community.span()[1]
    inbound: dict = {}
    for e in graph.edges:
        inbound.setdefault(e.dst, []).append(e)
    for a in f.newcomers:
        first = f.first_message.get(a)
        if first is None or first.at + ANSWER_WINDOW > end:
            continue  # not enough time has passed to judge
        considered += 1
        for edge in inbound.get(a, ()):
            if not (first.at <= edge.at <= first.at + ANSWER_WINDOW):
                continue
            if by_members_only and edge.src not in f.members:
                continue
            answered += 1
            break
    return stats.share(answered, considered)


def first_post_answered(f: Facts) -> Measure:
    """The single most predictive thing in the instrument.

    A first contribution nobody acknowledges is the most reliable way to lose
    a member, and it is the failure almost every dead server has in common.
    """
    m = Measure("first_post_answered", "First posts that somebody answered",
                unit="share",
                basis="of newcomers' first posts, within 24 hours; a reaction counts")
    m.value = _answered(f, f.seen)
    if m.value is None:
        m.unavailable = "no newcomers' first posts fall far enough inside the window"
    return m


def first_post_answered_by_member(f: Facts) -> Measure:
    m = Measure("first_post_answered_by_member",
                "First posts answered by another member, not staff", unit="share",
                basis="of newcomers' first posts, within 24 hours")
    m.value = _answered(f, f.seen, by_members_only=True)
    if m.value is None:
        m.unavailable = "no newcomers' first posts fall far enough inside the window"
    return m


# ---------------------------------------------------------------------------
# gate 2 -- coming back
# ---------------------------------------------------------------------------


def returned(f: Facts) -> Measure:
    m = Measure("returned", "Newcomers who posted in more than one week",
                unit="share", basis="of newcomers who posted at all")
    spoke = [a for a in f.newcomers if a in f.weeks_active]
    if not spoke:
        m.unavailable = "no newcomers posted in this window"
        return m
    m.value = stats.share(len([a for a in spoke if len(f.weeks_active[a]) >= 2]),
                          len(spoke))
    return m


def retention_30d(f: Facts) -> Measure:
    m = Measure("retention_30d", "Newcomers still posting a month later",
                unit="share", basis="of newcomers observed for 30+ days")
    c = f.community
    end = c.span()[1]
    considered = kept = 0
    for a in f.newcomers:
        first = f.first_message.get(a)
        if first is None or first.at + timedelta(days=37) > end:
            continue
        considered += 1
        low = first.at + timedelta(days=23)
        high = first.at + timedelta(days=37)
        if any(e.actor == a and low <= e.at <= high for e in f.messages):
            kept += 1
    m.value = stats.share(kept, considered)
    if m.value is None:
        m.unavailable = "the export is too short to see a month of anyone's life"
    return m


def weekly_active_trend(f: Facts) -> Measure:
    m = Measure("weekly_active_trend", "Change in weekly posters, first third to last",
                unit="ratio", basis="distinct non-staff posters per week")
    m.value = stats.trend(f.weekly_actives())
    if m.value is None:
        m.unavailable = "fewer than six weeks of history"
    return m


# ---------------------------------------------------------------------------
# gate 3 -- knowing somebody
# ---------------------------------------------------------------------------


def member_to_member(f: Facts) -> Measure:
    """The number that defines the difference between the two kinds of room."""
    m = Measure("member_to_member", "Interactions between members, not with staff",
                unit="share", basis="of interactions where we know the target")
    total = len(f.strict)
    if not total:
        m.unavailable = "no replies or mentions in this export"
        return m
    both = len(f.strict.between(lambda a: a in f.members))
    m.value = stats.share(both, total)
    return m


def median_partners(f: Facts) -> Measure:
    m = Measure("median_partners", "People the median regular has spoken with",
                unit="count", basis="distinct partners, among members with 3+ posts")
    partners = f.strict.partners()
    regulars = f.regulars()
    if not regulars:
        m.unavailable = "nobody posted three times"
        return m
    m.value = stats.median([len(partners.get(a, ())) for a in regulars])
    return m


def orbit(f: Facts) -> Measure:
    """Members whose only relationship here is with the host.

    This is what "announcement platform" means, stated as a number.  These
    people are an audience standing in a room together, not a community: if
    the host leaves, they have no reason to stay, because there is nobody
    here they know.
    """
    m = Measure("orbit", "Members who have only ever interacted with staff",
                unit="share", basis="of members who interacted with anyone")
    m.good = "low"
    partners = f.strict.partners()
    engaged = [a for a in f.members if partners.get(a)]
    if not engaged:
        m.unavailable = "no member replies or mentions in this export"
        return m
    orbiting = [a for a in engaged if partners[a] <= f.staff]
    m.value = stats.share(len(orbiting), len(engaged))
    return m


# ---------------------------------------------------------------------------
# gate 4 -- doing the work
# ---------------------------------------------------------------------------


def conversational(f: Facts) -> Measure:
    """Is anything here a conversation, or is it a wall of separate posts?

    A message counts as conversational if it answers something or something
    answers it.  A noticeboard scores near zero however busy it looks, which
    is the point: volume is not the question.
    """
    m = Measure("conversational", "Messages that are part of an exchange",
                unit="share", basis="of messages that answer or get answered")
    if not f.messages:
        m.unavailable = "no messages"
        return m
    involved = set()
    for e in f.strict.edges:
        if e.event_id:
            involved.add(e.event_id)
        if e.parent_event_id:
            involved.add(e.parent_event_id)
    m.value = stats.share(len(involved), len(f.messages))
    return m


def connectors(f: Facts) -> Measure:
    m = Measure("connectors", "Connectors per hundred active members", unit="count",
                basis="members with 5+ partners, active in 3+ weeks")
    actives = f.actives()
    if not actives:
        m.unavailable = "nobody posted"
        return m
    partners = f.strict.partners()
    found = [
        a for a in actives
        if len(partners.get(a, ())) >= CONNECTOR_PARTNERS
        and len(f.weeks_active.get(a, ())) >= CONNECTOR_WEEKS
    ]
    m.value = 100.0 * len(found) / len(actives)
    return m


def staff_voice(f: Facts) -> Measure:
    m = Measure("staff_voice", "Share of all posting done by staff", unit="share",
                basis="of messages")
    m.good = "low"
    if not f.messages:
        m.unavailable = "no messages"
        return m
    m.value = stats.share(len([e for e in f.messages if e.actor in f.staff]),
                          len(f.messages))
    return m


# ---------------------------------------------------------------------------
# gate 5 -- running without you
# ---------------------------------------------------------------------------


def absence_resilience(f: Facts) -> Measure:
    """Does the place keep talking on the days the host doesn't?

    A community that goes quiet when the founder is away is a broadcast with
    a comment section, whatever its member count says.
    """
    m = Measure("absence_resilience", "Member posting on staff-quiet days",
                unit="ratio", basis="versus days staff were active")
    if not f.staff:
        m.unavailable = "no staff are marked in this export, so there is nothing to compare"
        return m
    staff_by_day: dict = {}
    member_by_day: dict = {}
    for e in f.messages:
        day = e.at.date()
        if e.actor in f.staff:
            staff_by_day[day] = staff_by_day.get(day, 0) + 1
        else:
            member_by_day[day] = member_by_day.get(day, 0) + 1
    days = sorted(set(staff_by_day) | set(member_by_day))
    if len(days) < 14:
        m.unavailable = "fewer than a fortnight of days to compare"
        return m
    counts = [staff_by_day.get(d, 0) for d in days]
    cut = stats.quantile(counts, 0.25) or 0
    quiet = [member_by_day.get(d, 0) for d in days if staff_by_day.get(d, 0) <= cut]
    loud = [member_by_day.get(d, 0) for d in days if staff_by_day.get(d, 0) > cut]
    if not quiet or not loud:
        m.unavailable = "staff activity is too uniform to split the days"
        return m
    busy = stats.mean(loud)
    if not busy:
        m.unavailable = "no member posting on staff-active days"
        return m
    m.value = (stats.mean(quiet) or 0) / busy
    return m


def bus_factor(f: Facts) -> Measure:
    m = Measure("bus_factor", "People accounting for half the conversation",
                unit="count", basis="including staff")
    if not f.per_actor:
        m.unavailable = "no messages"
        return m
    m.value = stats.bus_factor(list(f.per_actor.values()))
    return m


# ---------------------------------------------------------------------------
# conditions that cut across every gate
# ---------------------------------------------------------------------------


def concentration(f: Facts) -> Measure:
    m = Measure("concentration", "Inequality of contribution", unit="index",
                basis="Gini over messages per person")
    m.good = "low"
    m.value = stats.gini(list(f.per_actor.values()))
    if m.value is None:
        m.unavailable = "not enough people posting to measure inequality"
    return m


def graveyards(f: Facts) -> Measure:
    m = Measure("graveyards", "Surfaces with nothing in them for a fortnight",
                unit="share", basis="of surfaces in the export")
    m.good = "low"
    surfaces = [s for s in f.community.surfaces if s]
    if not surfaces:
        m.unavailable = "the export does not list its surfaces"
        return m
    end = f.community.span()[1]
    last: dict = {}
    for e in f.messages:
        if e.surface:
            last[e.surface] = max(last.get(e.surface, e.at), e.at)
    dead = [
        s for s in surfaces
        if s not in last or (end - last[s]).days >= GRAVEYARD_DAYS
    ]
    m.value = stats.share(len(dead), len(surfaces))
    return m


def rooms_per_person(f: Facts) -> Measure:
    m = Measure("rooms_per_person", "Surfaces per hundred active members",
                unit="count", basis="rooms divided by people who post")
    m.good = "low"
    actives = f.actives()
    surfaces = [s for s in f.community.surfaces if s]
    if not actives or not surfaces:
        m.unavailable = "needs both surfaces and posters"
        return m
    m.value = 100.0 * len(surfaces) / len(actives)
    return m


#: every measure, in the order a report should read them
MEASURES = (
    never_spoke,
    activation,
    time_to_first_post,
    first_post_answered,
    first_post_answered_by_member,
    returned,
    retention_30d,
    weekly_active_trend,
    member_to_member,
    median_partners,
    orbit,
    conversational,
    connectors,
    staff_voice,
    absence_resilience,
    bus_factor,
    concentration,
    graveyards,
    rooms_per_person,
)


def measure(community: Community, graph: Graph) -> dict:
    """Every measure, keyed."""
    facts = Facts(community, graph)
    out = {}
    for fn in MEASURES:
        m = fn(facts)
        out[m.key] = m
    return out
