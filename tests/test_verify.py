"""`rev verify` -- the search for inputs a procedure cannot undo."""

import os

from framework import case, contains, eq, is_true, raises, slow
from support import EXAMPLES, STDLIB
from reverie import verify as V
from reverie.cli import load_module
from reverie.diagnostics import ReverieError, Source
from reverie.parser import parse


def module(text):
    return parse(Source("t.rev", text))


def check(text, name=None, **kw):
    """Verify one procedure of a small module and return its report."""
    m = module(text)
    kw.setdefault("cases", 40)
    reports = V.verify_module(m, [name] if name else None, **kw)
    return reports[0] if name else reports


MAIN = "\nproc main() { }\n"

GOOD = """
/// Add k to every element.
proc bump(int xs[], int k) {
    local int i = 0;
    from i == 0 do {
        xs[i] += k;
    } loop {
        i += 1;
    } until i == len(xs) - 1;
    delocal int i = len(xs) - 1;
}
""" + MAIN

# The exit test is the one the language relies on to tell the branches apart on
# the way back, and here it is wrong: after the then-branch, x is no longer
# positive when it started at 1.
BROKEN = """
proc drain(int x, int y) {
    if x > 0 {
        x -= 1;
    } else {
        y += 1;
    } fi x > 0;
}
""" + MAIN


# ---------------------------------------------------------------------------
# the property itself
# ---------------------------------------------------------------------------


def test_a_reversible_procedure_survives_the_search():
    r = check(GOOD, "bump")
    is_true(r.ok, r.failure and r.failure.describe())
    eq(r.cases, 40)
    is_true(r.steps > 0, "no instructions were executed")


def test_a_broken_exit_test_is_found():
    r = check(BROKEN, "drain")
    is_true(not r.ok, "the search missed a conditional that cannot be reversed")
    contains(r.failure.reason, "exit assertion")
    contains(r.failure.prop, "undo")


def test_the_counterexample_is_shrunk_to_the_smallest_one():
    r = check(BROKEN, "drain")
    eq(r.failure.inputs, {"x": 1, "y": 0})
    is_true(r.failure.shrinks > 0, "nothing was shrunk")


def test_the_counterexample_names_the_line_it_broke_on():
    r = check(BROKEN, "drain")
    contains(r.failure.where, "t.rev:3")


def test_the_search_is_repeatable():
    a = check(BROKEN, "drain")
    b = check(BROKEN, "drain")
    eq(a.cases, b.cases)
    eq(a.failure.inputs, b.failure.inputs)


def test_a_different_seed_still_finds_it():
    r = check(BROKEN, "drain", seed=99)
    is_true(not r.ok, "the bug hid from another seed")
    eq(r.failure.inputs, {"x": 1, "y": 0})


def test_every_property_is_checked():
    eq(V.PROPERTIES, ("undo", "uncall", "inverse"))
    for prop in V.PROPERTIES:
        f = V.Failure(prop, "why", {})
        is_true(f.describe(), "every property needs a sentence")


def calls(prog, proc="__verify_round"):
    """The calls one procedure body makes, as ``call p`` / ``uncall p``."""
    from reverie.isa import Call

    info = prog.procs[proc]
    body = prog.code[info.entry_at + 1:info.exit_at]
    return [("uncall " if i.uncall else "call ") + i.proc
            for i in body if isinstance(i, Call)]


def test_uncall_is_run_forwards_not_as_an_undo():
    """The second property compiles a program; check it says what we mean."""
    m = module(GOOD)
    d = V.Driver(m, m.find_proc("bump"), 4)
    eq(calls(d.round), ["call bump", "uncall bump"])
    eq(calls(d.once, "__verify_once"), ["call bump"])


def test_the_third_property_calls_the_printed_inverse():
    m = module(GOOD)
    d = V.Driver(m, m.find_proc("bump"), 4)
    eq(calls(d.mirror, "__verify_mirror"), ["call bump", "call __verify_inverse"])


