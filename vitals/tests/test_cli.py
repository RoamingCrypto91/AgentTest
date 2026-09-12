"""The command line, driven in-process."""

import contextlib
import io
import json
import os
import tempfile
import unittest

from vitals import bench, cli


def run(*argv, expect=0):
    out = io.StringIO()
    code = cli.main(list(argv), out=out)
    if expect is not None:
        assert code == expect, f"vitals {' '.join(argv)} -> {code}\n{out.getvalue()}"
    return out.getvalue()


def tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="vitals-cli-"), name)


class Commands(unittest.TestCase):
    def test_no_arguments_prints_help(self):
        out = io.StringIO()
        self.assertEqual(cli.main([], out=out), 1)
        self.assertIn("audit", out.getvalue())

    def test_a_template_can_be_audited_immediately(self):
        events = tmp("events.csv")
        run("template", "-o", events)
        self.assertTrue(os.path.exists(events))
        report = tmp("report.html")
        text = run("audit", events, "-o", report)
        self.assertIn("report written to", text)
        self.assertGreater(os.path.getsize(report), 10_000)

    def test_show_prints_the_gates_and_the_plan(self):
        events = tmp("events.csv")
        run("template", "-o", events)
        text = run("show", events)
        for gate in ("Arrival", "Return", "Relationship", "Contribution",
                     "Stewardship", "Structure"):
            self.assertIn(gate, text)

    def test_a_demo_report_can_be_built_without_any_data(self):
        page = tmp("demo.html")
        text = run("demo", "dying", "-o", page)
        self.assertIn("binding constraint", text.lower())
        with open(page) as fh:
            self.assertIn("<title>", fh.read())

    def test_the_audit_can_be_kept_as_json(self):
        events = tmp("events.csv")
        run("template", "-o", events)
        blob = tmp("audit.json")
        run("audit", events, "-o", tmp("r.html"), "--json", blob)
        with open(blob) as fh:
            data = json.load(fh)
        self.assertIn("measures", data)
        self.assertIn("gates", data)
        self.assertIn("first_post_answered", data["measures"])

    def test_a_window_can_be_narrowed(self):
        events = tmp("events.csv")
        run("template", "-o", events)
        text = run("show", events, "--window", "3")
        self.assertIn("Arrival", text)

    def test_a_missing_file_is_a_sentence_not_a_traceback(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = cli.main(["show", "/no/such/export.csv"], out=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("export.csv", errors.getvalue())

    def test_benchmarks_refuse_to_be_built_from_too_little(self):
        events = tmp("events.csv")
        run("template", "-o", events)
        blob = tmp("audit.json")
        run("audit", events, "-o", tmp("r.html"), "--json", blob)
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = cli.main(["benchmark", blob, "-o", tmp("b.json")],
                            out=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("not enough", errors.getvalue())

    def test_benchmarks_build_once_there_are_enough_audits(self):
        folder = tempfile.mkdtemp(prefix="vitals-bench-")
        paths = []
        for i in range(bench.MINIMUM_AUDITS):
            path = os.path.join(folder, f"a{i}.json")
            with open(path, "w") as fh:
                json.dump({"measures": {
                    "first_post_answered": {"value": 0.3 + i * 0.1},
                    "orbit": {"value": 0.5 - i * 0.05},
                }}, fh)
            paths.append(path)
        out = os.path.join(folder, "bands.json")
        text = run("benchmark", *paths, "-o", out)
        self.assertIn("bands for 2 measures", text)
        with open(out) as fh:
            bands = json.load(fh)
        self.assertLess(bands["first_post_answered"]["weak"],
                        bands["first_post_answered"]["strong"])

    def test_measured_benchmarks_replace_the_provisional_ones(self):
        from vitals import benchmarks

        folder = tempfile.mkdtemp(prefix="vitals-bands-")
        path = os.path.join(folder, "bands.json")
        with open(path, "w") as fh:
            json.dump({"orbit": {"weak": 0.9, "strong": 0.1,
                                 "typical": 0.4, "note": "measured"}}, fh)
        try:
            benchmarks.load(path)
            self.assertEqual(benchmarks.BANDS["orbit"].weak, 0.9)
            self.assertEqual(benchmarks.SOURCE, path)
        finally:
            benchmarks.BANDS.update({"orbit": benchmarks.Band(0.50, 0.15, 0.30)})
            benchmarks.SOURCE = "judgement"

    def test_a_typo_in_a_benchmark_file_is_refused(self):
        from vitals import benchmarks

        folder = tempfile.mkdtemp(prefix="vitals-bands-")
        path = os.path.join(folder, "bands.json")
        with open(path, "w") as fh:
            json.dump({"orbitt": {"weak": 0.9, "strong": 0.1}}, fh)
        with self.assertRaises(KeyError) as caught:
            benchmarks.load(path)
        self.assertIn("orbitt", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
