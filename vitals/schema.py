"""The only facts about a community that matter.

Every platform stores a different pile of things.  Almost none of it bears on
whether the place is alive.  What does:

* who arrived, and when
* who spoke, where, and when
* who they were speaking to
* who is staff

That is the whole model.  Note what is *not* here: message text.  Nothing in
this instrument reads what anyone said, which means an audit can be run on
someone else's community without holding a single sentence their members
wrote.  Adapters throw the content away at the boundary and keep the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

#: an interaction edge's evidence, strongest first
BASES = ("reply", "mention", "adjacency")

MESSAGE = "message"
JOIN = "join"
LEAVE = "leave"
REACTION = "reaction"
KINDS = (MESSAGE, JOIN, LEAVE, REACTION)


def utc(ts: datetime) -> datetime:
    """Everything is compared across time zones, so everything is UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@dataclass(frozen=True)
class Event:
    """One thing that happened.  No content, by design."""

    at: datetime
    actor: str
    kind: str = MESSAGE
    surface: str = ""
    #: id of this event, where the platform gives us one
    id: str = ""
    #: the event this one answers, if the platform recorded a reply
    parent_id: str = ""
    #: actors this event addressed explicitly, by reply or by mention
    addressed: tuple = ()
    #: a length proxy.  Useful for telling a reaction-emoji culture from a
    #: conversation, and cheap to keep.
    words: int = 0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")


@dataclass
class Member:
    name: str = ""
    staff: bool = False
    #: when the platform says they joined.  Absent in most chat exports, which
    #: changes what can honestly be measured -- see `Community.has_roster`.
    joined_at: Optional[datetime] = None
    bot: bool = False


@dataclass
class Community:
    """A normalised export, ready to measure."""

    name: str = ""
    platform: str = ""
    events: list = field(default_factory=list)
    members: dict = field(default_factory=dict)
    #: surfaces the export covers, even the silent ones.  A room nobody has
    #: posted in for a month is a finding, and it is invisible if the export
    #: only lists surfaces that have traffic.
    surfaces: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    # -- construction ----------------------------------------------------

    def add(self, event: Event) -> None:
        self.events.append(event)
        self.members.setdefault(event.actor, Member(name=event.actor))
        if event.surface and event.surface not in self.surfaces:
            self.surfaces.append(event.surface)

    def member(self, actor: str) -> Member:
        return self.members.setdefault(actor, Member(name=actor))

    def finalise(self) -> "Community":
        self.events.sort(key=lambda e: (e.at, e.id))
        return self

    # -- shape -----------------------------------------------------------

    @property
    def has_roster(self) -> bool:
        """Do we know when people arrived?

        Without it, arrival has to be inferred from someone's first message,
        which silently excludes everyone who never posted -- exactly the
        population an activation measure is about.  So the instrument says so
        rather than quietly reporting a flattering number.
        """
        return any(m.joined_at for m in self.members.values())

    @property
    def messages(self) -> list:
        return [e for e in self.events if e.kind == MESSAGE]

    def humans(self) -> set:
        return {a for a, m in self.members.items() if not m.bot}

    def staff(self) -> set:
        return {a for a, m in self.members.items() if m.staff and not m.bot}

    def span(self) -> tuple:
        if not self.events:
            raise ValueError("this export contains no events")
        return self.events[0].at, self.events[-1].at

    def window(self, days: int = 0) -> "Community":
        """The last `days` days of it, or everything when days is zero."""
        if days <= 0:
            return self
        end = self.span()[1]
        start = end - timedelta(days=days)
        clipped = Community(
            name=self.name, platform=self.platform,
            members=self.members, surfaces=list(self.surfaces),
            notes=list(self.notes) + [f"clipped to the last {days} days"],
        )
        clipped.events = [e for e in self.events if e.at >= start]
        return clipped

    def joined(self, actor: str) -> Optional[datetime]:
        """When this member arrived, by roster or by first trace of them."""
        m = self.members.get(actor)
        if m and m.joined_at:
            return m.joined_at
        for e in self.events:
            if e.actor == actor:
                return e.at
        return None

    def describe(self) -> str:
        start, end = self.span()
        days = max(1, (end - start).days)
        rooms = len(self.surfaces)
        people = len(self.humans())
        return (
            f"{self.name or 'community'} ({self.platform or 'unknown platform'}): "
            f"{len(self.messages):,} messages from {people:,} "
            f"{'person' if people == 1 else 'people'} across {rooms} "
            f"{'room' if rooms == 1 else 'rooms'} over {days} "
            f"{'day' if days == 1 else 'days'}"
        )


def build(name: str, platform: str, events: Iterable, members: Optional[dict] = None,
          surfaces: Optional[Iterable] = None, notes: Optional[Iterable] = None) -> Community:
    """Assemble a community from parts.  Used by every adapter."""
    c = Community(name=name, platform=platform, notes=list(notes or []))
    for actor, m in (members or {}).items():
        c.members[actor] = m
    for s in surfaces or ():
        if s not in c.surfaces:
            c.surfaces.append(s)
    for e in events:
        c.add(e)
    return c.finalise()