def test_the_inverse_property_has_teeth():
    """With the inverter neutered, a procedure that is not self-inverse fails."""
    m = module(GOOD)
    real = V.invert_proc
    V.invert_proc = lambda decl, rename: real(decl, rename).__class__(
        decl.span, rename, list(decl.params), decl.body, decl.doc
    )
    try:
        r = V.verify_proc(m, m.find_proc("bump"), cases=20)
    finally:
        V.invert_proc = real
    is_true(not r.ok, "calling `bump` twice should not be the identity")
    eq(r.failure.prop, "inverse")
    contains(r.failure.describe(), "rev invert")


# ---------------------------------------------------------------------------
# stating a domain
# ---------------------------------------------------------------------------


PARTIAL = """
proc poke(int xs[], int i) {
    xs[i] += 1;
}
""" + MAIN

GUARDED = """
/// requires: 0 <= i && i < len(xs)
proc poke(int xs[], int i) {
    xs[i] += 1;
}
""" + MAIN


def test_an_input_outside_the_domain_is_reported_without_a_requires():
    r = check(PARTIAL, "poke")
    is_true(not r.ok, "an out-of-range index went unnoticed")
    contains(r.failure.reason, "out of bounds")


def test_a_requires_keeps_the_search_inside_the_domain():
    r = check(GUARDED, "poke")
    is_true(r.ok, r.failure and r.failure.describe())
    eq(r.cases, 40)


def test_the_domain_also_constrains_shrinking():
    """A shrink that leaves the domain is not a counterexample."""
    text = """
/// requires: i >= 1
proc poke(int xs[], int i) {
    if i > 0 { xs[0] += 1; } else { skip; } fi i > 1;
}
""" + MAIN
    r = check(text, "poke")
    is_true(not r.ok, "the bad exit test was missed")
    eq(r.failure.inputs["i"], 1)


def test_an_unsatisfiable_domain_is_reported_rather_than_guessed_at():
    text = """
/// requires: i > 0 && i < 0
proc poke(int xs[], int i) {
    xs[0] += i;
}
""" + MAIN
    r = check(text, "poke", cases=3)
    contains(r.skipped, "no input satisfied")


def test_several_requires_lines_are_all_required():
    text = """
/// requires: 0 <= i
/// requires: i < len(xs)
proc poke(int xs[], int i) {
    xs[i] += 1;
}
""" + MAIN
    m = module(text)
    eq(V.requirement(m.find_proc("poke")), "(0 <= i) && (i < len(xs))")
    is_true(check(text, "poke").ok)


def test_a_nonsense_requires_is_a_checker_error_not_a_crash():
    text = """
/// requires: nosuchname > 0
proc poke(int xs[], int i) {
    xs[0] += i;
}
""" + MAIN
    r = check(text, "poke", cases=2)
    contains(r.skipped, "nosuchname")


# ---------------------------------------------------------------------------
# pinning arguments
# ---------------------------------------------------------------------------


def test_given_pins_an_argument():
    text = """
/// given: k = 3
proc bump(int xs[], int k) {
    xs[0] += k;
}
""" + MAIN
    m = module(text)
    d = V.Driver(m, m.find_proc("bump"), 4)
    import random

    for _ in range(20):
        drawn = V.gen_case(random.Random(0), d.slots, 12, 4, d.given)
        eq(drawn[d.argnames[1]], 3)


def test_given_finds_a_domain_rejection_would_miss():
    """A precondition of `a == 0 && b == 0` is a needle no filter would find."""
    text = """
/// given: a = 0, b = 0
/// requires: k >= 0
proc fib(int a, int b, int k) {
    if k == 0 {
        a += 1;
        b += 1;
    } else {
        k -= 1;
        call fib(a, b, k);
        a += b;
        a <=> b;
    } fi a == b;
}
""" + MAIN
    r = check(text, "fib", cases=10)
    is_true(r.ok, r.failure and r.failure.describe())
    is_true(r.cases > 1, "only one input was ever drawn")


def test_given_sizes_an_array_argument():
    text = """
/// given: len(out) = 12
proc widen(int src[], int out[]) {
    out[11] += src[0];
}
""" + MAIN
    r = check(text, "widen", cases=5, length=4)
    is_true(r.ok, r.failure and r.failure.describe())


def test_given_never_shrinks_away():
    text = """
/// given: y = 4
proc drain(int x, int y) {
    if x > 0 { x -= 1; } else { y += 1; } fi x > 0;
}
""" + MAIN
    r = check(text, "drain")
    eq(r.failure.inputs, {"x": 1, "y": 4})


