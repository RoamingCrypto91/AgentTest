"""The graph: what counts as one person speaking to another."""

import unittest
from datetime import timedelta

from vitals.interactions import ADJACENCY_WINDOW, derive
from vitals.schema import MESSAGE, REACTION, Community, Event, Member
from vitals.tests import fixture
from vitals.tests.fixture import DAY0


def community(events, members=None):
    c = Community(name="g", platform="test", surfaces=["general"])
    for who, kw in (members or {}).items():
        c.members[who] = Member(name=who, **kw)
    for e in events:
        c.add(e)
    return c.finalise()


def msg(minutes, actor, mid, parent="", addressed=(), surface="general"):
    return Event(at=DAY0 + timedelta(minutes=minutes), actor=actor, kind=MESSAGE,
                 surface=surface, id=mid, parent_id=parent, addressed=addressed)


class Edges(unittest.TestCase):
    def test_a_reply_is_the_strongest_evidence(self):
        g = derive(community([msg(0, "a", "m1"), msg(1, "b", "m2", parent="m1")]))
        self.assertEqual([(e.src, e.dst, e.basis) for e in g.edges],
                         [("b", "a", "reply")])

    def test_a_mention_counts_when_there_is_no_reply(self):
        g = derive(community([msg(0, "a", "m1"), msg(99, "b", "m2",
                                                     addressed=("a",))]))
        self.assertEqual([e.basis for e in g.edges], ["mention"])

    def test_a_reply_that_also_mentions_its_target_is_one_edge(self):
        g = derive(community([msg(0, "a", "m1"),
                              msg(1, "b", "m2", parent="m1", addressed=("a",))]))
        self.assertEqual(len(g), 1)

    def test_speaking_straight_after_somebody_is_weak_evidence(self):
        g = derive(community([msg(0, "a", "m1"), msg(3, "b", "m2")]))
        self.assertEqual([e.basis for e in g.edges], ["adjacency"])
        self.assertEqual(len(g.strict()), 0)

    def test_speaking_much_later_is_no_evidence_at_all(self):
        minutes = int(ADJACENCY_WINDOW.total_seconds() // 60) + 5
        g = derive(community([msg(0, "a", "m1"), msg(minutes, "b", "m2")]))
        self.assertEqual(len(g), 0)

    def test_adjacency_does_not_cross_rooms(self):
        g = derive(community([msg(0, "a", "m1", surface="general"),
                              msg(2, "b", "m2", surface="attic")]))
        self.assertEqual(len(g), 0)

    def test_nobody_talks_to_themselves(self):
        g = derive(community([msg(0, "a", "m1"), msg(1, "a", "m2", parent="m1"),
                              msg(2, "a", "m3", addressed=("a",))]))
        self.assertEqual(len(g), 0)

    def test_bots_are_not_part_of_the_community(self):
        c = community([msg(0, "bot", "m1"), msg(1, "b", "m2", parent="m1")],
                      members={"bot": {"bot": True}})
        self.assertEqual(len(derive(c)), 0)

    def test_a_reaction_is_acknowledgement_but_not_conversation(self):
        c = community([
            msg(0, "a", "m1"),
            Event(at=DAY0 + timedelta(minutes=2), actor="b", kind=REACTION,
                  surface="general", id="x1", parent_id="m1", addressed=("a",)),
        ])
        g = derive(c)
        self.assertEqual([e.basis for e in g.edges], ["reaction"])
        self.assertEqual(len(g.strict()), 0)
        self.assertEqual(len(g.acknowledging()), 1)

    def test_partners_are_symmetric(self):
        g = derive(community([msg(0, "a", "m1"), msg(1, "b", "m2", parent="m1")]))
        partners = g.partners()
        self.assertEqual(partners["a"], {"b"})
        self.assertEqual(partners["b"], {"a"})

    def test_the_fixture_graph_is_what_the_docstring_says(self):
        g = derive(fixture.build())
        self.assertEqual(
            sorted((e.src, e.dst, e.basis) for e in g.edges),
            [("alice", "bob", "reply"), ("bob", "cara", "mention"),
             ("cara", "alice", "adjacency"), ("cara", "bob", "reply")],
        )

    def test_the_tally_names_every_basis_present(self):
        g = derive(fixture.build())
        self.assertEqual(g.tally(), {"reply": 2, "mention": 1, "adjacency": 1})


if __name__ == "__main__":
    unittest.main()
