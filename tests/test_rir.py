"""The structured IR: inversion, rendering, and lowering."""

from framework import case, contains, eq, is_true, raises
from reverie import rir
from reverie.ir import AbsA, Bin, Const, Load, LocalA
from reverie.isa import Nop

X = AbsA(0, "x")
Y = AbsA(1, "y")
S = AbsA(2, "s")
gx = Load(X)


def render(stmt):
    return stmt.render()


def inv(stmt):
    return stmt.invert().render()


# ---------------------------------------------------------------------------
# inversion
# ---------------------------------------------------------------------------


@case("+", "-")
@case("-", "+")
@case("^", "^")
@case("*", "/")
@case("/", "*")
@case("<<", ">>")
@case(">>", "<<")
def test_updates_invert_by_operator(op, want):
    eq(inv(rir.RUpdate(op, X, Const(3))), f"@x {want}= 3")


def test_self_inverse_statements():
    for stmt in (
        rir.RSkip(),
        rir.RSwap(X, Y),
        rir.RAssert(gx),
        rir.RUnary("neg", X),
        rir.RUnary("not", X),
    ):
        eq(inv(stmt), render(stmt))


def test_paired_statements_swap():
    eq(inv(rir.RLocal(0, Const(1), 1, "t")), "delocal t = 1")
    eq(inv(rir.RDelocal(0, Const(1), 1, "t")), "local t = 1")
    eq(inv(rir.RPush(X, S)), "pop(@x, @s)")
    eq(inv(rir.RPop(X, S)), "push(@x, @s)")
    eq(inv(rir.REmit(["hi"])), "unprint 'hi'")
    eq(inv(rir.RUnemit(["hi"])), "print 'hi'")
    eq(inv(rir.REmit(["hi"], None, False)), "unwrite 'hi'")
    eq(inv(rir.RCall("f", [X])), "uncall f(@x)")
    eq(inv(rir.RCall("f", [X], True)), "call f(@x)")


def test_a_sequence_reverses():
    seq = rir.RSeq([
        rir.RUpdate("+", X, Const(1)),
        rir.RUpdate("*", Y, Const(3)),
        rir.RSwap(X, Y),
    ])
    eq(inv(seq), "@x <=> @y\n@y /= 3\n@x -= 1")


def test_a_conditional_swaps_its_predicates():
    stmt = rir.RIf(
        Bin(">", gx, Const(0)),
        rir.RUpdate("+", Y, Const(1)),
        rir.RUpdate("-", Y, Const(1)),
        Bin("!=", Load(Y), Const(0)),
    )
    got = inv(stmt)
    contains(got, "if (@y != 0) then")
    contains(got, "fi (@x > 0)")
    contains(got, "@y -= 1")


def test_a_loop_swaps_entry_and_exit():
    stmt = rir.RLoop(
        Bin("==", gx, Const(0)),
        rir.RUpdate("+", Y, Const(1)),
        rir.RUpdate("+", X, Const(1)),
        Bin("==", gx, Const(5)),
    )
    got = inv(stmt)
    contains(got, "from (@x == 5) do")
    contains(got, "until (@x == 0)")
    contains(got, "@y -= 1")
    contains(got, "@x -= 1")


def test_inverting_twice_is_the_identity():
    stmt = rir.RSeq([
        rir.RUpdate("+", X, Const(1)),
        rir.RIf(gx, rir.RPush(X, S), rir.RSkip(), Load(Y)),
        rir.RLoop(gx, rir.RSwap(X, Y), rir.RCall("f", [X]), Load(Y)),
        rir.RLocal(0, Const(0), 1, "t"),
        rir.RDelocal(0, Const(0), 1, "t"),
    ])
    eq(stmt.invert().invert().render(), stmt.render())


