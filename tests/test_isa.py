"""The instruction set: what each instruction refuses to do.

A reversible machine is unusually fault-prone by design.  Assertions that
would be dead code in a conventional VM -- a cell must be zero before it is
allocated, a division must be exact -- are load-bearing here, because
violating one would mean erasing something.  Every one of them is checked.
"""

from framework import case, contains, eq, is_true, raises
from support import build, roundtrip, run
from reverie import rir
from reverie.diagnostics import RuntimeFault
from reverie.ir import AbsA, Bin, Const, IndexA, Load, LocalA
from reverie.isa import (
    UPDATE_INVERSE,
    Assert,
    Call,
    ElseBegin,
    ElseEnd,
    Emit,
    Halt,
    Instr,
    LocalAlloc,
    LocalFree,
    Nop,
    ProcEntry,
    ProcExit,
    StackPop,
    StackPush,
    Swap,
    UnaryUpdate,
    Unemit,
    Update,
    INSTRUCTIONS,
)
from reverie.vm import GlobalInfo, Machine

X, Y = 0, 1
GLOBALS = {"x": GlobalInfo("x", X), "y": GlobalInfo("y", Y)}


def prog(body, **kw):
    return build(body, globals_=GLOBALS, **kw)


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_every_instruction_has_a_unique_mnemonic():
    eq(len(INSTRUCTIONS), 30)
    for name, cls in INSTRUCTIONS.items():
        eq(cls.name, name)


def test_update_operators_pair_up():
    for op, inv in UPDATE_INVERSE.items():
        eq(UPDATE_INVERSE[inv], op, f"{op} and {inv} must be mutual inverses")


def test_non_invertible_operators_are_refused():
    with raises(ValueError, "non-invertible update operator"):
        Update("%", AbsA(0), Const(1))
    with raises(ValueError, "unknown in-place unary"):
        UnaryUpdate("square", AbsA(0))


def test_rendering():
    eq(Update("+", AbsA(0, "x"), Const(3)).render(), "upd @x += 3")
    eq(UnaryUpdate("neg", AbsA(0, "x")).render(), "un neg @x")
    eq(Swap(AbsA(0, "x"), AbsA(1, "y")).render(), "swap @x <=> @y")
    eq(Assert(Bin(">", Load(AbsA(0, "x")), Const(0))).render(), "assert (@x > 0)")
    eq(Nop().render(), "nop")
    eq(Halt().render(), "halt")
    eq(Call("f", [AbsA(0, "x")]).render(), "call f(@x)")
    eq(Call("f", [AbsA(0, "x")], True).render(), "uncall f(@x)")
    eq(ProcEntry("f").render(), "entry f")
    eq(ProcExit("f").render(), "exit f")
    eq(LocalAlloc(2, None, 5, "buf").render(), "local %2[5]")
    eq(LocalFree(2, Const(0), 1, "t").render(), "delocal %2 = 0")
    eq(Emit(["a=", Load(AbsA(0, "x"))]).render(), "emit 'a=', @x")
    eq(Emit(["a"], newline=False).render(), "emit -nl 'a'")
    eq(Unemit(["a"]).render(), "unemit 'a'")
    eq(StackPush(AbsA(0, "x"), AbsA(1, "s")).render(), "push @x, @s")
    eq(StackPop(AbsA(0, "x"), AbsA(1, "s")).render(), "pop @x, @s")
    contains(repr(Nop()), "nop")


def test_the_abstract_base_refuses_to_run():
    base = Instr()
    with raises(NotImplementedError):
        base.forward(None)
    with raises(NotImplementedError):
        base.backward(None)


# ---------------------------------------------------------------------------
# updates that would erase
# ---------------------------------------------------------------------------


@case("*", 0, "destroys information")
@case("/", 0, "`/=` by zero")
@case("<<", -1, "negative shift")
@case("<<", 1 << 21, "too large")
@case(">>", -1, "negative shift")
def test_updates_that_would_erase_trap(op, operand, message):
    p = prog(rir.RUpdate(op, AbsA(X, "x"), Const(operand)))
    with raises(RuntimeFault, message):
        run(p, {"x": 7})


def test_inexact_division_names_the_numbers():
    p = prog(rir.RUpdate("/", AbsA(X, "x"), Const(3)))
    with raises(RuntimeFault, "not exact") as r:
        run(p, {"x": 7})
    contains(str(r.error), "7 is not divisible by 3")


