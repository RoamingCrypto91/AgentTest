"""A plain CSV of events: the universal way in.

Any platform that can be exported at all can be turned into this, which is
what makes the instrument platform-agnostic rather than platform-agnostic in
principle.  One row per thing that happened::

    timestamp,actor,surface,kind,id,parent_id,addressed,staff,words
    2026-01-04T09:12:00Z,u17,general,message,m1,,,0,14
    2026-01-04T09:20:00Z,u3,general,message,m2,m1,u17,1,8
    2026-01-02T11:00:00Z,u17,,join,,,,0,

Only ``timestamp`` and ``actor`` are required.

* ``kind`` defaults to ``message``; ``join`` rows supply the roster, which is
  what makes activation measurable at all
* ``parent_id`` is the message being answered, where the platform recorded one
* ``addressed`` is a space- or semicolon-separated list of actors the message
  named
* ``staff`` marks the people who run the place
* rooms that exist but are silent can be listed in a sidecar text file, one
  name per line, so that empty rooms stay visible
"""

from __future__ import annotations

import csv
import os

from ..schema import JOIN, KINDS, MESSAGE, Community, Event, Member
from .common import count_words, parse_time, truthy

REQUIRED = ("timestamp", "actor")


def _split(value) -> tuple:
    text = str(value or "").replace(";", " ").replace(",", " ")
    return tuple(t for t in text.split() if t)


def load(path: str, surfaces_file: str = "", name: str = "", **_) -> Community:
    community = Community(name=name or os.path.basename(path), platform="csv")

    if surfaces_file and os.path.exists(surfaces_file):
        with open(surfaces_file) as fh:
            for line in fh:
                room = line.strip()
                if room and room not in community.surfaces:
                    community.surfaces.append(room)

    with open(path, newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        fields = {(f or "").strip().casefold() for f in (reader.fieldnames or ())}
        missing = [c for c in REQUIRED if c not in fields]
        if missing:
            raise ValueError(
                f"{path} is missing the column(s) {', '.join(missing)}; "
                f"found {', '.join(sorted(fields)) or 'nothing'}"
            )
        seen = 0
        for row in reader:
            row = {(k or "").strip().casefold(): v for k, v in row.items()}
            actor = (row.get("actor") or "").strip()
            if not actor:
                continue
            when = parse_time(row["timestamp"])
            kind = (row.get("kind") or MESSAGE).strip().casefold() or MESSAGE
            if kind not in KINDS:
                raise ValueError(
                    f"{path}: row {seen + 2} has kind {kind!r}; "
                    f"expected one of {', '.join(KINDS)}"
                )
            seen += 1
            member = community.member(actor)
            if truthy(row.get("staff")):
                member.staff = True
            if truthy(row.get("bot")):
                member.bot = True
            if kind == JOIN:
                if member.joined_at is None or when < member.joined_at:
                    member.joined_at = when
                community.add(Event(at=when, actor=actor, kind=JOIN,
                                    id=(row.get("id") or "").strip()))
                continue
            words = row.get("words")
            community.add(Event(
                at=when, actor=actor, kind=kind,
                surface=(row.get("surface") or "").strip(),
                id=(row.get("id") or f"r{seen}").strip(),
                parent_id=(row.get("parent_id") or "").strip(),
                addressed=_split(row.get("addressed")),
                words=int(words) if str(words or "").strip().isdigit()
                else count_words(row.get("text")),
            ))
    if not community.events:
        raise ValueError(f"{path} produced no usable rows")
    community.notes.append(f"loaded {len(community.events):,} rows from CSV")
    return community.finalise()
