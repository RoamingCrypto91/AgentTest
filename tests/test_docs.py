"""The documentation is executed, not just written.

Two checks: every Reverie snippet in the docs is real syntax, and every
`$ rev …` transcript reproduces the lines it claims.
"""

import io
import os
import re
import sys

from framework import case, contains, eq, is_true, skip
from support import ROOT
from reverie.diagnostics import ReverieError
from reverie.parser import parse_text

DOCS = [os.path.join(ROOT, "README.md")] + [
    os.path.join(ROOT, "docs", f)
    for f in sorted(os.listdir(os.path.join(ROOT, "docs")))
    if f.endswith(".md")
]

#: fragments in the docs are written against these declarations
HARNESS = """
const N = 8;
int x; int y; int n; int total; int root; int rem; int scratch; int i;
int grid[N]; int a[N]; int xs[N]; int buf[N];
stack s; stack trail;
proc expensive(int p, int q) { q += p; }
proc name(int p, int q) { q += p; }
proc twice(int p, int q) { p += q * 2; }
proc reverse_range(int zs[], int lo, int hi) { zs[lo] += hi; }
proc main() {
%s
}
"""


def fenced(text, tag):
    return re.findall(rf"```{tag}\n(.*?)```", text, re.S)


def snippets():
    for path in DOCS:
        with open(path) as fh:
            text = fh.read()
        for block in fenced(text, "reverie"):
            yield os.path.basename(path), block


def _check_snippets(doc):
    path = next(p for p in DOCS if os.path.basename(p) == doc)
    with open(path) as fh:
        blocks = fenced(fh.read(), "reverie")
    is_true(blocks or doc == "ISA.md", f"{doc} has no Reverie snippets")
    for i, block in enumerate(blocks):
        if "…" in block or "..." in block:
            continue  # deliberately elided
        try:
            parse_text(block, f"{doc}#{i}")
            continue
        except ReverieError:
            pass
        try:
            parse_text(HARNESS % block, f"{doc}#{i}")
        except ReverieError as exc:
            raise AssertionError(
                f"{doc} snippet {i} does not parse:\n{block}\n\n{exc}"
            )


# ---------------------------------------------------------------------------
# transcripts
# ---------------------------------------------------------------------------


def transcripts():
    """Yield (doc, argv, expected lines) for each `$ rev …` block."""
    for path in DOCS:
        with open(path) as fh:
            text = fh.read()
        for block in fenced(text, "console"):
            lines = block.splitlines()
            argv, expected = None, []
            for line in lines + ["$ end"]:
                if line.startswith("$ "):
                    if argv is not None:
                        yield os.path.basename(path), argv, expected
                    argv, expected = line[2:].split(), []
                elif argv is not None:
                    expected.append(line)
            # trailing block handled by the sentinel above


def run_rev(argv):
    from reverie.cli import main

    out = io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = out
    try:
        main(argv)
    except SystemExit:
        pass
    finally:
        sys.stdout, sys.stderr = old
    return out.getvalue()


REAL = []
for _doc, _argv, _expected in transcripts():
    if not _argv or _argv[0] not in ("rev", "./rev"):
        continue
    if not any(a.endswith(".rev") for a in _argv):
        continue
    _cmds = [l.strip()[6:] for l in _expected if l.strip().startswith("(rev) ")]
    _lines = [
        l for l in _expected if l.strip() and not l.strip().startswith("(rev) ")
    ]
    _call = list(_argv[1:])
    for _c in _cmds:
        # a debugger transcript: the `(rev)` lines become -c commands, so the
        # session is driven rather than waiting for a terminal that is not there
        _call += ["-c", _c]
    if not _lines:
        continue
    REAL.append((_doc, _call, _lines))


def _check_transcript(doc, argv, expected):
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        got = run_rev(list(argv))
    finally:
        os.chdir(cwd)
    got_lines = [l.rstrip() for l in got.splitlines()]
    pos = 0
    for want in expected:
        want = want.rstrip()
        while pos < len(got_lines) and got_lines[pos] != want:
            pos += 1
        if pos >= len(got_lines):
            raise AssertionError(
                f"{doc}: `rev {' '.join(argv)}` never printed\n  {want!r}\n"
                f"--- actual output ---\n{got}"
            )
        pos += 1


test_every_reverie_snippet_is_real_syntax = _check_snippets
for _p in DOCS:
    test_every_reverie_snippet_is_real_syntax = case(os.path.basename(_p))(
        test_every_reverie_snippet_is_real_syntax
    )

test_transcript = _check_transcript
for _d, _a, _e in REAL:
    test_transcript = case(_d, _a, _e)(test_transcript)


def test_there_are_transcripts_to_check():
    is_true(len(REAL) >= 4, f"only {len(REAL)} runnable transcripts found")


def test_documented_commands_all_exist():
    from reverie.cli import make_parser

    parser = make_parser()
    known = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            known |= set(action.choices)
    for path in DOCS:
        with open(path) as fh:
            text = fh.read()
        for m in re.finditer(r"`rev (\w+)", text):
            verb = m.group(1)
            if verb in ("run", "back"):
                continue
            is_true(
                verb in known,
                f"{os.path.basename(path)} mentions `rev {verb}`, which does not exist",
            )


def test_every_example_mentioned_in_the_readme_exists():
    with open(os.path.join(ROOT, "README.md")) as fh:
        text = fh.read()
    for name in set(re.findall(r"examples/([\w.]+\.rev)", text)):
        is_true(
            os.path.exists(os.path.join(ROOT, "examples", name)),
            f"README mentions examples/{name}, which is missing",
        )


def test_every_example_is_mentioned_in_the_readme():
    with open(os.path.join(ROOT, "README.md")) as fh:
        text = fh.read()
    for name in sorted(os.listdir(os.path.join(ROOT, "examples"))):
        if name.endswith(".rev"):
            contains(text, name)