def test_shifting_out_set_bits_says_how_many():
    p = prog(rir.RUpdate(">>", AbsA(X, "x"), Const(3)))
    with raises(RuntimeFault, "discard 3 nonzero low bits"):
        run(p, {"x": 9})


def test_shifting_out_zero_bits_is_fine():
    p = prog(rir.RUpdate(">>", AbsA(X, "x"), Const(3)))
    m, fwd, ok, why = roundtrip(p, {"x": 8})
    eq(fwd["x"], 1)
    is_true(ok, why)


# ---------------------------------------------------------------------------
# local storage
# ---------------------------------------------------------------------------


def test_local_requires_a_zeroed_cell():
    body = rir.RSeq([
        rir.RUpdate("+", LocalA(0, "t"), Const(5)),
        rir.RLocal(0, Const(1), 1, "t"),
        rir.RDelocal(0, Const(1), 1, "t"),
        rir.RUpdate("-", LocalA(0, "t"), Const(5)),
    ])
    with raises(RuntimeFault, "requires a zeroed cell"):
        run(prog(body, frame_size=2), {})


def test_delocal_reports_the_mismatch_and_why_it_matters():
    body = rir.RSeq([
        rir.RLocal(0, Const(1), 1, "t"),
        rir.RUpdate("+", LocalA(0, "t"), Const(4)),
        rir.RDelocal(0, Const(1), 1, "t"),
    ])
    with raises(RuntimeFault, "expected 1 but the cell holds 5") as r:
        run(prog(body, frame_size=2), {})
    contains(" ".join(r.error.notes), "without erasing information")


def test_local_arrays_must_be_zeroed_before_release():
    body = rir.RSeq([
        rir.RLocal(0, None, 3, "buf"),
        rir.RUpdate("+", LocalA(1, "buf"), Const(7)),
        rir.RDelocal(0, None, 3, "buf"),
    ])
    with raises(RuntimeFault, "element 1 holds 7"):
        run(prog(body, frame_size=4), {})


def test_a_leaked_frame_is_caught_on_the_way_out():
    body = rir.RSeq([rir.RUpdate("+", LocalA(0, "leak"), Const(1))])
    with raises(RuntimeFault, "leaks") as r:
        run(prog(body, frame_size=1), {})
    contains(" ".join(r.error.notes), "delocal")


# ---------------------------------------------------------------------------
# stacks
# ---------------------------------------------------------------------------


def stack_prog(body):
    g = dict(GLOBALS)
    g["s"] = GlobalInfo("s", 2, kind="stack", stack_id=1)
    p = build(body, globals_=g, frame_size=2)
    p.n_stacks = 1
    return p


def test_popping_an_empty_stack():
    p = stack_prog(rir.RPop(AbsA(X, "x"), AbsA(2, "s")))
    with raises(RuntimeFault, "`pop` from an empty stack"):
        run(p, {})


def test_popping_into_a_dirty_cell():
    p = stack_prog(rir.RSeq([
        rir.RUpdate("+", AbsA(Y, "y"), Const(1)),
        rir.RPush(AbsA(Y, "y"), AbsA(2, "s")),
        rir.RUpdate("+", AbsA(X, "x"), Const(3)),
        rir.RPop(AbsA(X, "x"), AbsA(2, "s")),
    ]))
    with raises(RuntimeFault, "requires a zeroed target"):
        run(p, {})


def test_a_cell_that_is_not_a_stack_handle():
    p = stack_prog(rir.RPush(AbsA(X, "x"), AbsA(Y, "y")))
    with raises(RuntimeFault, "does not hold a stack handle"):
        run(p, {})


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def test_unprinting_an_empty_log():
    p = prog(rir.RUnemit(["hello"]))
    with raises(RuntimeFault, "the output log is empty"):
        run(p, {})


def test_unprinting_with_a_line_still_open():
    p = prog(rir.RSeq([rir.REmit(["abc"], None, False), rir.RUnemit(["x"])]))
    with raises(RuntimeFault, "a partial line is still open"):
        run(p, {})


def test_unprinting_the_wrong_line():
    p = prog(rir.RUnemit(["hello"]))
    m = Machine(p, mem_size=64)
    m.output.append("goodbye")
    with raises(RuntimeFault, "un-print mismatch"):
        m.start_forward().run()


def test_unwriting_the_wrong_text():
    p = prog(rir.RUnemit(["hello"], None, False))
    m = Machine(p, mem_size=64)
    m.line = "goodbye"
    with raises(RuntimeFault, "un-write mismatch"):
        m.start_forward().run()


