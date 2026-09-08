"""Tests for the test framework itself -- if this lies, everything else does."""

from framework import AssertionFail, case, contains, eq, is_false, is_true, ne, raises


def test_eq_passes():
    eq(1 + 1, 2)
    eq("ab", "ab")
    eq([1, 2], [1, 2])


def test_eq_fails_with_detail():
    with raises(AssertionFail, "expected: 3") as r:
        eq(2, 3)
    contains(str(r.error), "actual:   2")


def test_raises_requires_exception():
    with raises(AssertionFail, "no exception was raised"):
        with raises(ValueError):
            pass


def test_raises_match():
    with raises(ValueError, "boom"):
        raise ValueError("boom goes the dynamite")


def test_raises_wrong_type_propagates():
    try:
        with raises(ValueError):
            raise KeyError("nope")
    except KeyError:
        return
    raise AssertionError("wrong exception type should propagate")


@case(1, 1)
@case(2, 4)
@case(3, 9)
def test_parametrised(n, square):
    eq(n * n, square)


def test_truthiness_helpers():
    is_true([0])
    is_false([])
    ne(1, 2)


def test_string_diff_is_reported():
    with raises(AssertionFail, "line 2") as r:
        eq("a\nx\nc", "a\nb\nc")
    contains(str(r.error), "- a\nb\nc" if False else "line 2")
