"""Join dates, which chat exports do not carry.

This is the single most valuable file you can obtain about a community, and
almost no export tool produces it.  Without it, "arrival" has to be inferred
from somebody's first message, which silently excludes everyone who never
posted -- and those people are the whole subject of an activation measure.
With it, two of the strongest measures in the instrument switch on.

A roster is a CSV of everyone who is a member, whether or not they have ever
said anything::

    actor,joined_at,staff,bot
    u17,2026-01-04T11:02:00Z,0,0
    u3,2025-11-30T08:00:00Z,1,0

``actor`` has to match the ids the chat export uses, which for Discord means
user ids rather than display names.
"""

from __future__ import annotations

import csv

from .adapters.common import parse_time, truthy
from .schema import JOIN, Community, Event, Member

REQUIRED = ("actor", "joined_at")


def merge(community: Community, path: str) -> Community:
    """Attach join dates to a community loaded from a chat export."""
    attached = added = 0
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fields = {(f or "").strip().casefold() for f in (reader.fieldnames or ())}
        missing = [c for c in REQUIRED if c not in fields]
        if missing:
            raise ValueError(
                f"{path} is a roster, so it needs the column(s) "
                f"{', '.join(missing)}; found {', '.join(sorted(fields)) or 'nothing'}"
            )
        for row in reader:
            row = {(k or "").strip().casefold(): v for k, v in row.items()}
            actor = (row.get("actor") or "").strip()
            if not actor:
                continue
            when = parse_time(row["joined_at"])
            known = actor in community.members
            member = community.members.setdefault(actor, Member(name=actor))
            if member.joined_at is None or when < member.joined_at:
                member.joined_at = when
            if truthy(row.get("staff")):
                member.staff = True
            if truthy(row.get("bot")):
                member.bot = True
            if (row.get("name") or "").strip():
                member.name = row["name"].strip()
            attached += 1
            if not known:
                added += 1
                # a member who never posted still has to exist in the timeline,
                # or the silent majority stays invisible
                community.add(Event(at=when, actor=actor, kind=JOIN,
                                    id=f"roster-{actor}"))

    if not attached:
        raise ValueError(f"{path} contained no usable rows")
    community.notes.append(
        f"roster merged: {attached:,} members, {added:,} of whom never posted"
    )
    return community.finalise()
