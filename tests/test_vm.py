"""Direct tests of the reversible machine, driven from hand-built RIR."""

from framework import case, contains, eq, is_true, raises
from support import build, g, roundtrip, run
from reverie import rir
from reverie.diagnostics import RuntimeFault
from reverie.ir import AbsA, Bin, Const, IndexA, Load, Un
from reverie.vm import GlobalInfo, Machine, state_equal

X, Y, Z = 0, 1, 2
GLOBALS = {
    "x": GlobalInfo("x", X),
    "y": GlobalInfo("y", Y),
    "z": GlobalInfo("z", Z),
}


def prog(body, **kw):
    return build(body, globals_=kw.pop("globals_", GLOBALS), **kw)


# ---------------------------------------------------------------------------
# updates
# ---------------------------------------------------------------------------


@case("+", 10, 3, 13)
@case("-", 10, 3, 7)
@case("^", 12, 10, 6)
@case("*", 10, 3, 30)
@case("/", 12, 3, 4)
@case("<<", 5, 3, 40)
@case(">>", 40, 3, 5)
def test_update_operators(op, start, operand, expected):
    p = prog(rir.RUpdate(op, AbsA(X, "x"), Const(operand)))
    m, fwd, ok, why = roundtrip(p, {"x": start})
    eq(fwd["x"], expected)
    is_true(ok, why)


def test_update_by_expression_of_another_cell():
    p = prog(rir.RUpdate("+", AbsA(X, "x"), Bin("*", g("y", Y), Const(3))))
    m, fwd, ok, why = roundtrip(p, {"x": 1, "y": 5})
    eq(fwd["x"], 16)
    is_true(ok, why)


def test_multiply_by_zero_is_refused():
    p = prog(rir.RUpdate("*", AbsA(X, "x"), Const(0)))
    with raises(RuntimeFault, "destroys information"):
        run(p, {"x": 7})


def test_inexact_division_is_refused():
    p = prog(rir.RUpdate("/", AbsA(X, "x"), Const(3)))
    with raises(RuntimeFault, "not exact"):
        run(p, {"x": 7})


def test_shift_that_would_drop_bits_is_refused():
    p = prog(rir.RUpdate(">>", AbsA(X, "x"), Const(2)))
    with raises(RuntimeFault, "discard"):
        run(p, {"x": 7})


def test_negative_numbers_survive_roundtrip():
    p = prog(
        rir.RSeq(
            [
                rir.RUpdate("-", AbsA(X, "x"), Const(100)),
                rir.RUpdate("*", AbsA(X, "x"), Const(-3)),
                rir.RUpdate("/", AbsA(X, "x"), Const(2)),
            ]
        )
    )
    m, fwd, ok, why = roundtrip(p, {"x": 8})
    eq(fwd["x"], 138)
    is_true(ok, why)


def test_swap_is_self_inverse():
    p = prog(rir.RSwap(AbsA(X, "x"), AbsA(Y, "y")))
    m, fwd, ok, why = roundtrip(p, {"x": 3, "y": 9})
    eq((fwd["x"], fwd["y"]), (9, 3))
    is_true(ok, why)


def test_unary_updates():
    p = prog(rir.RSeq([rir.RUnary("neg", AbsA(X, "x")), rir.RUnary("not", AbsA(Y, "y"))]))
    m, fwd, ok, why = roundtrip(p, {"x": 5, "y": 5})
    eq((fwd["x"], fwd["y"]), (-5, -6))
    is_true(ok, why)


def test_assert_traps_on_failure():
    p = prog(rir.RAssert(Bin("==", g("x", X), Const(1)), "x must be one"))
    with raises(RuntimeFault, "x must be one"):
        run(p, {"x": 2})


# ---------------------------------------------------------------------------
# conditionals
# ---------------------------------------------------------------------------


def cond_program():
    return prog(
        rir.RIf(
            Bin(">", g("x", X), Const(3)),
            rir.RUpdate("+", AbsA(Y, "y"), Const(100)),
            rir.RUpdate("+", AbsA(Y, "y"), Const(7)),
            Bin(">", g("y", Y), Const(50)),
        )
    )


@case(10, 100)
@case(0, 7)
def test_conditional_both_branches(x, y):
    m, fwd, ok, why = roundtrip(cond_program(), {"x": x})
    eq(fwd["y"], y)
    is_true(ok, why)


def test_conditional_exit_assertion_must_hold():
    # then-branch runs but leaves y small, so the exit assertion fails
    p = prog(
        rir.RIf(
            Bin(">", g("x", X), Const(3)),
            rir.RUpdate("+", AbsA(Y, "y"), Const(1)),
            rir.RSkip(),
            Bin(">", g("y", Y), Const(50)),
        )
    )
    with raises(RuntimeFault, "must hold after the then-branch"):
        run(p, {"x": 10})


