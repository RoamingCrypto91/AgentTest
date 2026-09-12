"""The gate chain, the verdicts, and the choice of what to prescribe."""

import unittest

from vitals import model, prescribe
from vitals.metrics import Measure


def measures(**values):
    """A measure set where every gate's primaries are set by hand."""
    out = {}
    for gate in list(model.GATES) + [model.STRUCTURE]:
        for key in gate.primary + gate.supporting:
            out.setdefault(key, Measure(key, key.replace("_", " ")))
    for key, value in values.items():
        m = out[key]
        m.value = value
    return out


#: values that put every measure comfortably in the holding band
FINE = {
    "never_spoke": 0.72, "activation": 0.30, "first_post_answered": 0.92,
    "time_to_first_post": 6.0, "returned": 0.70, "retention_30d": 0.50,
    "weekly_active_trend": 0.2, "member_to_member": 0.80, "orbit": 0.05,
    "median_partners": 20.0, "conversational": 0.70,
    "first_post_answered_by_member": 0.70, "connectors": 30.0,
    "staff_voice": 0.08, "absence_resilience": 0.98, "bus_factor": 25.0,
    "concentration": 0.50, "graveyards": 0.0, "rooms_per_person": 1.0,
}


def tweak(**changes):
    values = dict(FINE)
    values.update(changes)
    return measures(**values)


class Verdicts(unittest.TestCase):
    def test_a_healthy_set_holds_every_gate(self):
        a = model.assess(tweak())
        self.assertIsNone(a["binding"])
        for gate in a["gates"]:
            self.assertEqual(gate.verdict, model.HOLDING, gate.gate.key)

    def test_a_measure_with_no_value_is_not_a_failure(self):
        values = dict(FINE)
        values.pop("retention_30d")
        a = model.assess(measures(**values))
        self.assertIsNone(a["binding"])
        self.assertIn("retention_30d", [m.key for m in a["unknowns"]])

    def test_the_binding_constraint_is_the_earliest_not_the_worst(self):
        a = model.assess(tweak(
            # arrival merely strained
            first_post_answered=0.55, activation=0.06,
            # relationship catastrophic
            member_to_member=0.0, orbit=1.0, median_partners=0.0,
        ))
        by_key = {g.gate.key: g for g in a["gates"]}
        self.assertEqual(by_key["relationship"].verdict, model.FAILING)
        self.assertEqual(a["binding"].gate.key, "arrival",
                         "a leak upstream has to be fixed before the flood below it")

    def test_a_gate_reports_its_own_worst_measure(self):
        a = model.assess(tweak(first_post_answered=0.05, activation=0.02))
        self.assertEqual(a["binding"].gate.key, "arrival")
        self.assertIn(a["binding"].worst.measure.key,
                      ("first_post_answered", "activation"))

    def test_structure_is_judged_but_is_never_the_binding_constraint(self):
        a = model.assess(tweak(graveyards=0.9, rooms_per_person=40.0,
                               concentration=0.99))
        self.assertEqual(a["structure"].verdict, model.FAILING)
        self.assertIsNone(a["binding"])

    def test_every_gate_states_a_mechanism_not_a_platitude(self):
        for gate in list(model.GATES) + [model.STRUCTURE]:
            with self.subTest(gate=gate.key):
                self.assertGreater(len(gate.mechanism), 80)
                self.assertTrue(gate.question.endswith("?"))


class Plans(unittest.TestCase):
    def test_nothing_is_prescribed_when_nothing_leaks(self):
        plan = prescribe.prescribe(model.assess(tweak()))
        self.assertEqual(plan["plan"], [])
        self.assertIn("nothing to unblock", plan["reason"])

    def test_a_plan_is_capped_so_that_it_gets_done(self):
        plan = prescribe.prescribe(model.assess(tweak(
            never_spoke=0.99, activation=0.0, first_post_answered=0.0,
            time_to_first_post=400.0,
        )))
        self.assertLessEqual(len(plan["plan"]), prescribe.MAX_PRESCRIBED)
        self.assertTrue(plan["plan"])

    def test_housekeeping_is_separate_from_the_plan(self):
        plan = prescribe.prescribe(model.assess(tweak(
            first_post_answered=0.0, graveyards=0.95, rooms_per_person=50.0,
        )))
        self.assertTrue(plan["housekeeping"])
        for action in plan["plan"]:
            self.assertNotEqual(action.gate, "structure")

    def test_every_intervention_can_be_falsified(self):
        for action in prescribe.LIBRARY:
            with self.subTest(action=action.key):
                self.assertTrue(action.moves, "an unfalsifiable prescription")
                self.assertGreater(action.review_days, 0)
                self.assertGreater(len(action.mechanism), 60)
                self.assertGreater(len(action.do), 80)

    def test_every_intervention_belongs_to_a_real_gate(self):
        keys = {g.key for g in model.GATES} | {model.STRUCTURE.key}
        for action in prescribe.LIBRARY:
            self.assertIn(action.gate, keys)

    def test_every_gate_has_something_to_offer(self):
        for gate in model.GATES:
            self.assertIn(gate.key, prescribe.BY_GATE, gate.key)

    def test_every_intervention_names_measures_that_exist(self):
        known = set(measures())
        for action in prescribe.LIBRARY:
            for key in action.moves + action.trigger:
                self.assertIn(key, known, f"{action.key} names {key}")


if __name__ == "__main__":
    unittest.main()