def test_given_must_name_a_parameter():
    text = "/// given: zzz = 1\nproc f(int x) { x += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "not a parameter")


def test_given_wants_a_number():
    text = "/// given: x = four\nproc f(int x) { x += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "whole number")


def test_given_wants_a_pair():
    text = "/// given: x\nproc f(int x) { x += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "name = value")


def test_a_length_must_be_positive():
    text = "/// given: len(xs) = 0\nproc f(int xs[]) { xs[0] += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "length of 1 or more")


def test_a_length_must_name_a_parameter():
    text = "/// given: len(zz) = 4\nproc f(int xs[]) { xs[0] += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "not a parameter")


# ---------------------------------------------------------------------------
# building state a predicate cannot describe
# ---------------------------------------------------------------------------


STACKED = """
/// requires: n >= 1
proc fill(stack s, int n) {
    local int i = 0;
    from i == 0 do {
        local int v = n - i;
        push(v, s);
        delocal int v = 0;
    } loop {
        i += 1;
    } until i == n - 1;
    delocal int i = n - 1;
}

/// requires: n >= 1
/// setup: call fill(s, n);
proc drop_one(stack s, int n) {
    local int v = 0;
    pop(v, s);
    push(v, s);
    delocal int v = 0;
}
""" + MAIN


def test_setup_builds_the_state_the_procedure_needs():
    without = check(STACKED.replace("/// setup: call fill(s, n);\n", ""), "drop_one")
    is_true(not without.ok, "popping an empty stack should have been caught")
    with_setup = check(STACKED, "drop_one")
    is_true(with_setup.ok, with_setup.failure and with_setup.failure.describe())


def test_setup_is_undone_so_the_property_still_means_the_same_thing():
    m = module(STACKED)
    d = V.Driver(m, m.find_proc("drop_one"), 4)
    eq(calls(d.round), ["call __verify_setup", "call drop_one",
                        "uncall drop_one", "uncall __verify_setup"])
    eq(calls(d.once, "__verify_once"), ["call __verify_setup", "call drop_one"])


# ---------------------------------------------------------------------------
# what the search chooses to vary
# ---------------------------------------------------------------------------


COUNTER = """
int total;
int n;
proc main() {
    local int i = 0;
    from i == 0 do { total += i; } loop { i += 1; } until i == n;
    delocal int i = n;
}
"""


def test_a_procedure_with_no_arguments_is_run_once_from_zero():
    r = check(COUNTER, "main")
    eq(r.cases, 1)
    is_true(r.ok, r.failure and r.failure.describe())


def test_globals_are_only_varied_when_asked_for():
    m = module(COUNTER)
    main = m.find_proc("main")
    eq(V.Driver(m, main, 4).slots, [])
    varied = [name for name, _, _ in V.Driver(m, main, 4, True).slots]
    eq(sorted(varied), ["n", "total"])
    eq(check(COUNTER, "main").cases, 1)


def test_a_case_that_will_not_terminate_is_given_up_on_not_reported():
    """A count down to a target below zero never meets the loop's exit test."""
    text = """
/// given: n = -1, acc = 0
proc countdown(int n, int acc) {
    local int i = 0;
    from i == 0 do { acc += 1; } loop { i += 1; } until i == n;
    delocal int i = n;
}
""" + MAIN
    r = check(text, "countdown", cases=4, max_steps=400)
    is_true(r.ok, "a step limit is not a counterexample")
    eq(r.gave_up, 3)  # every property gave up on the one input there is


def test_generated_procedures_are_not_verified():
    m = module("int x; proc main() { embed (x ^= a) { var a = 2; } }")
    names = [d.name for d in V.verifiable(m)]
    eq(names, ["main"])


def test_asking_for_a_procedure_that_is_not_there():
    with raises(ReverieError, "no procedure named"):
        V.verify_module(module(GOOD), ["nope"])


# ---------------------------------------------------------------------------
# shrinking, in isolation
# ---------------------------------------------------------------------------


