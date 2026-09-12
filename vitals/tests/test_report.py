"""The report: the thing a client reads, so it has to hold together."""

import re
import unittest

from vitals import audit, benchmarks, report, simulate
from vitals.metrics import Measure


class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {
            name: report.render(audit.run(simulate.archetype(name)))
            for name in simulate.ARCHETYPES
        }

    def test_every_archetype_renders(self):
        for name, page in self.pages.items():
            with self.subTest(archetype=name):
                self.assertGreater(len(page), 12_000)
                self.assertIn("<title>", page)

    #: things that only appear in a page if a value was formatted wrongly
    LEAKS = (r"\bNone\b", r"\bnan\b", r"\binf\b", r"[{}]", r"Measure\(",
             r"object at 0x", r"<class ")

    def test_no_python_leaks_into_the_page(self):
        """The report is built by string formatting, so check none of it shows."""
        for name, page in self.pages.items():
            body = page.split("</style>")[-1]
            with self.subTest(archetype=name):
                for leak in self.LEAKS:
                    found = re.search(leak, body)
                    self.assertIsNone(
                        found,
                        f"{leak} matched {body[max(0, (found.start() if found else 0) - 40):][:90]!r}",
                    )

    def test_both_themes_are_defined(self):
        page = self.pages["mesh"]
        self.assertIn("prefers-color-scheme:dark", page)
        self.assertIn('[data-theme="dark"]', page)
        self.assertIn('[data-theme="light"]', page)
        # the light palette is declared on bare :root, not inside a media block
        bare = page.split("@media")[0]
        for token in ("--ground", "--ink", "--accent", "--failing"):
            self.assertIn(token, bare, token)

    def test_the_body_paints_its_own_background(self):
        self.assertIn("background:var(--ground)", self.pages["mesh"])

    def test_the_binding_gate_is_named_on_the_page(self):
        page = self.pages["broadcast"]
        self.assertIn("binding constraint", page)
        self.assertIn("Arrival", page)

    def test_a_healthy_community_is_told_so_plainly(self):
        self.assertIn("Every gate is holding", self.pages["mesh"])

    def test_the_prescriptions_appear_with_a_review_date(self):
        page = self.pages["broadcast"]
        self.assertIn("re-measure in", page)
        self.assertIn("Why it works", page)

    def test_the_method_note_says_no_content_was_read(self):
        for page in self.pages.values():
            self.assertIn("No message content was read", page)

    def test_provisional_benchmarks_are_admitted_to(self):
        if benchmarks.SOURCE == "judgement":
            self.assertIn("Benchmarks are provisional", self.pages["mesh"])

    def test_the_sparkline_is_drawn_inside_its_box(self):
        page = self.pages["mesh"]
        self.assertIn("<svg viewBox=\"0 0 880 150\"", page)
        for x, y in re.findall(r"(\d+\.\d),(\d+\.\d)", page):
            self.assertLessEqual(float(x), 880.0)
            self.assertLessEqual(float(y), 150.0)

    def test_every_svg_shape_has_an_explicit_fill(self):
        page = self.pages["mesh"]
        for tag in re.findall(r"<(polyline|circle|text|line)[^>]*>", page):
            pass
        for shape in re.findall(r"<(?:polyline|circle|text)\b[^>]*>", page):
            self.assertIn("fill=", shape, shape[:90])

    def test_graveyards_are_named_so_they_can_be_closed(self):
        self.assertIn("room-", self.pages["broadcast"])

    def test_html_in_a_community_name_cannot_escape(self):
        community = simulate.archetype("mesh")
        community.name = '<script>alert("x")</script>'
        page = report.render(audit.run(community))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)


class Formatting(unittest.TestCase):
    def check(self, unit, value, expected, key="k", good="high"):
        m = Measure(key, "label", value, unit)
        m.good = good
        self.assertEqual(report.fmt(m), expected)

    def test_shares_are_percentages(self):
        self.check("share", 0.4237, "42%")

    def test_hours_become_days_when_they_are_long(self):
        self.check("hours", 20.0, "20h")
        self.check("hours", 96.0, "4.0d")

    def test_counts_stay_readable(self):
        self.check("count", 3.0, "3.0")
        self.check("count", 42.0, "42")

    def test_a_trend_keeps_its_sign(self):
        m = Measure("weekly_active_trend", "label", -0.31, "ratio")
        self.assertEqual(report.fmt(m), "-31%")

    def test_an_unmeasured_thing_says_so(self):
        self.assertEqual(report.fmt(Measure("k", "label")), "not measurable")


if __name__ == "__main__":
    unittest.main()
