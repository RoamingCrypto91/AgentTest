"""Compilation: storage layout, lowering, and end-to-end behaviour."""

from framework import case, contains, eq, is_true, raises
from support import roundtrip
from reverie.compiler import compile_text
from reverie.diagnostics import RuntimeFault
from reverie.vm import Machine, state_equal


def build(text):
    return compile_text(text, "t.rev")


def run(text, initial=None, mem_size=512):
    prog = build(text)
    m = Machine(prog, mem_size=mem_size)
    if initial:
        m.set_globals(initial)
    m.start_forward().run()
    return m


def both_ways(text, initial=None, mem_size=512):
    prog = build(text)
    m = Machine(prog, mem_size=mem_size)
    if initial:
        m.set_globals(initial)
    before = m.snapshot()
    m.start_forward().run()
    forward = m.globals_dict()
    out = list(m.output)
    m.check_clean()
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, why)
    return forward, out


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


def test_program_starts_with_a_call_and_a_halt():
    prog = build("proc main() { skip; }")
    eq(prog.code[0].render(), "call main()")
    eq(prog.code[1].name, "halt")


def test_frame_size_is_the_high_water_mark():
    prog = build(
        "int x; proc main() { local int a = 1; { local int b = 2; x += b; "
        "delocal int b = 2; } local int c = 3; x += a + c; delocal int c = 3; "
        "delocal int a = 1; }"
    )
    eq(prog.procs["main"].frame_size, 2)


def test_globals_table_survives_compilation():
    prog = build("int x; int a[3]; stack s; proc main() { skip; }")
    eq([(g.name, g.addr, g.size, g.kind) for g in prog.globals.values()],
       [("x", 0, 1, "int"), ("a", 1, 3, "array"), ("s", 4, 1, "stack")])
    eq(prog.n_stacks, 1)


def test_procedures_are_recorded_with_their_docs():
    prog = build("/// adds\nproc helper(int a, int b) { a += b; }\nproc main() { skip; }")
    info = prog.procs["helper"]
    eq(info.params, ("a", "b"))
    eq(info.doc, "adds")


# ---------------------------------------------------------------------------
# behaviour
# ---------------------------------------------------------------------------


@case("x += 3 * 4;", {}, 12)
@case("x += 2 ** 10;", {}, 1024)
@case("x += 7 / 2;", {}, 3)
@case("x += -7 / 2;", {}, -3)
@case("x += 7 % 3;", {}, 1)
@case("x += -7 % 3;", {}, -1)
@case("x += min(3, 5) + max(3, 5);", {}, 8)
@case("x += abs(-9) + sign(-4);", {}, 8)
@case("x += 1 < 2;", {}, 1)
@case("x += 5 & 3;", {}, 1)
@case("x += 5 | 3;", {}, 7)
@case("x += 5 ^ 3;", {}, 6)
@case("x += ~5;", {}, -6)
@case("x += !0 + !7;", {}, 1)
@case("x += 1 << 5;", {}, 32)
@case("x += 1024 >> 3;", {}, 128)
@case("x += (0 && 1) + (1 || 0);", {}, 1)
def test_expression_evaluation(stmt, initial, expected):
    forward, _ = both_ways(f"int x; proc main() {{ {stmt} }}", initial)
    eq(forward["x"], expected)


def test_short_circuit_avoids_the_trap():
    # the right operand would divide by zero if it were evaluated
    forward, _ = both_ways("int x; int d; proc main() { x += (d != 0) && (10 / d > 1); }")
    eq(forward["x"], 0)


def test_division_by_zero_traps():
    with raises(RuntimeFault, "division by zero"):
        run("int x; int d; proc main() { x += 1 / d; }")


def test_array_bounds_are_checked():
    with raises(RuntimeFault, "out of bounds"):
        run("int a[3]; int i; proc main() { i += 5; a[i] += 1; }")
    with raises(RuntimeFault, "out of bounds"):
        run("int a[3]; int i; proc main() { i -= 1; a[i] += 1; }")


def test_nested_procedure_calls():
    forward, _ = both_ways(
        "int x; int y;\n"
        "proc inner(int p) { p += 1; }\n"
        "proc outer(int q) { call inner(q); call inner(q); }\n"
        "proc main() { call outer(x); call outer(y); uncall outer(y); }"
    )
    eq((forward["x"], forward["y"]), (2, 0))


def test_recursion():
    forward, _ = both_ways(
        "int n; int acc;\n"
        "proc down(int k, int a) {\n"
        "  if k > 0 { a += k; k -= 1; call down(k, a); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n, acc); }",
        {"n": 5},
    )
    eq(forward["acc"], 15)
    eq(forward["n"], 5)


def test_uncall_undoes_a_call():
    forward, _ = both_ways(
        "int x;\nproc bump(int p) { p += 41; p *= 2; }\n"
        "proc main() { x += 1; call bump(x); uncall bump(x); }"
    )
    eq(forward["x"], 1)