# ---------------------------------------------------------------------------
# control flow that cannot be entered the wrong way
# ---------------------------------------------------------------------------


def conditional(entry, exit_):
    return prog(
        rir.RIf(entry, rir.RUpdate("+", AbsA(Y, "y"), Const(1)),
                rir.RUpdate("-", AbsA(Y, "y"), Const(1)), exit_)
    )


def test_reverse_entering_a_branch_checks_the_entry_test():
    # y is left at 1 with x = 0, so reversing takes the then-branch and finds
    # the entry test false
    p = conditional(Bin(">", Load(AbsA(X, "x")), Const(0)),
                    Bin(">", Load(AbsA(Y, "y")), Const(0)))
    m = Machine(p, mem_size=64)
    m.set_globals({"x": 5, "y": 0})
    m.start_forward().run()
    m.set_globals({"x": 0})
    m.start_backward()
    with raises(RuntimeFault, "reverse-entering the then-branch"):
        m.run()


def test_reverse_entering_the_else_branch_checks_the_entry_test():
    p = conditional(Bin(">", Load(AbsA(X, "x")), Const(0)),
                    Bin(">", Load(AbsA(Y, "y")), Const(0)))
    m = Machine(p, mem_size=64)
    m.set_globals({"x": 0, "y": 0})
    m.start_forward().run()
    m.set_globals({"x": 5})
    m.start_backward()
    with raises(RuntimeFault, "reverse-entering the else-branch"):
        m.run()


def test_markers_that_are_unreachable_say_so():
    m = Machine(prog(rir.RSkip()), mem_size=32)
    for ins, message in (
        (ElseBegin(Const(1)), "fell into else_begin"),
        (ProcEntry("f"), "entry marker"),
    ):
        ins.at = 0
        with raises(RuntimeFault, message):
            ins.forward(m)
    for ins, message in (
        (ElseEnd(Const(1)), "fell into else_end"),
        (ProcExit("f"), "exit marker"),
        (Halt(), "cannot step backwards through `halt`"),
    ):
        ins.at = 0
        with raises(RuntimeFault, message):
            ins.backward(m)


def test_calling_an_unknown_procedure():
    b = prog(rir.RCall("nowhere", []))
    with raises(RuntimeFault, "unknown procedure"):
        run(b, {})


def test_calling_with_the_wrong_number_of_arguments():
    p = prog(rir.RCall("main", [AbsA(X, "x")]))
    with raises(RuntimeFault, "expects 0 arguments, got 1"):
        run(p, {})


def test_returning_with_no_frame():
    m = Machine(prog(rir.RSkip()), mem_size=32)
    ins = ProcExit("f")
    ins.at = 0
    with raises(RuntimeFault, "return with no active frame"):
        ins.forward(m)


def test_out_of_memory_is_reported_not_crashed():
    src = (
        "int n;\n"
        "proc down(int k) {\n"
        "  if k > 0 { k -= 1; call down(k); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n); }"
    )
    from reverie.compiler import compile_text

    p = compile_text(src, "deep.rev")
    p.procs["down"].frame_size = 64
    m = Machine(p, mem_size=256, max_steps=100000)
    m.set_globals({"n": 50})
    with raises(RuntimeFault, "out of memory"):
        m.start_forward().run()


def test_assert_carries_its_message_in_both_directions():
    p = prog(rir.RAssert(Bin("==", Load(AbsA(X, "x")), Const(1)), "x must be one"))
    with raises(RuntimeFault, "x must be one"):
        run(p, {"x": 0})
    m = Machine(p, mem_size=64)
    m.set_globals({"x": 0})
    m.start_backward()
    with raises(RuntimeFault, "x must be one"):
        m.run()


# ---------------------------------------------------------------------------
# integrity checks on the classical instructions
#
# These guard states that valid programs never reach, which makes them
# invisible to end-to-end testing -- and exactly the kind of check that rots.
# They are driven directly here.
# ---------------------------------------------------------------------------


def bare_machine(frame=4):
    """A machine with one open frame, ready to run a single instruction."""
    from reverie.vm import ProcInfo

    p = prog(rir.RSkip(), frame_size=frame)
    m = Machine(p, mem_size=64)
    info = p.procs["main"]
    m.enter_frame(info, [], [], 0, False)
    return m


