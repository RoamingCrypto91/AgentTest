"""The scrubbable HTML visualiser."""

import json
import os
import re
import shutil
import subprocess
import tempfile

from framework import case, contains, eq, is_true, skip, slow
from support import EXAMPLES, ROOT
from reverie.cli import load_module
from reverie.checker import analyze
from reverie.compiler import Compiler
from reverie.diagnostics import Source
from reverie.viz import build_page

SRC = """int x;
int y;
stack s;
proc main() {
    from x == 0 loop { x += 1; } until x == 4;
    local int t = x; push(t, s); delocal int t = 0;
    y += x * 3;
    print "y = ", y;
    embed (y ^= r) { var r = 0; while (r < 3) { r = r + 1; } }
}
"""


def page(src=SRC, initial=None, name="t.rev"):
    from reverie.compiler import compile_text

    prog = compile_text(src, name)
    return build_page(prog, Source(name, src), initial or {})


def payload_of(html):
    body = html.split('<script id="trace" type="application/json">')[1]
    body = body.split("</script>")[0]
    return json.loads(body.replace("<\\/", "</"))


def test_page_is_self_contained():
    html = page()
    is_true(html.startswith("<!doctype html>"))
    contains(html, "</html>")
    # no network references of any kind
    eq(re.findall(r"(?:src|href)=[\"']https?://", html), [])


def test_the_trace_is_embedded_and_safe():
    html = page()
    marker = '<script id="trace" type="application/json">'
    body = html.split(marker)[1].split("</script>")[0]
    is_true("</script" not in body, "the payload must not close its own tag")
    data = json.loads(body.replace("<\\/", "</"))
    is_true(len(data["frames"]) > 20)


def test_payload_carries_everything_a_viewer_needs():
    data = payload_of(page())
    for key in ("sources", "globals", "code", "procs", "frames", "stats", "start_mem"):
        is_true(key in data, f"missing {key}")
    eq([g["name"] for g in data["globals"]], ["x", "y", "s"])
    eq(data["stats"]["bits_erased"], 0)
    eq(data["output"], ["y = 12"])


def test_deltas_carry_both_values():
    data = payload_of(page())
    deltas = [f["c"] for f in data["frames"] if "c" in f]
    is_true(deltas, "no memory deltas recorded")
    for d in deltas:
        for pair in d.values():
            eq(len(pair), 2)


def test_stack_and_output_events_are_recorded():
    data = payload_of(page())
    is_true(any("k" in f for f in data["frames"]), "no stack event")
    is_true(any("e" in f for f in data["frames"]), "no output event")


def test_imported_files_are_all_included():
    path = os.path.join(EXAMPLES, "sorting.rev")
    module = load_module(path)
    a = analyze(module)
    a.diagnostics.raise_if_errors()
    prog = Compiler(module, a).compile()
    with open(path) as fh:
        text = fh.read()
    html = build_page(prog, Source(path, text), {"xs": [5, 3, 9, 1, 7, 2, 8, 4]})
    data = payload_of(html)
    names = sorted(os.path.basename(n) for n in data["sources"])
    eq(names, ["sort.rev", "sorting.rev"])
    files = {f.get("f", "") for f in data["frames"]}
    is_true(any("sort.rev" in f for f in files))


def test_truncation_is_flagged():
    from reverie.compiler import compile_text

    prog = compile_text(SRC, "t.rev")
    html = build_page(prog, Source("t.rev", SRC), {}, limit=10)
    data = payload_of(html)
    eq(data["truncated"], True)
    eq(len(data["frames"]), 10)


def test_replaying_the_deltas_reproduces_the_final_state():
    """The viewer's model, implemented here, must agree with the machine."""
    data = payload_of(page())
    mem = list(data["start_mem"])
    stacks = [[] for _ in range(data["n_stacks"])]
    lines, line = [], ""
    for f in data["frames"]:
        for k, (_, after) in f.get("c", {}).items():
            mem[int(k)] = after
        if "k" in f:
            idx, op, val = f["k"]
            (stacks[idx].append(val) if op == "push" else stacks[idx].pop())
        if "e" in f:
            text, ends, undo = f["e"]
            if not undo:
                if ends:
                    lines.append(line + text)
                    line = ""
                else:
                    line += text
    named = {}
    for g in data["globals"]:
        if g["kind"] == "array":
            named[g["name"]] = mem[g["addr"]: g["addr"] + g["size"]]
        elif g["kind"] == "stack":
            named[g["name"]] = stacks[mem[g["addr"]] - 1]
        else:
            named[g["name"]] = mem[g["addr"]]
    eq(named, data["final"])
    eq(lines, data["output"])


def test_unapplying_the_deltas_returns_to_the_start():
    data = payload_of(page())
    mem = list(data["start_mem"])
    for f in data["frames"]:
        for k, (_, after) in f.get("c", {}).items():
            mem[int(k)] = after
    for f in reversed(data["frames"]):
        for k, (before, _) in f.get("c", {}).items():
            mem[int(k)] = before
    eq(mem, data["start_mem"])


# ---------------------------------------------------------------------------
# a real browser, when one is available
# ---------------------------------------------------------------------------

DRIVER = r"""
import { chromium } from '%s/playwright/index.mjs';
const file = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
await page.goto('file://' + file);
const max = await page.$eval('#scrub', e => +e.max);
const read = () => page.evaluate(() => ({
  t: +document.getElementById('t').textContent,
  globals: document.getElementById('globals').innerText,
  output: document.getElementById('output').innerText,
  stacks: document.getElementById('stacks').innerText,
}));
await page.evaluate(m => goto(m), max);
const end = await read();
await page.evaluate(() => goto(0));
const start = await read();
for (let i = 0; i < 60; i++)
  await page.evaluate(n => goto(n), Math.floor(Math.random() * (max + 1)));
await page.evaluate(m => goto(m), max);
const end2 = await read();
await page.evaluate(() => goto(0));
const start2 = await read();
await browser.close();
console.log(JSON.stringify({ max, end, start, end2, start2, errors }));
"""


def _node_modules():
    node = shutil.which("node")
    if node is None:
        return None
    for base in ("/opt/node22/lib/node_modules", "/usr/lib/node_modules"):
        if os.path.isdir(os.path.join(base, "playwright")):
            return base
    return None


@slow
def test_the_page_scrubs_exactly_in_a_real_browser():
    base = _node_modules()
    if base is None:
        skip("playwright is not installed")
    tmp = tempfile.mkdtemp(prefix="reverie-viz-")
    html_path = os.path.join(tmp, "page.html")
    with open(html_path, "w") as fh:
        fh.write(page(initial={}))
    driver = os.path.join(tmp, "drive.mjs")
    with open(driver, "w") as fh:
        fh.write(DRIVER % base)
    out = subprocess.run(
        ["node", driver, html_path], capture_output=True, text=True, timeout=120
    )
    is_true(out.returncode == 0, out.stderr[-2000:])
    result = json.loads(out.stdout.strip().splitlines()[-1])
    eq(result["errors"], [])
    eq(result["end"], result["end2"], "replaying to the end is not deterministic")
    eq(result["start"], result["start2"], "scrubbing back to zero is not exact")
    eq(result["start"]["t"], 0)
    contains(result["end"]["output"], "y = 12")
    eq(result["start"]["output"], "")