def test_undo_block_is_inversion_at_the_statement_level():
    forward, _ = both_ways(
        "int x; int y; proc main() { x += 5; y += 2; y *= 3; "
        "undo { x += 5; y *= 3; } }"
    )
    eq((forward["x"], forward["y"]), (0, 2))


def test_stacks():
    forward, _ = both_ways(
        "int x; stack s;\n"
        "proc main() {\n"
        "  local int t = 7; push(t, s); delocal int t = 0;\n"
        "  local int u = 9; push(u, s); delocal int u = 0;\n"
        "  x += size(s) + top(s);\n"
        "}"
    )
    eq(forward["x"], 11)
    eq(forward["s"], [7, 9])


def test_pop_requires_a_zeroed_target():
    with raises(RuntimeFault, "requires a zeroed target"):
        run("int x; stack s; proc main() { local int t = 1; push(t, s); "
            "delocal int t = 0; x += 5; pop(x, s); }")


def test_top_of_an_empty_stack_traps():
    with raises(RuntimeFault, "empty stack"):
        run("int x; stack s; proc main() { x += top(s); }")


def test_local_cell_must_be_zero_before_use():
    # frames are checked on exit, so a leak is caught there
    prog = build("int x; proc leak(int p) { p += 1; } proc main() { call leak(x); }")
    m = Machine(prog, mem_size=64)
    m.start_forward().run()
    m.check_clean()


def test_leaking_a_frame_is_caught():
    from reverie.rir import RSeq, RUpdate
    from reverie.ir import AbsA, Const
    from support import build as build_prog

    prog = build_prog(RSeq([RUpdate("+", AbsA(0, "leak"), Const(1))]), n_globals=0,
                      frame_size=1)
    m = Machine(prog, mem_size=32)
    with raises(RuntimeFault, "leaks"):
        m.start_forward().run()


def test_print_and_write_build_lines():
    forward, out = both_ways(
        'int x; proc main() { x += 2; write "a"; write x; print "b"; print "c"; }'
    )
    eq(out, ["a2b", "c"])


def test_open_line_is_reported_as_unclean():
    prog = build('proc main() { write "oops"; }')
    m = Machine(prog, mem_size=32).start_forward().run()
    with raises(RuntimeFault, "never finished"):
        m.check_clean()


def test_dynamic_array_parameters():
    forward, _ = both_ways(
        "int a[3]; int b[5]; int total;\n"
        "proc add_all(int xs[], int acc) {\n"
        "  local int i = 0;\n"
        "  from i == 0 do { acc += xs[i]; } loop { i += 1; } until i == len(xs) - 1;\n"
        "  delocal int i = len(xs) - 1;\n"
        "}\n"
        "proc main() { call add_all(a, total); call add_all(b, total); }",
        {"a": [1, 2, 3], "b": [10, 20, 30, 40, 50]},
    )
    eq(forward["total"], 156)


def test_bounds_use_the_length_actually_passed():
    with raises(RuntimeFault, "out of bounds"):
        run(
            "int a[2];\n"
            "proc touch(int xs[]) { xs[4] += 1; }\n"
            "proc main() { call touch(a); }"
        )


def test_constant_arguments_are_released():
    forward, _ = both_ways(
        "int a[4]; int total;\n"
        "proc add_from(int xs[], int acc, int lo) {\n"
        "  local int i = lo;\n"
        "  from i == lo do { acc += xs[i]; } loop { i += 1; } until i == len(xs) - 1;\n"
        "  delocal int i = len(xs) - 1;\n"
        "}\n"
        "proc main() { call add_from(a, total, 1); }",
        {"a": [1, 2, 3, 4]},
    )
    eq(forward["total"], 9)


def test_swap_moves_rather_than_copies():
    forward, _ = both_ways(
        "int x; int y; proc main() { x += 5; x <=> y; }"
    )
    eq((forward["x"], forward["y"]), (0, 5))


def test_assert_statement():
    both_ways("int x; proc main() { x += 3; assert x == 3; }")
    with raises(RuntimeFault, "assertion failed"):
        run("int x; proc main() { assert x == 3; }")


def test_step_limit_stops_a_runaway():
    # x runs away from the exit test, and the entry assertion stays satisfied
    prog = build("int x; proc main() { from x == 0 loop { x += 1; } until x == -1; }")
    m = Machine(prog, mem_size=64, max_steps=5000)
    with raises(RuntimeFault, "step limit"):
        m.start_forward().run()


def test_a_loop_that_re_enters_dirty_is_caught():
    prog = build("int x; proc main() { from x == 0 loop { x += 0; } until x == 1; }")
    m = Machine(prog, mem_size=64, max_steps=5000)
    with raises(RuntimeFault, "must fail on every arrival"):
        m.start_forward().run()