@case(0, [])
@case(1, [0])
@case(-1, [0])
@case(8, [0, 1, 4])
@case(-8, [0, -1, -4])
@case(3, [0, 1])
def test_smaller_walks_towards_zero(value, expected):
    eq(V.smaller(value), expected)


def test_shrinks_offers_the_whole_array_first():
    first = next(V.shrinks({"xs": [1, 2]}))
    eq(first, {"xs": [0, 0]})


def test_shrinks_leaves_frozen_names_alone():
    out = list(V.shrinks({"a": 5, "b": 5}, frozenset({"a"})))
    is_true(all(c["a"] == 5 for c in out), "a pinned value was shrunk")
    is_true(any(c["b"] != 5 for c in out), "nothing was shrunk at all")


def test_shrinking_gives_up_rather_than_running_forever():
    is_true(V.SHRINK_BUDGET > 0)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_the_report_counts_in_english():
    reports = [V.Report("f", "f(int x)", cases=1, steps=3)]
    text = V.render(reports, "t.rev")
    contains(text, "1 case ")
    contains(text, "1 procedure,")
    contains(text, "every procedure was reversible")


def test_the_report_shows_the_counterexample():
    r = check(BROKEN, "drain")
    text = V.render([r], "t.rev")
    contains(text, "FAILED")
    contains(text, "smallest input found: x = 1, y = 0")
    contains(text, "1 of 1 procedure failed")


def test_the_report_says_when_a_procedure_was_skipped():
    reports = [V.Report("f", "f(int x)", skipped="no reason")]
    contains(V.render(reports, "t.rev"), "skipped: no reason")


def test_the_report_says_when_nothing_could_be_varied():
    f = V.Failure("undo", "boom", {})
    text = V.render([V.Report("m", "main()", cases=1, failure=f)], "t.rev")
    contains(text, "with every global at zero")


def test_a_long_signature_is_truncated_not_wrapped():
    long = V.Report("f", "f(" + "int aaaaaaaaaa, " * 8 + "int z)", cases=1)
    for line in V.render([long], "t.rev").splitlines():
        is_true(len(line) < 100, f"line too long: {line!r}")


def test_values_print_as_they_were_generated():
    eq(V.show_value([1, 2, 3]), "[1, 2, 3]")
    eq(V.show_value(-4), "-4")


# ---------------------------------------------------------------------------
# the library and the examples, for real
# ---------------------------------------------------------------------------


def test_the_standard_library_states_domains_that_hold():
    for name in sorted(os.listdir(STDLIB)):
        if not name.endswith(".rev"):
            continue
        path = os.path.join(STDLIB, name)
        reports = V.verify_module(load_module(path), cases=6, length=8)
        for r in reports:
            is_true(r.ok, f"{name}: {r.proc}: "
                          f"{r.failure and r.failure.describe()}")
            is_true(not r.skipped, f"{name}: {r.proc}: {r.skipped}")


@slow
def test_every_example_is_reversible_on_every_input_tried():
    for name in sorted(os.listdir(EXAMPLES)):
        if not name.endswith(".rev"):
            continue
        path = os.path.join(EXAMPLES, name)
        reports = V.verify_module(load_module(path), cases=15, length=8)
        for r in reports:
            is_true(r.ok, f"{name}: {r.proc}: "
                          f"{r.failure and r.failure.describe()}")


# ---------------------------------------------------------------------------
# corners
# ---------------------------------------------------------------------------


def test_argument_globals_avoid_names_the_program_already_uses():
    eq(V._fresh("a", set()), "a")
    taken = {"a", "a_1"}
    eq(V._fresh("a", taken), "a_2")
    is_true("a_2" in taken, "the new name should be claimed")


def test_a_driver_works_around_a_collision():
    text = "int __arg_x;\nproc f(int x) { x += 1; }" + MAIN
    m = module(text)
    d = V.Driver(m, m.find_proc("f"), 4)
    is_true(d.argnames[0] != "__arg_x", "the driver reused an existing name")
    is_true(check(text, "f").ok)


def test_a_requires_that_traps_rejects_the_input_rather_than_the_program():
    """`requires: 1 / i` divides by zero when i is zero; that is a rejection."""
    text = """
/// requires: (1 / i) != 0
proc poke(int xs[], int i) {
    xs[0] += i;
}
""" + MAIN
    r = check(text, "poke", cases=6)
    is_true(r.ok, r.failure and r.failure.describe())
    is_true(r.cases > 0, "every input was rejected")


