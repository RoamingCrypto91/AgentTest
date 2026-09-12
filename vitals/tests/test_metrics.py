"""Every measure, against a fixture small enough to check by hand."""

import unittest

from vitals.interactions import derive
from vitals.metrics import measure
from vitals.tests import fixture


class Measured(unittest.TestCase):
    def setUp(self):
        self.community = fixture.build()
        self.graph = derive(self.community)
        self.m = measure(self.community, self.graph)

    def value(self, key):
        m = self.m[key]
        self.assertTrue(m.measured, f"{key} was not measurable: {m.unavailable}")
        return m.value

    def test_the_silent_member_is_counted(self):
        # bob, cara, dan are the non-staff roster; only dan never posted
        self.assertAlmostEqual(self.value("never_spoke"), 1 / 3, places=6)

    def test_activation_counts_arrivals_not_posters(self):
        self.assertAlmostEqual(self.value("activation"), 2 / 3, places=6)

    def test_the_wait_before_a_first_post_is_a_median_over_speakers(self):
        # bob at +24h, cara at +24h10m; the median of the two
        self.assertAlmostEqual(self.value("time_to_first_post"), 24.0 + 5 / 60,
                               places=4)

    def test_only_one_of_the_two_first_posts_was_answered(self):
        # alice answered bob within five minutes; nobody answered cara inside a day
        self.assertAlmostEqual(self.value("first_post_answered"), 0.5, places=6)

    def test_no_first_post_was_answered_by_a_member(self):
        self.assertEqual(self.value("first_post_answered_by_member"), 0.0)

    def test_member_to_member_ignores_the_staff_reply(self):
        # strict edges: alice->bob, bob->cara, cara->bob
        self.assertAlmostEqual(self.value("member_to_member"), 2 / 3, places=6)

    def test_nobody_is_only_orbiting_staff_here(self):
        self.assertEqual(self.value("orbit"), 0.0)

    def test_staff_voice_is_one_message_in_five(self):
        self.assertAlmostEqual(self.value("staff_voice"), 0.2, places=6)

    def test_four_of_five_messages_are_part_of_an_exchange(self):
        self.assertAlmostEqual(self.value("conversational"), 0.8, places=6)

    def test_two_people_carry_half_the_conversation(self):
        self.assertEqual(self.value("bus_factor"), 2)

    def test_both_members_returned_in_a_second_week(self):
        self.assertEqual(self.value("returned"), 1.0)

    def test_the_attic_is_a_graveyard(self):
        # `attic` is declared but never posted in
        self.assertAlmostEqual(self.value("graveyards"), 0.5, places=6)

    def test_measures_that_need_more_history_say_so(self):
        for key in ("retention_30d", "weekly_active_trend", "absence_resilience",
                    "median_partners"):
            m = self.m[key]
            self.assertFalse(m.measured, f"{key} should not be measurable here")
            self.assertTrue(m.unavailable, f"{key} gives no reason")

    def test_every_measure_either_has_a_number_or_a_reason(self):
        for key, m in self.m.items():
            with self.subTest(measure=key):
                self.assertTrue(m.measured or m.unavailable)
                self.assertTrue(m.label)


class Degenerate(unittest.TestCase):
    """Nothing should crash on a community that has barely happened."""

    def test_a_single_message_community(self):
        from vitals.schema import MESSAGE, Community, Event, Member
        from vitals.tests.fixture import DAY0

        c = Community(name="one", platform="test")
        c.members["sam"] = Member(name="sam")
        c.add(Event(at=DAY0, actor="sam", kind=MESSAGE, surface="general", id="m1"))
        c.finalise()
        m = measure(c, derive(c))
        self.assertTrue(m)
        self.assertFalse(m["member_to_member"].measured)

    def test_an_empty_community_is_an_error_with_a_sentence(self):
        from vitals.schema import Community

        with self.assertRaises(ValueError) as caught:
            Community().span()
        self.assertIn("no events", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
