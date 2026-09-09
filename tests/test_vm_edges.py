"""Machine bookkeeping: cleanliness checks, state loading, comparisons."""

from framework import case, contains, eq, is_true, raises
from support import build, run
from reverie import rir
from reverie.compiler import compile_text
from reverie.diagnostics import RuntimeFault
from reverie.ir import AbsA, Const
from reverie.vm import GlobalInfo, Machine, Program, Stats, state_equal

SRC = """int x;
int a[3];
stack s;
proc main() {
    x += 1;
    local int t = 0;
    t += 4;
    push(t, s);
    delocal int t = 0;
}
"""


def machine():
    return Machine(compile_text(SRC, "t.rev"), mem_size=256)


def test_globals_view_round_trips():
    m = machine()
    m.set_globals({"x": 5, "a": [1, 2, 3], "s": [7, 8]})
    eq(m.globals_dict(), {"x": 5, "a": [1, 2, 3], "s": [7, 8]})


def test_setting_an_unknown_global():
    with raises(KeyError, "no global named"):
        machine().set_globals({"nope": 1})


def test_an_array_that_is_too_long():
    with raises(ValueError, "holds 3 elements, got 5"):
        machine().set_globals({"a": [1, 2, 3, 4, 5]})


def test_check_clean_names_what_is_wrong():
    m = machine()
    m.history.append(1)
    with raises(RuntimeFault, "unreclaimed entries"):
        m.check_clean()

    m = machine()
    m.sp += 4
    with raises(RuntimeFault, "stack pointer is"):
        m.check_clean()

    m = machine()
    m.mem[m.program.n_globals + 2] = 9
    with raises(RuntimeFault, "scratch cell") as r:
        m.check_clean()
    contains(str(r.error), "holds 9")

    m = machine()
    m.start_forward()
    m.step()
    m.step()
    with raises(RuntimeFault, "frames still open"):
        m.check_clean()


def test_check_clean_passes_after_a_tidy_run():
    m = machine().start_forward().run()
    m.check_clean()
    eq(m.bits_erased, 0)


def test_repr_shows_the_direction():
    m = machine().start_forward()
    contains(repr(m), "->")
    m.reverse()
    contains(repr(m), "<-")


def test_state_equal_reports_the_first_difference():
    m = machine()
    a = m.snapshot()
    m.mem[2] = 99
    ok, why = state_equal(a, m.snapshot())
    is_true(not ok)
    contains(why, "mem[2]")


def test_state_equal_notices_every_component():
    m = machine()
    base = m.snapshot()
    for mutate, marker in (
        (lambda s: s["stacks"][0].append(1), "stacks"),
        (lambda s: s["output"].append("x"), "output"),
        (lambda s: s.__setitem__("line", "partial"), "open line"),
        (lambda s: s.__setitem__("history", [1]), "history"),
        (lambda s: s.__setitem__("fp", 9), "frame pointers"),
    ):
        other = m.snapshot()
        mutate(other)
        ok, why = state_equal(base, other)
        is_true(not ok, marker)
        contains(why, marker)


def test_state_equal_on_different_lengths():
    m = machine()
    a = m.snapshot()
    b = m.snapshot()
    b["mem"] = b["mem"][:-1]
    ok, why = state_equal(a, b)
    is_true(not ok)
    contains(why, "length")


def test_running_off_the_end_stops():
    prog = compile_text("proc main() { skip; }", "t.rev")
    m = Machine(prog, mem_size=64)
    m.pc = len(prog.code)
    m.dir = 1
    eq(m.step(), False)
    is_true(m.halted)


def test_stepping_a_halted_machine_does_nothing():
    m = machine().start_forward().run()
    eq(m.step(), False)


def test_run_honours_its_limit():
    m = machine().start_forward()
    m.run(limit=3)
    eq(m.position, 3)


def test_the_halt_boundary_is_found():
    prog = compile_text("proc main() { skip; }", "t.rev")
    m = Machine(prog, mem_size=64)
    eq(m._halt_boundary(), 1)
    stripped = Program(code=[c for c in prog.code if c.name != "halt"])
    stripped.procs = prog.procs
    m2 = Machine(stripped.finalize(), mem_size=64)
    eq(m2._halt_boundary(), len(stripped.code))


def test_restore_puts_everything_back():
    m = machine().start_forward()
    m.run(limit=4)
    snap = m.snapshot()
    m.run()
    m.restore(snap)
    ok, why = state_equal(snap, m.snapshot())
    is_true(ok, why)
    eq(m.pc, snap["pc"])
    eq(m.position, snap["position"])


def test_stats_report():
    m = machine().start_forward().run()
    text = m.stats.merge_report()
    for key in ("steps=", "calls=", "tape_peak=", "tape_bits="):
        contains(text, key)


def test_disassembly_and_layout():
    prog = compile_text(SRC, "t.rev")
    text = prog.disassemble()
    contains(text, "call main()")
    layout = prog.globals_layout()
    contains(layout, "a")
    contains(layout, "[3]")
    contains(layout, "stack")


def test_stack_handles_are_installed_at_startup():
    prog = compile_text(SRC, "t.rev")
    m = Machine(prog, mem_size=64)
    eq(m.mem[prog.globals["s"].addr], 1)
    eq(len(m.stacks), 1)
