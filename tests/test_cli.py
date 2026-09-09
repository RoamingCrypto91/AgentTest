"""The `rev` command line, driven in-process."""

import io
import json
import os
import sys
import tempfile

from framework import case, contains, eq, is_true, raises
from support import EXAMPLES, ROOT, STDLIB
from reverie.cli import main


class Captured:
    def __init__(self) -> None:
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.code = 0

    @property
    def text(self) -> str:
        return self.out.getvalue()

    @property
    def errors(self) -> str:
        return self.err.getvalue()


def rev(*argv, expect=0) -> Captured:
    cap = Captured()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = cap.out, cap.err
    try:
        cap.code = main(list(argv))
    except SystemExit as exc:
        cap.code = exc.code if isinstance(exc.code, int) else 1
        if isinstance(exc.code, str):
            cap.err.write(exc.code)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    if expect is not None:
        eq(cap.code, expect, f"rev {' '.join(argv)}\n{cap.text}\n{cap.errors}")
    return cap


def write(text, name="prog.rev"):
    d = tempfile.mkdtemp(prefix="reverie-")
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


SAMPLE = """int n;
int total;
proc main() {
    local int i = 0;
    from i == 0 do { total += i; } loop { i += 1; } until i == n;
    delocal int i = n;
    print "sum = ", total;
}
"""


def test_version():
    cap = rev("--version")
    contains(cap.text, "reverie")


def test_no_arguments_prints_help():
    cap = rev(expect=1)
    contains(cap.text, "usage")


def test_run():
    path = write(SAMPLE)
    cap = rev("run", path, "--set", "n=5")
    contains(cap.text, "sum = 15")
    contains(cap.text, "total = 15")


def test_run_quiet_shows_only_output():
    path = write(SAMPLE)
    cap = rev("run", path, "--set", "n=5", "-q")
    eq(cap.text.strip(), "sum = 15")


def test_run_with_stats_and_clean_check():
    path = write(SAMPLE)
    cap = rev("run", path, "--set", "n=4", "--stats", "--check-clean")
    contains(cap.text, "steps=")
    contains(cap.text, "bits erased: 0")


def test_set_accepts_arrays_and_bases():
    path = write("int a[4]; int x; proc main() { x += a[0] + a[3]; }")
    cap = rev("run", path, "--set", "a=1,2,3,4", "--set", "x=0x10")
    contains(cap.text, "x = 21")


def test_set_rejects_nonsense():
    path = write(SAMPLE)
    cap = rev("run", path, "--set", "nonsense", expect=1)
    contains(cap.errors + cap.text, "name=value")


def test_back_proves_the_round_trip():
    path = write(SAMPLE)
    cap = rev("back", path, "--set", "n=6")
    contains(cap.text, "state identical")


def test_check_reports_the_shape():
    path = write(SAMPLE)
    cap = rev("check", path)
    contains(cap.text, ": ok")
    contains(cap.text, "main()")


def test_check_reports_errors_and_exits_nonzero():
    path = write("int x; proc main() { x += x; }")
    cap = rev("check", path, expect=1)
    contains(cap.errors, "may not appear")


def test_check_of_a_library():
    cap = rev("check", os.path.join(STDLIB, "array.rev"))
    contains(cap.text, "library")
    contains(cap.text, "read-only")


def test_fmt():
    path = write("int x;proc main(){x+=1+2*3;}")
    cap = rev("fmt", path)
    contains(cap.text, "x += 1 + 2 * 3;")


def test_fmt_in_place():
    path = write("int x;proc main(){x+=1;}")
    rev("fmt", path, "-i")
    with open(path) as fh:
        contains(fh.read(), "    x += 1;")


def test_invert():
    path = write(SAMPLE)
    cap = rev("invert", path)
    contains(cap.text, "total -= i;")
    contains(cap.text, "unprint")


def test_invert_to_a_file_and_run_it():
    path = write(SAMPLE)
    out = path.replace(".rev", "_inv.rev")
    rev("invert", path, "-o", out)
    cap = rev("check", out)
    contains(cap.text, ": ok")


def test_invert_keeping_the_original():
    path = write(SAMPLE)
    cap = rev("invert", path, "--keep", "--suffix", "_back")
    contains(cap.text, "proc main()")
    contains(cap.text, "proc main_back()")


def test_disasm():
    path = write(SAMPLE)
    cap = rev("disasm", path)
    contains(cap.text, "call main()")
    contains(cap.text, "halt")
    contains(cap.text, "globals:")


def test_debug_with_commands():
    path = write(SAMPLE)
    cap = rev("debug", path, "--set", "n=3", "-c", "run", "-c", "globals")
    contains(cap.text, "total = 6")


def test_trace_to_stdout_is_json():
    path = write(SAMPLE)
    cap = rev("trace", path, "--set", "n=3")
    data = json.loads(cap.text)
    eq(data["final"]["total"], 6)
    is_true(len(data["frames"]) > 10)
    eq(data["stats"]["bits_erased"], 0)


def test_trace_to_a_file():
    path = write(SAMPLE)
    out = path + ".json"
    cap = rev("trace", path, "--set", "n=3", "-o", out)
    contains(cap.text, "frames")
    with open(out) as fh:
        json.load(fh)


