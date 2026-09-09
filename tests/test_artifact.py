"""The showcase page is built from the real tools, so it cannot drift.

Every figure on `tools/artifact.html` is filled in at build time by running
the command it claims to be showing.  These tests check that the template and
the builder still agree, and that nothing on the page is transcribed by hand.
"""

import json
import os
import re
import sys

from framework import case, contains, eq, is_true
from support import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_artifact as B  # noqa: E402

TEMPLATE = os.path.join(ROOT, "tools", "artifact.html")


def page(tmp="artifact-test.html"):
    out = os.path.join(ROOT, "build", tmp)
    text = B.build(out)
    os.remove(out)
    return text


def payload(text):
    m = re.search(r'<script id="payload" type="application/json">(.*?)</script>',
                  text, re.S)
    is_true(m, "the page has no payload")
    return json.loads(m.group(1))


def test_the_page_builds():
    text = page()
    is_true("__DATA__" not in text, "the payload placeholder was left in place")
    contains(text, "<title>")
    is_true(len(text) > 100_000, "the page came out suspiciously small")


def test_every_element_the_script_fills_exists_in_the_page():
    with open(TEMPLATE) as fh:
        text = fh.read()
    wanted = set(re.findall(r'getElementById\("([\w-]+)"\)', text))
    present = set(re.findall(r'id="([\w-]+)"', text))
    missing = sorted(wanted - present)
    eq(missing, [], f"the script fills elements that are not there: {missing}")


def test_every_figure_is_produced_by_running_something():
    data = payload(page())
    for key in ("demos", "diagnostic", "inversion", "doctor", "fibonacci",
                "conditional", "verify"):
        is_true(key in data, f"the page lost its {key} data")
    eq(len(data["demos"]), 3)
    for demo in data["demos"]:
        is_true(demo["trace"]["frames"], f"{demo['label']} recorded no frames")
        is_true(demo["trace"]["code"], f"{demo['label']} recorded no code")


def test_the_verify_figure_shows_a_real_search():
    v = payload(page())["verify"]
    contains(v["broken"], "FAILED")
    contains(v["broken"], "smallest input found: x = 1, y = 0")
    contains(v["clean"], "every procedure was reversible on every input tried")
    is_true("/tmp" not in v["broken"], "a temporary path leaked onto the page")


def test_the_page_quotes_no_html_it_did_not_escape():
    data = payload(page())
    blob = json.dumps(data)
    is_true("</script" not in blob, "an unescaped closing tag reached the payload")


@case("verify-src")
@case("verify-broken")
@case("verify-clean")
@case("doctor")
@case("fib")
def test_the_page_has_the_figure(name):
    with open(TEMPLATE) as fh:
        contains(fh.read(), f'id="{name}"')
