"""Can the instrument tell the four kinds of community apart?

This is the test that decides whether any of the rest is worth anything.  The
archetypes in `vitals.simulate` are generated from known parameters -- a
broadcast where nobody answers anybody, a clique that talks only to itself, a
healthy mesh, and a mesh that loses its connectors halfway through -- so the
measurements have a ground truth to be checked against.

If these assertions start failing after a change to the metrics, the change
made the instrument blind to something it could see before.
"""

import unittest

from vitals import audit, simulate
from vitals.interactions import derive
from vitals.metrics import measure


def measured(name):
    community = simulate.archetype(name)
    return measure(community, derive(community))


class Archetypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = {name: measured(name) for name in simulate.ARCHETYPES}
        cls.a = {name: audit.run(simulate.archetype(name))
                 for name in simulate.ARCHETYPES}

    def value(self, name, key):
        m = self.m[name][key]
        self.assertTrue(m.measured, f"{name}/{key}: {m.unavailable}")
        return m.value

    # -- the measurements ------------------------------------------------

    def test_a_broadcast_is_members_orbiting_the_host(self):
        self.assertGreater(self.value("broadcast", "orbit"), 0.5)
        self.assertGreater(self.value("broadcast", "staff_voice"), 0.7)
        self.assertLess(self.value("broadcast", "member_to_member"), 0.2)

    def test_a_mesh_is_the_opposite_on_every_one_of_those(self):
        self.assertLess(self.value("mesh", "orbit"), 0.15)
        self.assertLess(self.value("mesh", "staff_voice"), 0.3)
        self.assertGreater(self.value("mesh", "member_to_member"), 0.6)

    def test_a_clique_talks_plenty_but_not_to_newcomers(self):
        # the thing that makes a clique a clique: members do interact
        self.assertGreater(self.value("clique", "member_to_member"),
                           self.value("broadcast", "member_to_member"))
        # ... and newcomers still get nothing
        self.assertLess(self.value("clique", "first_post_answered_by_member"), 0.3)
        self.assertLess(self.value("clique", "connectors"),
                        self.value("mesh", "connectors"))

    def test_a_collapsing_community_can_look_busy_while_it_collapses(self):
        """The most useful thing the instrument knows.

        `dying` loses its connectors halfway through but keeps taking
        arrivals, so weekly posters hold up and the place looks fine. The
        damage is only visible in the cohort measure: almost nobody who
        arrives is still here a month later. This is exactly the community
        that gets told its numbers are healthy.
        """
        self.assertGreater(self.value("dying", "weekly_active_trend"), -0.1)
        self.assertGreater(self.value("dying", "member_to_member"), 0.4)
        self.assertLess(self.value("dying", "retention_30d"), 0.25)
        self.assertGreater(self.value("mesh", "retention_30d"), 0.4)

    def test_conversation_separates_a_noticeboard_from_a_room(self):
        self.assertLess(self.value("broadcast", "conversational"), 0.15)
        self.assertGreater(self.value("mesh", "conversational"), 0.3)

    def test_the_healthy_one_wins_on_every_measure_that_has_a_direction(self):
        losses = []
        for key, good in self.m["mesh"].items():
            mesh, cast = self.m["mesh"][key], self.m["broadcast"][key]
            if not (mesh.measured and cast.measured):
                continue
            better = mesh.value > cast.value if mesh.good == "high" \
                else mesh.value < cast.value
            if not better:
                losses.append(key)
        self.assertEqual(
            losses, ["time_to_first_post"],
            "the only measure a broadcast should beat a mesh on is the wait "
            "before a first post, which is conditioned on people who spoke at "
            "all -- and a broadcast has almost none",
        )

    # -- the verdicts ----------------------------------------------------

    def test_each_archetype_gets_the_diagnosis_it_deserves(self):
        self.assertEqual(self.a["broadcast"].binding.gate.key, "arrival")
        self.assertIsNone(self.a["mesh"].binding)
        self.assertEqual(self.a["dying"].binding.gate.key, "return")
        self.assertIsNotNone(self.a["clique"].binding)

    def test_the_healthy_one_holds_every_gate(self):
        for gate in self.a["mesh"].assessment["gates"]:
            self.assertEqual(gate.verdict, "holding", gate.gate.key)

    def test_the_broadcast_fails_the_relationship_gate(self):
        by_key = {g.gate.key: g for g in self.a["broadcast"].assessment["gates"]}
        self.assertEqual(by_key["relationship"].verdict, "failing")

    def test_a_plan_is_offered_where_something_is_wrong(self):
        for name in ("broadcast", "clique", "dying"):
            with self.subTest(archetype=name):
                self.assertTrue(self.a[name].plan["plan"])
                self.assertLessEqual(len(self.a[name].plan["plan"]), 3)
        self.assertFalse(self.a["mesh"].plan["plan"])

    def test_every_prescription_aims_at_the_binding_gate(self):
        for name, result in self.a.items():
            if result.binding is None:
                continue
            for action in result.plan["plan"]:
                with self.subTest(archetype=name, action=action.key):
                    self.assertEqual(action.gate, result.binding.gate.key)

    def test_the_dying_one_shows_a_collapse_between_the_halves(self):
        worse = [c for c in self.a["dying"].changes if not c.better]
        self.assertTrue(worse, "a community losing its core should show it")
        self.assertIn("retention_30d", [c.key for c in worse])

    def test_names_come_out_of_it_not_just_numbers(self):
        self.assertTrue(self.a["mesh"].connectors)
        self.assertTrue(self.a["broadcast"].graveyards)
        for person in self.a["mesh"].connectors:
            self.assertGreaterEqual(person.partners, 5)
            self.assertFalse(person.staff)


class Stability(unittest.TestCase):
    def test_the_simulator_is_deterministic(self):
        first = simulate.archetype("mesh")
        second = simulate.archetype("mesh")
        self.assertEqual(len(first.events), len(second.events))
        self.assertEqual([e.id for e in first.events[:50]],
                         [e.id for e in second.events[:50]])

    def test_a_different_seed_is_a_different_community_of_the_same_character(self):
        from dataclasses import replace

        base = simulate.ARCHETYPES["mesh"]
        other = simulate.generate(replace(base, seed=base.seed + 1))
        self.assertNotEqual(len(other.events), len(simulate.archetype("mesh").events))
        m = measure(other, derive(other))
        self.assertGreater(m["member_to_member"].value, 0.5)
        self.assertLess(m["orbit"].value, 0.2)


if __name__ == "__main__":
    unittest.main()
