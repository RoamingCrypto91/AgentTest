"""The pure expression and address layer the machine evaluates."""

from framework import case, contains, eq, is_true, raises
from reverie.diagnostics import RuntimeFault
from reverie.ir import (
    AbsA,
    ArrayLen,
    Bin,
    Const,
    IndexA,
    Load,
    LocalA,
    ParamA,
    StackQuery,
    Un,
    idiv,
    imod,
    ipow,
    as_written,
    ishl,
    ishr,
)


class Fake:
    """Just enough machine for the pure layer to run against."""

    def __init__(self, mem=None, fp=0, params=None, lens=None, stacks=None):
        self.mem = list(mem or [0] * 32)
        self.fp = fp
        self.stacks = [list(s) for s in (stacks or [])]
        self.frame = None
        if params is not None:
            class F:
                pass

            self.frame = F()
            self.frame.params = list(params)
            self.frame.param_lens = list(lens or [1] * len(params))

    def stack_at(self, addr):
        return self.stacks[self.mem[addr] - 1]


# ---------------------------------------------------------------------------
# integer semantics: truncating division, remainder follows the dividend
# ---------------------------------------------------------------------------


@case(7, 2, 3, 1)
@case(-7, 2, -3, -1)
@case(7, -2, -3, 1)
@case(-7, -2, 3, -1)
@case(9, 3, 3, 0)
@case(0, 5, 0, 0)
def test_division_and_remainder(a, b, q, r):
    eq(idiv(a, b), q)
    eq(imod(a, b), r)
    eq(q * b + r, a, "the division identity must hold for every sign")


def test_division_by_zero():
    with raises(RuntimeFault, "division by zero"):
        idiv(1, 0)
    with raises(RuntimeFault, "modulo by zero"):
        imod(1, 0)


def test_power():
    eq(ipow(2, 10), 1024)
    eq(ipow(-3, 3), -27)
    eq(ipow(5, 0), 1)
    with raises(RuntimeFault, "negative exponent"):
        ipow(2, -1)
    with raises(RuntimeFault, "too large"):
        ipow(3, 10_000_000)


def test_an_expression_can_be_rendered_as_it_was_written():
    """Disassembly wants the sigils; a trap message quotes the source."""
    e = Bin(">", Load(ParamA(0, "x")), Const(0))
    eq(e.render(), "(&x > 0)")
    eq(as_written(e), "(x > 0)")
    eq(as_written(Load(AbsA(3, "total"))), "total")
    eq(as_written(Load(LocalA(0, "i"))), "i")
    eq(as_written(ArrayLen(ParamA(0, "xs"))), "len(xs)")
    eq(as_written(Un("abs", Load(ParamA(0, "x")))), "abs(x)")
    eq(as_written(Bin("min", Const(1), Const(2))), "min(1, 2)")
    eq(as_written(StackQuery("size", AbsA(2, "s"))), "size(s)")
    idx = IndexA(AbsA(4, "a"), Load(LocalA(0, "i")), 8, "a")
    eq(as_written(Load(idx)), "a[i]")


def test_rendering_as_source_falls_back_when_there_is_no_name():
    eq(as_written(Load(AbsA(3))), "@3")
    eq(as_written(Load(ParamA(1))), "&1")
    eq(as_written(Load(LocalA(2))), "%2")


def test_shifting():
    eq(ishl(3, 4), 48)
    eq(ishr(48, 4), 3)
    eq(ishr(-1, 40), -1)


def test_a_negative_shift_is_a_trap_not_a_python_error():
    """Found by `rev verify`, which handed `bitrev` a width of zero."""
    with raises(RuntimeFault, "negative shift count"):
        ishl(1, -1)
    with raises(RuntimeFault, "negative shift count"):
        ishr(1, -1)


def test_a_shift_that_would_exhaust_memory_is_a_trap():
    with raises(RuntimeFault, "too large"):
        ishl(1, 1 << 30)
    with raises(RuntimeFault, "too large"):
        ishl(255, 1 << 22)
    eq(ishr(1, 1 << 30), 0, "shifting right is always cheap")


# ---------------------------------------------------------------------------
# addresses
# ---------------------------------------------------------------------------


def test_absolute_address():
    m = Fake([0, 11, 22])
    a = AbsA(2, "g", 4)
    eq(a.resolve(m), 2)
    eq(a.extent(m), 4)
    eq(a.render(), "@g")
    eq(AbsA(3).render(), "@3")


def test_local_address_follows_the_frame_pointer():
    m = Fake(list(range(20)), fp=8)
    a = LocalA(3, "t")
    eq(a.resolve(m), 11)
    eq(Load(a).eval(m), 11)


def test_parameter_address_comes_from_the_frame():
    m = Fake(list(range(20)), params=[7, 12], lens=[1, 5])
    eq(ParamA(0).resolve(m), 7)
    eq(ParamA(1).extent(m), 5)
    eq(ParamA(1, "xs").render(), "&xs")


def test_parameter_outside_a_frame_traps():
    m = Fake()
    with raises(RuntimeFault, "outside of a procedure frame"):
        ParamA(0).resolve(m)
    with raises(RuntimeFault, "outside of a procedure frame"):
        ParamA(0).extent(m)