def test_classical_statements_refuse_to_be_inverted_alone():
    for stmt in (
        rir.CSetStmt(X, Const(1)),
        rir.CIfStmt(gx, rir.RSkip(), rir.RSkip()),
        rir.CWhileStmt(gx, rir.RSkip(), 0),
    ):
        with raises(TypeError, "uncalling their procedure"):
            stmt.invert()


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_rendering_covers_every_node():
    eq(render(rir.RSkip()), "skip")
    eq(render(rir.RSeq([])), "skip")
    eq(render(rir.RUnary("neg", X)), "neg @x")
    eq(render(rir.RSwap(X, Y)), "@x <=> @y")
    eq(render(rir.RAssert(gx)), "assert @x")
    eq(render(rir.RLocal(0, None, 4, "buf")), "local buf[4]")
    eq(render(rir.RDelocal(0, None, 4, "buf")), "delocal buf[4]")
    eq(render(rir.CSetStmt(X, Const(2))), "@x := 2")
    contains(render(rir.CIfStmt(gx, rir.RSkip(), rir.RSkip())), "cif @x {")
    contains(render(rir.CWhileStmt(gx, rir.RSkip(), 1)), "cwhile @x {")
    contains(repr(rir.RUpdate("+", X, Const(1))), "@x += 1")


def test_indentation_nests():
    stmt = rir.RIf(gx, rir.RUpdate("+", Y, Const(1)), rir.RSkip(), gx)
    lines = render(stmt).splitlines()
    eq(lines[1], "  @y += 1")


# ---------------------------------------------------------------------------
# traversal and lowering
# ---------------------------------------------------------------------------


def test_walk_visits_children():
    inner = rir.RUpdate("+", Y, Const(1))
    stmt = rir.RIf(gx, inner, rir.RSkip(), gx)
    seen = list(stmt.walk())
    is_true(inner in seen)
    eq(len(seen), 3)
    loop = rir.RLoop(gx, inner, rir.RSkip(), gx)
    eq(len(list(loop.walk())), 3)
    eq(len(rir.RUpdate("+", X, Const(1)).children()), 0)


def test_the_abstract_base_is_abstract():
    base = rir.RStmt()
    with raises(NotImplementedError):
        base.invert()
    with raises(NotImplementedError):
        base.lower(rir.CodeBuilder())
    with raises(NotImplementedError):
        base.render()
    eq(base.children(), ())


def test_lowering_assigns_indices():
    code = rir.lower_body(
        rir.RSeq([rir.RSkip(), rir.RUpdate("+", X, Const(1)), rir.RSkip()])
    )
    eq([c.at for c in code], [0, 1, 2])
    eq([c.name for c in code], ["nop", "upd", "nop"])


def test_code_builder_length():
    b = rir.CodeBuilder()
    eq(len(b), 0)
    b.emit(Nop())
    eq(len(b), 1)


def test_lowering_a_conditional_wires_every_target():
    code = rir.lower_body(
        rir.RIf(gx, rir.RUpdate("+", Y, Const(1)), rir.RUpdate("-", Y, Const(1)), gx)
    )
    kinds = [c.name for c in code]
    eq(kinds, ["if", "upd", "else_end", "else_begin", "upd", "fi"])
    if_, ee, eb, fi = code[0], code[2], code[3], code[5]
    eq((if_.else_begin, if_.fi), (3, 5))
    eq(ee.fi, 5)
    eq(eb.if_at, 0)
    eq((fi.then_end, fi.else_end, fi.if_at), (2, 4, 0))


def test_lowering_a_loop_wires_every_target():
    code = rir.lower_body(
        rir.RLoop(gx, rir.RUpdate("+", Y, Const(1)), rir.RUpdate("+", X, Const(1)), gx)
    )
    eq([c.name for c in code], ["from", "upd", "until", "upd", "repeat"])
    f, u, r = code[0], code[2], code[4]
    eq((f.until, f.repeat), (2, 4))
    eq((u.from_at, u.repeat), (0, 4))
    eq((r.from_at, r.until), (0, 2))


def test_lowering_a_classical_loop_wires_every_target():
    code = rir.lower_body(rir.CWhileStmt(gx, rir.RUpdate("+", Y, Const(1)), 3))
    eq([c.name for c in code], ["cfrom", "cuntil", "upd", "crepeat"])
    f, u, r = code[0], code[1], code[3]
    eq(f.repeat, 3)
    eq((u.repeat, u.counter), (3, 3))
    eq((r.from_at, r.until, r.counter), (0, 1, 3))


def test_lowering_a_classical_conditional():
    code = rir.lower_body(rir.CIfStmt(gx, rir.RSkip(), rir.RSkip()))
    eq([c.name for c in code], ["cif", "nop", "celse_end", "celse_begin", "nop", "cfi"])
    eq(code[5].then_end, 2)
