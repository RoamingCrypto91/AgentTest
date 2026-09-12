"""Who spoke to whom.

A community is a graph, and the graph is what distinguishes a room where
people talk to each other from a room where everyone talks at the host.  The
difficulty is that chat platforms only sometimes record who a message was
answering, so the graph has to be assembled from three kinds of evidence of
decreasing strength:

``reply``
    the platform recorded it.  Unambiguous.
``mention``
    the message named someone.  Nearly as good.
``adjacency``
    neither, but someone else had just spoken in the same room.  This is a
    guess, and it is the only way to see anything at all in the many servers
    where nobody uses the reply button.

Everything downstream can ask for the strict graph (reply and mention only)
or the inclusive one.  Findings are reported on the strict graph, because a
number built on guesses should not be the one a client is shown; the
inclusive graph is reported alongside it so the gap is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .schema import MESSAGE, REACTION, Community

#: how close two messages must be for adjacency to mean anything
ADJACENCY_WINDOW = timedelta(minutes=10)

#: evidence that two people were actually in conversation
STRICT = ("reply", "mention")
#: evidence that somebody was at least noticed.  A reaction is not a
#: conversation, but for a newcomer's first post it is acknowledgement, and
#: acknowledgement is what the arrival gate is about.
ACKNOWLEDGING = ("reply", "mention", "reaction")


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    at: datetime
    surface: str
    basis: str
    #: the event that created the edge, so a first post can be traced
    event_id: str = ""
    parent_event_id: str = ""


class Graph:
    """The interaction graph, with the evidence for every edge kept."""

    def __init__(self, edges: list) -> None:
        self.edges = edges

    def __len__(self) -> int:
        return len(self.edges)

    def strict(self) -> "Graph":
        return Graph([e for e in self.edges if e.basis in STRICT])

    def acknowledging(self) -> "Graph":
        return Graph([e for e in self.edges if e.basis in ACKNOWLEDGING])

    def between(self, keep) -> "Graph":
        """Edges where both ends satisfy `keep`."""
        return Graph([e for e in self.edges if keep(e.src) and keep(e.dst)])

    def partners(self) -> dict:
        """actor -> the distinct people they interacted with, either way round."""
        out: dict = {}
        for e in self.edges:
            out.setdefault(e.src, set()).add(e.dst)
            out.setdefault(e.dst, set()).add(e.src)
        return out

    def inbound(self, actor: str) -> list:
        return [e for e in self.edges if e.dst == actor]

    def answers_to(self, event_id: str) -> list:
        """Edges created by a message answering this particular event."""
        return [e for e in self.edges if e.parent_event_id == event_id]

    def tally(self) -> dict:
        counts: dict = {}
        for e in self.edges:
            counts[e.basis] = counts.get(e.basis, 0) + 1
        return counts


def derive(community: Community, window: timedelta = ADJACENCY_WINDOW) -> Graph:
    """Assemble the interaction graph from an export."""
    by_id = {e.id: e for e in community.events if e.id}
    humans = community.humans()
    edges: list = []
    #: last message per surface, so adjacency has something to attach to
    previous: dict = {}

    for event in community.events:
        if event.actor not in humans:
            continue

        if event.kind == REACTION:
            parent = by_id.get(event.parent_id) if event.parent_id else None
            targets = set(event.addressed)
            if parent is not None:
                targets.add(parent.actor)
            for target in targets:
                if target and target != event.actor and target in humans:
                    edges.append(Edge(event.actor, target, event.at, event.surface,
                                      "reaction", event.id, event.parent_id))
            continue

        if event.kind != MESSAGE:
            continue
        explicit = set()

        parent = by_id.get(event.parent_id) if event.parent_id else None
        if parent is not None and parent.actor != event.actor and parent.actor in humans:
            explicit.add(parent.actor)
            edges.append(Edge(event.actor, parent.actor, event.at, event.surface,
                              "reply", event.id, parent.id))

        for named in event.addressed:
            if named == event.actor or named not in humans or named in explicit:
                continue
            explicit.add(named)
            edges.append(Edge(event.actor, named, event.at, event.surface,
                              "mention", event.id,
                              parent.id if parent is not None else ""))

        if not explicit:
            last = previous.get(event.surface)
            if (last is not None and last.actor != event.actor
                    and event.at - last.at <= window):
                edges.append(Edge(event.actor, last.actor, event.at, event.surface,
                                  "adjacency", event.id, last.id))

        previous[event.surface] = event

    return Graph(edges)