def test_doctor():
    path = write(SAMPLE)
    cap = rev("doctor", path, "--set", "n=5")
    contains(cap.text, "bits erased       0")
    contains(cap.text, "reversible        yes")


def test_run_backward_flag():
    path = write("int x; proc main() { x += 5; }")
    cap = rev("run", path, "--set", "x=5", "--backward")
    contains(cap.text, "x = 0")


def test_a_trap_is_reported_with_notes():
    path = write("int x; proc main() { x *= 0; }")
    cap = rev("run", path, expect=2)
    contains(cap.errors, "destroys information")
    contains(cap.errors, "note:")


def test_missing_file():
    cap = rev("run", "/definitely/not/here.rev", expect=1)
    contains(cap.errors, "No such file")


def test_import_resolution_finds_the_stdlib():
    path = write(
        'import "math.rev";\nint n; int r; int m;\n'
        "proc main() { call isqrt(n, r, m); }"
    )
    cap = rev("run", path, "--set", "n=144")
    contains(cap.text, "r = 12")


def test_import_of_a_missing_module_names_the_search_path():
    path = write('import "nowhere.rev";\nproc main() { skip; }')
    cap = rev("run", path, expect=1)
    contains(cap.errors, "cannot find module")
    contains(cap.errors, "searched")


def test_stdin_input():
    old = sys.stdin
    sys.stdin = io.StringIO("int x; proc main() { x += 2; }")
    try:
        cap = rev("run", "-")
        contains(cap.text, "x = 2")
    finally:
        sys.stdin = old


# ---------------------------------------------------------------------------
# an import names a module, not an arbitrary file
# ---------------------------------------------------------------------------


def test_absolute_imports_are_refused():
    path = write('import "/etc/passwd";\nproc main() { skip; }')
    cap = rev("run", path, expect=1)
    contains(cap.errors, "not absolute")


def test_imports_may_not_escape_the_search_roots():
    d = tempfile.mkdtemp(prefix="reverie-")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "secret.rev"), "w") as fh:
        fh.write("int leaked;\n")
    path = os.path.join(d, "sub", "prog.rev")
    with open(path, "w") as fh:
        fh.write('import "../secret.rev";\nproc main() { skip; }')
    cap = rev("run", path, expect=1)
    contains(cap.errors, "outside the search roots")


def test_imports_must_name_a_reverie_module():
    d = tempfile.mkdtemp(prefix="reverie-")
    with open(os.path.join(d, "notes.txt"), "w") as fh:
        fh.write("int x;\n")
    path = os.path.join(d, "prog.rev")
    with open(path, "w") as fh:
        fh.write('import "notes.txt";\nproc main() { skip; }')
    cap = rev("run", path, expect=1)
    contains(cap.errors, "cannot find module")


def test_a_sibling_import_still_works():
    d = tempfile.mkdtemp(prefix="reverie-")
    with open(os.path.join(d, "lib.rev"), "w") as fh:
        fh.write("proc bump(int p) { p += 7; }\n")
    path = os.path.join(d, "prog.rev")
    with open(path, "w") as fh:
        fh.write('import "lib.rev";\nint x;\nproc main() { call bump(x); }')
    cap = rev("run", path)
    contains(cap.text, "x = 7")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


BAD_PROC = """
proc drain(int x, int y) {
    if x > 0 {
        x -= 1;
    } else {
        y += 1;
    } fi x > 0;
}
proc main() { }
"""


def test_verify_reports_a_clean_library():
    cap = rev("verify", os.path.join(STDLIB, "array.rev"), "--cases", "8")
    contains(cap.text, "every procedure was reversible on every input tried")
    contains(cap.text, "reverse_range(int xs[], int lo, int hi)")


def test_verify_fails_and_says_why():
    path = write(BAD_PROC)
    cap = rev("verify", path, "--cases", "40", expect=1)
    contains(cap.text, "FAILED")
    contains(cap.text, "smallest input found: x = 1, y = 0")
    contains(cap.text, "1 of 2 procedures failed")


def test_verify_can_be_pointed_at_one_procedure():
    path = write(BAD_PROC)
    cap = rev("verify", path, "--proc", "main", "--cases", "5")
    contains(cap.text, "1 procedure,")
    is_true("drain" not in cap.text, "only `main` was asked for")


def test_verify_rejects_a_procedure_that_is_not_there():
    path = write(BAD_PROC)
    cap = rev("verify", path, "--proc", "nope", expect=1)
    contains(cap.errors + cap.text, "no procedure named")


def test_verify_takes_a_seed_and_repeats_itself():
    path = write(BAD_PROC)
    a = rev("verify", path, "--seed", "7", "--cases", "30", expect=1)
    b = rev("verify", path, "--seed", "7", "--cases", "30", expect=1)
    eq(a.text, b.text)


def test_verify_can_vary_globals_too():
    path = write("int n; proc main() { n += 1; }")
    cap = rev("verify", path, "--globals", "--cases", "6")
    contains(cap.text, "cases")


def test_verify_checks_every_step_when_asked():
    path = write("int n; proc main() { n += 1; }")
    cap = rev("verify", path, "--paranoid", "--cases", "3")
    contains(cap.text, "every procedure was reversible")