def test_shrinking_reaches_inside_an_array():
    """Zeroing the array does not preserve the failure, so elements shrink."""
    text = """
proc drain(int xs[]) {
    if xs[0] > 0 {
        xs[0] -= 1;
    } else {
        skip;
    } fi xs[0] > 0;
}
""" + MAIN
    r = check(text, "drain", cases=60, length=3)
    is_true(not r.ok, "the bad exit test was missed")
    eq(r.failure.inputs, {"xs": [1, 0, 0]})


def test_a_state_that_differs_without_a_trap_is_reported():
    """The oracle's own verdict, with the machine standing in for a broken one."""
    m = module(GOOD)
    d = V.Driver(m, m.find_proc("bump"), 4)
    real = V.state_equal
    V.state_equal = lambda a, b: (False, "mem[3]: 1 != 0")
    try:
        failure, _ = V.check_case(d, {d.argnames[1]: 1}, "undo",
                                  mem=4096, max_steps=1000, paranoid=False)
    finally:
        V.state_equal = real
    contains(failure.reason, "mem[3]")


@case(RecursionError, "ran out of stack")
@case(ZeroDivisionError, "the interpreter crashed: ZeroDivisionError")
def test_an_exception_escaping_the_machine_becomes_a_counterexample(exc, said):
    m = module(GOOD)
    d = V.Driver(m, m.find_proc("bump"), 4)

    def boom(a, b):
        raise exc("bang")

    real = V.state_equal
    V.state_equal = boom
    try:
        failure, _ = V.check_case(d, {}, "undo", mem=4096, max_steps=1000,
                                  paranoid=False)
    finally:
        V.state_equal = real
    contains(failure.reason, said)


def test_shrinking_stops_when_its_budget_runs_out():
    m = module(BROKEN)
    d = V.Driver(m, m.find_proc("drain"), 4)
    budget = V.SHRINK_BUDGET
    V.SHRINK_BUDGET = 1
    try:
        start = dict(zip(d.argnames, (9, 9)))
        f = V.shrink(d, V.Failure("undo", "boom", start),
                     mem=4096, max_steps=2000, paranoid=False)
    finally:
        V.SHRINK_BUDGET = budget
    is_true(f.shrinks <= 1, "the budget was ignored")


def test_shrinking_skips_a_candidate_that_will_not_terminate():
    """A candidate that runs away is not evidence either way."""
    text = """
proc drain(int x, int y) {
    if x > 0 {
        x -= 1;
    } else {
        local int i = 0;
        from i == 0 do { y += 1; } loop { i += 1; } until i == x;
        delocal int i = x;
    } fi x > 0;
}
""" + MAIN
    r = check(text, "drain", cases=40, max_steps=3000)
    is_true(not r.ok, "the bad exit test was missed")
    eq(r.failure.inputs["x"], 1)


def test_the_search_stops_early_if_the_domain_dries_up():
    m = module(GOOD)
    decl = m.find_proc("bump")
    seen = []
    real = V.Driver.satisfies

    def once(self, values, mem=0):
        seen.append(values)
        return len(seen) == 1

    V.Driver.satisfies = once
    try:
        r = V.verify_proc(m, decl, cases=9)
    finally:
        V.Driver.satisfies = real
    eq(r.cases, 1)
    eq(r.skipped, "")
    is_true(len(seen) > 1, "the search gave up without trying again")


def test_the_same_input_is_never_run_twice():
    text = """
/// given: x = 0
/// requires: k == 1 || k == 2
proc bump(int x, int k) {
    x += k;
}
""" + MAIN
    r = check(text, "bump", cases=30)
    eq(r.cases, 2, "there are only two inputs in the domain")


def test_a_pinned_stack_is_rejected():
    text = "/// given: s = 1\nproc f(stack s, int x) { push(x, s); }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "cannot pin a stack")


def test_a_length_only_makes_sense_for_an_array():
    text = "/// given: len(x) = 4\nproc f(int x) { x += 1; }" + MAIN
    r = check(text, "f", cases=2)
    contains(r.skipped, "is not an array")