def fire(ins, m, direction="forward", at=0):
    ins.at = at
    getattr(ins, direction)(m)


def test_a_classical_loop_refuses_a_dirty_counter():
    from reverie.isa import CFrom

    m = bare_machine()
    m.mem[m.fp] = 3
    with raises(RuntimeFault, "counter was not zero on entry"):
        fire(CFrom(0), m)


def test_a_classical_loop_tail_requires_a_counted_iteration():
    from reverie.isa import CRepeat

    m = bare_machine()
    ins = CRepeat(0)
    ins.from_at, ins.until = 0, 1
    with raises(RuntimeFault, "without counting an iteration"):
        fire(ins, m)


def test_reverse_entering_a_classical_loop_checks_the_counter():
    from reverie.isa import CRepeat

    m = bare_machine()
    m.mem[m.fp] = 2
    ins = CRepeat(0)
    ins.from_at, ins.until = 0, 1
    with raises(RuntimeFault, "not zero on reverse entry"):
        fire(ins, m, "backward")


def test_the_tape_cannot_underflow():
    from reverie.isa import CFi, CRepeat, CSet

    m = bare_machine()
    ins = CRepeat(0)
    ins.from_at, ins.until = 0, 1
    with raises(RuntimeFault, "history tape underflow in crepeat"):
        fire(ins, m, "backward")

    fi = CFi()
    fi.then_end, fi.else_end = 1, 2
    with raises(RuntimeFault, "history tape underflow in cfi"):
        fire(fi, m, "backward")

    with raises(RuntimeFault, "history tape underflow in cset"):
        fire(CSet(LocalA(0, "t"), Const(1)), m, "backward")

    m2 = bare_machine()
    m2.history.append(0)
    with raises(RuntimeFault, "history tape underflow in cset"):
        fire(CSet(IndexA(LocalA(0, "t"), Const(0), 2, "t"), Const(1)), m2, "backward")


def test_a_classical_loop_counts_and_uncounts():
    """The head and the test have to agree, or the loop drifts."""
    from reverie.isa import CFrom, CUntil

    m = bare_machine()
    head, test = CFrom(0), CUntil(Const(1), 0)
    head.repeat, test.repeat = 9, 9
    fire(head, m, at=0)
    eq(m.mem[m.fp], 0)
    for expected in (1, 2, 3):
        fire(test, m, at=1)
        eq(m.mem[m.fp], expected)
    for expected in (2, 1, 0):
        fire(test, m, "backward", at=1)
        eq(m.mem[m.fp], expected)


def test_the_exit_of_a_classical_loop_banks_the_counter():
    from reverie.isa import CRepeat, CUntil

    m = bare_machine()
    m.mem[m.fp] = 7
    test = CUntil(Const(0), 0)  # condition false -> leave the loop
    test.repeat = 9
    fire(test, m, at=1)
    eq(m.mem[m.fp], 0, "the counter is banked, not left behind")
    eq(list(m.history), [7])

    tail = CRepeat(0)
    tail.from_at, tail.until = 0, 1
    fire(tail, m, "backward", at=9)
    eq(m.mem[m.fp], 7, "and handed back on the way in")
    eq(list(m.history), [])


def test_reverse_entering_a_loop_checks_the_exit_test():
    """Walking back into a loop body, the exit test must not already hold."""
    from reverie.isa import Until

    body = rir.RLoop(
        Bin("==", Load(AbsA(X, "x")), Const(0)),
        rir.RSkip(),
        rir.RUpdate("+", AbsA(X, "x"), Const(1)),
        Bin("==", Load(AbsA(X, "x")), Const(3)),
    )
    p = prog(body)
    m = Machine(p, mem_size=64)
    m.start_forward()
    for _ in range(4):
        m.step()
    # tamper: make the exit test true at a point the loop is still running
    m.set_globals({"x": 3})
    m.reverse()
    with raises(RuntimeFault, "must fail when re-entering the loop backwards"):
        m.run()


def test_the_tail_of_a_loop_checks_the_entry_test():
    """Arriving at the tail, the entry assertion must no longer hold."""
    p = prog(
        rir.RLoop(
            Bin("==", Load(AbsA(X, "x")), Const(0)),
            rir.RSkip(),
            rir.RUpdate("+", AbsA(Y, "y"), Const(1)),
            Bin("==", Load(AbsA(Y, "y")), Const(3)),
        )
    )
    with raises(RuntimeFault, "must fail on every arrival"):
        run(p, {})
