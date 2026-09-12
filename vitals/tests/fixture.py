"""A tiny community whose every measurement can be worked out by hand.

Five messages, four people, one of whom never speaks.  Small enough that the
expected value of every measure can be reasoned about on paper, which is the
only way to know the code computes what the docstrings claim.

    day 0 00:00  alice, bob, cara, dan all join (alice is staff)
    day 1 10:00  bob   posts  m1                      <- bob's first
    day 1 10:05  alice posts  m2  reply to m1, @bob   <- answers bob, staff
    day 1 10:10  cara  posts  m3  no target           <- cara's first, adjacent
    day 8 11:00  bob   posts  m4  @cara
    day 8 11:02  cara  posts  m5  reply to m4
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vitals.schema import JOIN, MESSAGE, Community, Event, Member

DAY0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def build() -> Community:
    community = Community(name="handmade", platform="test",
                          surfaces=["general", "attic"])
    for who, staff in (("alice", True), ("bob", False), ("cara", False),
                       ("dan", False)):
        community.members[who] = Member(name=who, staff=staff, joined_at=DAY0)
        community.add(Event(at=DAY0, actor=who, kind=JOIN, id=f"j-{who}"))

    def msg(minutes, actor, mid, parent="", addressed=(), surface="general"):
        community.add(Event(
            at=DAY0 + timedelta(minutes=minutes), actor=actor, kind=MESSAGE,
            surface=surface, id=mid, parent_id=parent, addressed=addressed,
            words=10,
        ))

    msg(24 * 60 + 0, "bob", "m1")
    msg(24 * 60 + 5, "alice", "m2", parent="m1", addressed=("bob",))
    msg(24 * 60 + 10, "cara", "m3")
    msg(8 * 24 * 60 + 60, "bob", "m4", addressed=("cara",))
    msg(8 * 24 * 60 + 62, "cara", "m5", parent="m4")
    return community.finalise()
