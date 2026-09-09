"""The package's public API and `python3 -m reverie`."""

import os
import subprocess
import sys

from framework import contains, eq, is_true, raises
from support import ROOT
import reverie
from reverie import (
    Machine,
    Program,
    ReverieError,
    check_source,
    compile_source,
    invert_source,
    parse_source,
    run_source,
)

SRC = "int x; proc main() { from x == 0 loop { x += 1; } until x == 5; }"


def test_version_is_a_string():
    is_true(reverie.__version__.count(".") >= 1)
    eq(sorted(reverie.__all__)[0], "Machine")


def test_the_docstring_example_works():
    m = run_source(SRC)
    eq(m.globals_dict()["x"], 5)
    eq(m.start_backward().run().globals_dict()["x"], 0)


def test_parse_source():
    module = parse_source(SRC, "api.rev")
    eq(module.source_name, "api.rev")
    eq([p.name for p in module.procs()], ["main"])


def test_check_source_reports_without_raising():
    a = check_source("int x; proc main() { x += x; }", "api.rev")
    is_true(not a.ok)
    contains(str(a.diagnostics.errors[0]), "may not appear")


def test_compile_source():
    prog = compile_source(SRC, "api.rev")
    is_true(isinstance(prog, Program))
    eq(prog.entry, "main")


def test_run_source_takes_initial_state():
    m = run_source("int n; int t; proc main() { t += n * 2; }", initial={"n": 21})
    eq(m.globals_dict()["t"], 42)


def test_invert_source():
    got = invert_source("int x; proc main() { x += 1; }")
    contains(got, "x -= 1;")


def test_errors_are_raised_from_the_convenience_wrappers():
    with raises(ReverieError):
        compile_source("int x; proc main() { x += x; }")


def test_module_entry_point():
    out = subprocess.run(
        [sys.executable, "-m", "reverie", "--version"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    eq(out.returncode, 0, out.stderr)
    contains(out.stdout, "reverie")


def test_the_shell_wrapper_runs():
    out = subprocess.run(
        [os.path.join(ROOT, "rev"), "run", "examples/first.rev", "--set", "n=21"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    eq(out.returncode, 0, out.stderr)
    contains(out.stdout, "total = 42")
