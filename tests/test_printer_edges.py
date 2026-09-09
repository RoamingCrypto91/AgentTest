"""Printer paths the examples do not happen to exercise."""

from framework import case, contains, eq, is_true, raises
from reverie import ast
from reverie.parser import parse_text
from reverie.printer import (
    _body,
    expr_prec,
    print_cstmt,
    print_decl,
    print_expr,
    print_module,
    print_stmt,
    print_type,
)


def fmt(text):
    return print_module(parse_text(text, "t.rev"))


def test_a_missing_expression_prints_as_a_question_mark():
    eq(print_expr(None), "?")


def test_expression_precedence_by_kind():
    eq(expr_prec(ast.BinOp(None, "+", ast.Num(None, 1), ast.Num(None, 2))), 9)
    eq(expr_prec(ast.UnOp(None, "-", ast.Num(None, 1))), 11)
    eq(expr_prec(ast.Num(None, 1)), 20)
    eq(expr_prec(ast.BinOp(None, "??", ast.Num(None, 1), ast.Num(None, 2))), 0)


def test_empty_blocks():
    eq(print_stmt(ast.Block(None, []), 0), "{ }")
    eq(_body(None, 0), "")
    eq(_body(ast.Skip(None), 0), "    skip;")


def test_local_arrays_print_without_an_initialiser():
    got = fmt("proc main() { local int buf[4]; delocal int buf[4]; }")
    contains(got, "local int buf[4];")
    contains(got, "delocal int buf[4];")


def test_types():
    eq(print_type(ast.TInt(None), "x"), "int x")
    eq(print_type(ast.TStack(None), "s"), "stack s")
    eq(print_type(ast.TArray(None, ast.Num(None, 3)), "a"), "int a[3]")
    eq(print_type(ast.TArray(None, None), "a"), "int a[]")


def test_classical_statements_print():
    eq(print_cstmt(None, 0), "")
    got = fmt(
        "int x; proc main() { embed (x ^= a[0]) {\n"
        "  var a[4];\n"
        "  for (var i = 0; i < 4; i = i + 1) { a[i] = i * i; }\n"
        "  while (a[0] < 0) { a[0] = a[0] + 1; }\n"
        "  if (a[1] > 0) { a[1] = 0 - a[1]; } else { a[1] = 1; }\n"
        "} }"
    )
    contains(got, "var a[4];")
    contains(got, "for (var i = 0; i < 4; i = i + 1) {")
    contains(got, "while (a[0] < 0) {")
    contains(got, "} else {")
    eq(fmt(got), got, "printing a `for` block must be a fixed point")


def test_imports_print():
    eq(print_decl(ast.Import(None, "array.rev")), 'import "array.rev";')


def test_unprintable_nodes_are_reported():
    with raises(TypeError, "cannot print expression"):
        print_expr(ast.Expr(None))
    with raises(TypeError, "cannot print statement"):
        print_stmt(ast.Stmt(None), 0)
    with raises(TypeError, "cannot print classical statement"):
        print_cstmt(ast.CStmt(None), 0)
    with raises(TypeError, "cannot print declaration"):
        print_decl(ast.Decl(None))


def test_a_procedure_with_an_empty_body():
    eq(fmt("proc main() { }"), "proc main() {\n    skip;\n}\n")


def test_loops_print_every_combination():
    for text in (
        "from x == 0 until x == 1;",
        "from x == 0 do { x += 1; } until x == 1;",
        "from x == 0 loop { x += 1; } until x == 1;",
        "from x == 0 do { x += 1; } loop { x += 1; } until x == 3;",
    ):
        src = f"int x; proc main() {{ {text} }}"
        once = fmt(src)
        eq(fmt(once), once, f"not a fixed point: {text}")
