"""The time-travel debugger."""

import io
import os

from framework import case, contains, eq, is_true, raises
from reverie.compiler import compile_text
from reverie.debugger import Debugger
from reverie.diagnostics import Source
from reverie.vm import Machine, state_equal

SRC = """int x;
int y;
proc main() {
    from x == 0 loop { x += 1; } until x == 5;
    call twice(y, x);
    print "y is ", y;
}
proc twice(int a, int b) {
    a += b * 2;
}
"""


def make(src=SRC, initial=None):
    prog = compile_text(src, "d.rev")
    out = io.StringIO()
    dbg = Debugger(prog, Source("d.rev", src), initial or {}, mem_size=1024,
                   out=out, color=False)
    return dbg, out


def drive(commands, src=SRC, initial=None):
    dbg, out = make(src, initial)
    for c in commands:
        dbg.execute(c)
    return dbg, out.getvalue()


def test_stepping_and_position():
    dbg, _ = drive(["step 4"])
    eq(dbg.machine.position, 4)
    dbg.execute("back 2")
    eq(dbg.machine.position, 2)


def test_running_reaches_halt():
    dbg, text = drive(["run"])
    is_true(dbg.machine.halted)
    contains(text, "reached halt")
    eq(dbg.machine.globals_dict()["x"], 5)


def test_goto_is_the_same_as_stepping_there():
    prog = compile_text(SRC, "d.rev")
    walked = Machine(prog, mem_size=1024).start_forward()
    for _ in range(17):
        walked.step()

    dbg, _ = make()
    dbg.execute("goto 17")
    ok, why = state_equal(walked.snapshot(), dbg.machine.snapshot())
    is_true(ok, why)


def test_goto_backwards_from_the_end():
    dbg, _ = drive(["run"])
    end = dbg.machine.position
    dbg.execute("goto 3")
    eq(dbg.machine.position, 3)
    eq(dbg.machine.globals_dict()["x"], 0)
    dbg.execute(f"goto {end}")
    eq(dbg.machine.globals_dict()["x"], 5)


def test_goto_zero_reaches_the_start():
    prog = compile_text(SRC, "d.rev")
    start = Machine(prog, mem_size=1024).snapshot()
    dbg, _ = drive(["run", "goto 0"])
    eq(dbg.machine.position, 0)
    ok, why = state_equal(start, dbg.machine.snapshot())
    is_true(ok, why)


def test_rewind_runs_to_the_beginning():
    prog = compile_text(SRC, "d.rev")
    start = Machine(prog, mem_size=1024).snapshot()
    dbg, text = drive(["run", "rewind"])
    eq(dbg.machine.position, 0)
    ok, why = state_equal(start, dbg.machine.snapshot())
    is_true(ok, why)
    contains(text, "beginning of time")


def test_reverse_flips_the_arrow():
    dbg, text = drive(["step 6", "reverse"])
    eq(dbg.machine.arrow, -1)
    contains(text, "backwards")
    dbg.execute("step 3")
    eq(dbg.machine.position, 3)


def test_breakpoint_on_a_line():
    dbg, text = drive(["break 5", "run"])
    contains(text, "breakpoint")
    eq(dbg.current_line(), 5)


def test_breakpoint_on_a_procedure():
    dbg, text = drive(["break proc twice", "run"])
    contains(text, "breakpoint")
    is_true(any(f.proc.name == "twice" for f in dbg.machine.frames)
            or dbg.current().render().endswith("twice"))


def test_breakpoint_listing_and_deletion():
    dbg, text = drive(["break 4", "break pc 3", "break", "delete 0", "break"])
    contains(text, "('line', 4)")
    contains(text, "('pc', 3)")
    contains(text, "removed")


def test_watch_reports_changes_in_both_directions():
    dbg, text = drive(["watch x", "step 12", "back 6"])
    contains(text, "watch x: 0 -> 1")
    contains(text, "<-")


def test_print_a_global():
    dbg, text = drive(["step 12", "print x"])
    contains(text, "x = ")
    contains(text, "global @0")


def test_print_an_unknown_name_reports_it():
    dbg, text = drive(["print nope"])
    contains(text, "no variable named")


def test_print_a_parameter_inside_a_frame():
    dbg, text = drive(["break proc twice", "run", "print a", "locals", "where"])
    contains(text, "parameter")
    contains(text, "frame for twice")
    contains(text, "twice")


def test_globals_listing():
    dbg, text = drive(["run", "globals"])
    contains(text, "x = 5")
    contains(text, "y = 10")


def test_source_listing_marks_the_current_line():
    dbg, text = drive(["step 4", "list 2"])
    contains(text, ">")
    contains(text, "from x == 0")


def test_disassembly():
    dbg, text = drive(["step 2", "disasm 3"])
    contains(text, "upd")


def test_memory_and_tape_and_output():
    dbg, text = drive(["run", "mem 0 2", "tape", "out", "stats"])
    contains(text, "@0")
    contains(text, "entries")
    contains(text, "y is 10")
    contains(text, "bits erased  0")


def test_reset_returns_to_the_start():
    prog = compile_text(SRC, "d.rev")
    start = Machine(prog, mem_size=1024).snapshot()
    dbg, _ = drive(["run", "reset"])
    ok, why = state_equal(start, dbg.machine.snapshot())
    is_true(ok, why)


def test_unknown_command():
    dbg, text = drive(["frobnicate"])
    contains(text, "unknown command")


def test_help_and_quit():
    dbg, text = drive(["help"])
    contains(text, "time-travel")
    dbg.execute("quit")
    eq(dbg.running, False)


def test_a_trap_is_reported_not_raised():
    src = "int x;\nproc main() {\n  assert x == 1;\n}\n"
    dbg, text = drive(["run"], src)
    contains(text, "assertion failed")


def test_bare_enter_steps_once():
    dbg, _ = drive([""])
    eq(dbg.machine.position, 1)


def test_repl_reads_until_quit():
    dbg, out = make()
    script = iter(["step 2", "globals", "quit"])
    dbg.repl(lambda prompt: next(script))
    contains(out.getvalue(), "reverie debugger")
    contains(out.getvalue(), "x = 0")


def test_repl_stops_at_end_of_input():
    dbg, out = make()

    def reader(prompt):
        raise EOFError

    dbg.repl(reader)
    is_true(True)


def test_goto_beyond_the_end_stops_gracefully():
    dbg, text = drive(["goto 100000"])
    contains(text, "edge of time")


def test_negative_goto_is_refused():
    dbg, text = drive(["goto -1"])
    contains(text, "logical time starts at 0")