def test_indexing_is_bounds_checked():
    m = Fake(list(range(20)))
    a = IndexA(AbsA(4, "xs", 3), Const(1), 3, "xs")
    eq(a.resolve(m), 5)
    for bad in (3, -1, 99):
        with raises(RuntimeFault, "out of bounds") as r:
            IndexA(AbsA(4, "xs", 3), Const(bad), 3, "xs").resolve(m)
        contains(str(r.error), "length 3")


def test_a_dynamic_index_uses_the_length_that_was_passed():
    m = Fake(list(range(20)), params=[4], lens=[2])
    a = IndexA(ParamA(0, "xs"), Const(1), -1, "xs")
    eq(a.resolve(m), 5)
    with raises(RuntimeFault, "length 2"):
        IndexA(ParamA(0, "xs"), Const(2), -1, "xs").resolve(m)


def test_array_len_reads_the_frame():
    m = Fake(list(range(20)), params=[4], lens=[6])
    eq(ArrayLen(ParamA(0, "xs")).eval(m), 6)
    eq(ArrayLen(ParamA(0, "xs")).render(), "len(&xs)")


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------


@case("+", 7, 3, 10)
@case("-", 7, 3, 4)
@case("*", 7, 3, 21)
@case("/", 7, 3, 2)
@case("%", 7, 3, 1)
@case("**", 2, 8, 256)
@case("&", 12, 10, 8)
@case("|", 12, 10, 14)
@case("^", 12, 10, 6)
@case("<<", 3, 4, 48)
@case(">>", 48, 4, 3)
@case("==", 3, 3, 1)
@case("!=", 3, 3, 0)
@case("<", 3, 4, 1)
@case("<=", 4, 4, 1)
@case(">", 3, 4, 0)
@case(">=", 4, 4, 1)
@case("min", 3, 4, 3)
@case("max", 3, 4, 4)
def test_binary_operators(op, a, b, want):
    eq(Bin(op, Const(a), Const(b)).eval(Fake()), want)


@case("-", 5, -5)
@case("~", 5, -6)
@case("!", 0, 1)
@case("!", 7, 0)
@case("+", 5, 5)
@case("abs", -9, 9)
@case("sign", -9, -1)
@case("sign", 0, 0)
@case("sign", 9, 1)
def test_unary_operators(op, a, want):
    eq(Un(op, Const(a)).eval(Fake()), want)


def test_logical_operators_short_circuit():
    m = Fake()
    # the right operand would trap if it were evaluated
    bomb = Bin("/", Const(1), Const(0))
    eq(Bin("&&", Const(0), bomb).eval(m), 0)
    eq(Bin("||", Const(1), bomb).eval(m), 1)
    eq(Bin("&&", Const(1), Const(3)).eval(m), 1)
    eq(Bin("||", Const(0), Const(0)).eval(m), 0)


def test_unknown_operators_are_refused_at_construction():
    with raises(ValueError, "unknown binary operator"):
        Bin("<=>", Const(1), Const(2))
    with raises(ValueError, "unknown unary"):
        Un("?", Const(1))
    with raises(ValueError, "unknown stack query"):
        StackQuery("peek", AbsA(0))


def test_stack_queries():
    m = Fake([1] + [0] * 8, stacks=[[4, 5, 6]])
    eq(StackQuery("size", AbsA(0)).eval(m), 3)
    eq(StackQuery("top", AbsA(0)).eval(m), 6)
    eq(StackQuery("empty", AbsA(0)).eval(m), 0)
    m.stacks[0].clear()
    eq(StackQuery("empty", AbsA(0)).eval(m), 1)
    with raises(RuntimeFault, "empty stack"):
        StackQuery("top", AbsA(0)).eval(m)


# ---------------------------------------------------------------------------
# rendering and identity
# ---------------------------------------------------------------------------


def test_rendering():
    e = Bin("+", Load(AbsA(0, "x")), Bin("*", Const(3), Load(LocalA(2, "t"))))
    eq(e.render(), "(@x + (3 * %t+2))")
    eq(Un("-", Const(4)).render(), "-4")
    eq(Un("abs", Const(4)).render(), "abs(4)")
    eq(Bin("min", Const(1), Const(2)).render(), "min(1, 2)")
    eq(StackQuery("top", AbsA(0, "s")).render(), "top(@s)")
    eq(repr(Const(9)), "9")


def test_structural_equality_and_hashing():
    a = Bin("+", Load(AbsA(0, "x")), Const(1))
    b = Bin("+", Load(AbsA(0, "x")), Const(1))
    c = Bin("+", Load(AbsA(1, "y")), Const(1))
    eq(a, b)
    is_true(a != c)
    eq(len({a, b, c}), 2)
    eq(AbsA(3), AbsA(3, "different name"))
    is_true(AbsA(3) != LocalA(3))
    eq(len({IndexA(AbsA(0), Const(1), 4), IndexA(AbsA(0), Const(1), 4)}), 1)


def test_children_expose_the_tree():
    e = Bin("+", Const(1), Un("-", Const(2)))
    eq(len(e.children()), 2)
    eq(Load(AbsA(0)).children()[0], AbsA(0))
    eq(IndexA(AbsA(0), Const(1), 2).children()[1], Const(1))
    eq(Const(1).children(), ())