def test_conditional_else_assertion_must_fail():
    p = prog(
        rir.RIf(
            Bin(">", g("x", X), Const(3)),
            rir.RSkip(),
            rir.RUpdate("+", AbsA(Y, "y"), Const(99)),
            Bin(">", g("y", Y), Const(50)),
        )
    )
    with raises(RuntimeFault, "must fail after the else-branch"):
        run(p, {"x": 0})


def test_empty_branches_still_pair_up():
    p = prog(
        rir.RIf(
            Bin("==", g("x", X), Const(0)),
            rir.RSeq([]),
            rir.RSeq([]),
            Bin("==", g("x", X), Const(0)),
        )
    )
    m, fwd, ok, why = roundtrip(p, {"x": 0})
    is_true(ok, why)
    m, fwd, ok, why = roundtrip(p, {"x": 5})
    is_true(ok, why)


# ---------------------------------------------------------------------------
# loops
# ---------------------------------------------------------------------------


def counter_loop(limit_addr=Y):
    """from x == 0 do skip loop x += 1 until x == n"""
    return prog(
        rir.RLoop(
            Bin("==", g("x", X), Const(0)),
            rir.RSkip(),
            rir.RUpdate("+", AbsA(X, "x"), Const(1)),
            Bin("==", g("x", X), Load(AbsA(limit_addr))),
        )
    )


@case(0)
@case(1)
@case(5)
@case(37)
def test_loop_counts_up(n):
    m, fwd, ok, why = roundtrip(counter_loop(), {"y": n})
    eq(fwd["x"], n)
    is_true(ok, why)


def test_loop_entry_assertion_rejects_dirty_entry():
    p = counter_loop()
    with raises(RuntimeFault, "must hold on"):
        run(p, {"x": 3, "y": 5})


def test_nested_loops():
    inner = rir.RLoop(
        Bin("==", g("y", Y), Const(0)),
        rir.RSkip(),
        rir.RUpdate("+", AbsA(Y, "y"), Const(1)),
        Bin("==", g("y", Y), Const(3)),
    )
    body = rir.RSeq(
        [
            inner,
            rir.RUpdate("+", AbsA(Z, "z"), g("y", Y)),
            rir.RUpdate("-", AbsA(Y, "y"), Const(3)),
        ]
    )
    p = prog(
        rir.RLoop(
            Bin("==", g("x", X), Const(0)),
            rir.RSkip(),
            rir.RSeq([body, rir.RUpdate("+", AbsA(X, "x"), Const(1))]),
            Bin("==", g("x", X), Const(4)),
        )
    )
    m, fwd, ok, why = roundtrip(p, {})
    eq(fwd["z"], 12)
    eq(fwd["x"], 4)
    is_true(ok, why)


# ---------------------------------------------------------------------------
# direction flipping
# ---------------------------------------------------------------------------


def test_direction_flip_is_free_and_exact():
    p = counter_loop()
    m = Machine(p, mem_size=64)
    m.set_globals({"y": 6})
    m.start_forward()
    for _ in range(9):
        m.step()
    mid = m.snapshot()
    m.reverse()
    for _ in range(4):
        m.step()
    m.reverse()
    for _ in range(4):
        m.step()
    ok, why = state_equal(mid, m.snapshot())
    is_true(ok, why)
    eq(m.pc, mid["pc"])


def test_full_reverse_from_the_middle_reaches_the_start():
    p = counter_loop()
    m = Machine(p, mem_size=64)
    m.set_globals({"y": 6})
    start = m.snapshot()
    m.start_forward()
    for _ in range(7):
        m.step()
    m.reverse()
    m.run()
    ok, why = state_equal(start, m.snapshot())
    is_true(ok, why)


# ---------------------------------------------------------------------------
# output is information too
# ---------------------------------------------------------------------------


def test_print_rewinds():
    p = prog(
        rir.RSeq(
            [
                rir.RUpdate("+", AbsA(X, "x"), Const(4)),
                rir.REmit(["x is ", g("x", X)]),
                rir.RUpdate("+", AbsA(X, "x"), Const(1)),
                rir.REmit(["x is ", g("x", X)]),
            ]
        )
    )
    m, fwd, ok, why = roundtrip(p, {})
    is_true(ok, why)
    m2 = run(p, {})
    eq(m2.output, ["x is 4", "x is 5"])


def test_unemit_is_the_inverse_of_emit():
    body = rir.RSeq([rir.REmit(["hello"]), rir.RUpdate("+", AbsA(X, "x"), Const(1))])
    fwd_prog = prog(body)
    inv_prog = prog(body.invert())
    m = run(fwd_prog, {})
    eq(m.output, ["hello"])
    m2 = Machine(inv_prog, mem_size=64)
    m2.output.append("hello")
    m2.set_globals({"x": 1})
    m2.start_forward().run()
    eq(m2.output, [])
    eq(m2.globals_dict()["x"], 0)
