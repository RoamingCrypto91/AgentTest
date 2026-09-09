"""Debugger paths for empty states and bad input."""

import io

from framework import case, contains, eq, is_true
from reverie.compiler import compile_text
from reverie.debugger import Debugger
from reverie.diagnostics import Source

BARE = "proc main() {\n    skip;\n}\n"
WITH_STATE = """int x;
stack s;
proc main() {
    x += 1;
    call helper(x);
}
proc helper(int p) {
    local int t = 0;
    t += p;
    p ^= 3;
    delocal int t = p ^ 3;
}
"""


def drive(commands, src=WITH_STATE, source=True):
    prog = compile_text(src, "d.rev")
    out = io.StringIO()
    dbg = Debugger(
        prog, Source("d.rev", src) if source else None, {}, mem_size=512,
        out=out, color=False,
    )
    for c in commands:
        dbg.execute(c)
    return dbg, out.getvalue()


def test_a_program_with_no_globals():
    dbg, text = drive(["globals"], BARE)
    contains(text, "(no globals)")


def test_no_active_frame():
    dbg, text = drive(["locals", "where"], BARE)
    contains(text, "(no active frame)")
    contains(text, "(no frames)")


def test_locals_are_listed_inside_a_frame():
    dbg, text = drive(["break 10", "run", "locals", "where", "print p"])
    contains(text, "frame for helper")
    contains(text, "locals:")
    contains(text, "helper")
    contains(text, "parameter")


def test_a_procedure_breakpoint_stops_at_its_marker():
    dbg, text = drive(["break proc helper", "run"])
    contains(text, "breakpoint")
    contains(dbg.current().render(), "helper")


def test_no_output_yet():
    dbg, text = drive(["out"], BARE)
    contains(text, "(no output yet)")


def test_no_breakpoints_and_no_watches():
    dbg, text = drive(["break", "watch"])
    contains(text, "(no breakpoints)")
    contains(text, "(nothing watched)")


def test_deleting_a_breakpoint_that_is_not_there():
    dbg, text = drive(["delete 4"])
    contains(text, "no such breakpoint")


def test_commands_that_need_an_argument():
    dbg, text = drive(["print", "mem"])
    contains(text, "print what?")
    contains(text, "mem <start> [end]")


def test_a_number_that_is_not_a_number():
    dbg, text = drive(["step forwards"])
    contains(text, "expected a number")


def test_unbalanced_quotes_are_reported():
    dbg, text = drive(['break "'])
    contains(text, "quotation")


def test_listing_without_a_source_falls_back_to_disassembly():
    dbg, text = drive(["list"], source=False)
    contains(text, "call main()")


def test_listing_an_instruction_with_no_source_position():
    dbg, text = drive(["list"], BARE)
    is_true(text.strip() != "")


def test_stepping_past_the_end_reports_the_edge():
    dbg, text = drive(["run", "step"])
    contains(text, "reached halt")


def test_stepping_back_past_the_start_reports_the_edge():
    dbg, text = drive(["back 5"])
    contains(text, "beginning of time")


def test_running_backwards_off_the_start():
    dbg, text = drive(["rewind"])
    contains(text, "beginning of time")


def test_breakpoints_on_a_pc():
    dbg, text = drive(["break pc 4", "run"])
    contains(text, "breakpoint")
    eq(dbg.machine.pc, 4)


def test_watching_a_variable_that_does_not_exist_is_harmless():
    dbg, text = drive(["watch nope", "step 4"])
    is_true("watch nope" not in text.split("watching")[1])


def test_memory_range():
    dbg, text = drive(["mem 0 3"])
    contains(text, "@0")
    contains(text, "@2")
