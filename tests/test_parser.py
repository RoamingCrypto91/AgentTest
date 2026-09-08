"""Parser tests: shapes accepted, shapes rejected, and the messages given."""

from framework import case, contains, eq, is_true, raises
from reverie import ast
from reverie.diagnostics import ParseError
from reverie.parser import parse_text
from reverie.printer import print_module


def parse(text):
    return parse_text(text, "t.rev")


def main_of(text):
    return parse(text).find_proc("main").body.stmts


def test_declarations():
    m = parse("const K = 3; int x; int a[K]; stack s; proc main() { skip; }")
    kinds = [type(d).__name__ for d in m.decls]
    eq(kinds, ["ConstDecl", "GlobalDecl", "GlobalDecl", "GlobalDecl", "ProcDecl"])
    eq(m.decls[0].name, "K")
    is_true(isinstance(m.decls[2].type, ast.TArray))
    is_true(isinstance(m.decls[3].type, ast.TStack))


def test_doc_comments_attach_to_procedures():
    m = parse("/// first line\n/// second\nproc main() { skip; }")
    eq(m.find_proc("main").doc, "first line\nsecond")


@case("x += 1;", ast.Update)
@case("x -= 1;", ast.Update)
@case("x ^= 1;", ast.Update)
@case("x *= 2;", ast.Update)
@case("x /= 2;", ast.Update)
@case("x <<= 2;", ast.Update)
@case("x >>= 2;", ast.Update)
@case("x <=> y;", ast.Swap)
@case("neg x;", ast.UnaryStmt)
@case("not x;", ast.UnaryStmt)
@case("skip;", ast.Skip)
@case(";", ast.Skip)
@case("assert x > 0;", ast.Assert)
@case("print x;", ast.Print)
@case("write x;", ast.Print)
@case("push(x, s);", ast.StackOp)
@case("pop(x, s);", ast.StackOp)
@case("call f(x);", ast.Call)
@case("uncall f(x);", ast.Call)
@case("undo { x += 1; }", ast.Undo)
@case("{ x += 1; }", ast.Block)
@case("if x > 0 { x += 1; } fi x > 1;", ast.If)
@case("from x == 0 until x == 3;", ast.Loop)
def test_statement_shapes(text, node_type):
    stmts = main_of(f"int x; int y; stack s; proc main() {{ {text} }} proc f(int a) {{ a += 1; }}")
    is_true(isinstance(stmts[0], node_type), f"{text!r} parsed as {type(stmts[0]).__name__}")


def test_print_and_write_differ_only_in_the_newline():
    stmts = main_of("int x; proc main() { print x; write x; }")
    eq((stmts[0].newline, stmts[1].newline), (True, False))


def test_fi_without_an_expression_reuses_the_entry_test():
    stmt = main_of("int x; proc main() { if x > 0 { skip; } fi; }")[0]
    is_true(stmt.exit is stmt.entry, "`fi;` should reuse the entry expression object")


def test_else_if_chains():
    stmt = main_of(
        "int x; proc main() { if x > 2 { skip; } else if x > 1 { skip; } "
        "fi x > 1; fi x > 2; }"
    )[0]
    is_true(isinstance(stmt.otherwise, ast.If))


def test_loop_parts_are_optional():
    s = main_of("int x; proc main() { from x == 0 until x == 1; }")[0]
    is_true(isinstance(s.body, ast.Skip))
    is_true(isinstance(s.step, ast.Skip))


def test_precedence():
    e = main_of("int x; proc main() { x += 1 + 2 * 3 - 4; }")[0].expr
    from reverie.printer import print_expr

    eq(print_expr(e), "1 + 2 * 3 - 4")
    e2 = main_of("int x; proc main() { x += (1 + 2) * 3; }")[0].expr
    eq(print_expr(e2), "(1 + 2) * 3")


def test_power_is_right_associative():
    from reverie.printer import print_expr

    e = main_of("int x; proc main() { x += 2 ** 3 ** 2; }")[0].expr
    eq(print_expr(e), "2 ** 3 ** 2")
    eq(e.right.op, "**")


def test_comparison_chain_binds_looser_than_arithmetic():
    from reverie.printer import print_expr

    e = main_of("int x; proc main() { x += 1 + 2 < 3 * 4; }")[0].expr
    eq(print_expr(e), "1 + 2 < 3 * 4")


def test_builtins():
    from reverie.printer import print_expr

    e = main_of("int x; int a[3]; stack s; proc main() { x += min(1, 2) + max(3, 4) "
                "+ abs(-5) + sign(6) + len(a) + size(s) + top(s) + empty(s); }")[0].expr
    for name in ("min", "max", "abs", "sign", "len", "size", "top", "empty"):
        contains(print_expr(e), name + "(")


def test_unknown_builtin_is_rejected_with_a_list():
    with raises(ParseError, "unknown builtin") as r:
        parse("int x; proc main() { x += frobnicate(1); }")
    contains(r.error.notes[0], "min")


def test_builtin_arity_is_checked():
    with raises(ParseError, "takes 2 argument"):
        parse("int x; proc main() { x += min(1); }")


def test_plain_assignment_is_rejected_helpfully():
    with raises(ParseError, "not reversible") as r:
        parse("int x; proc main() { x = 1; }")
    contains(" ".join(r.error.notes), "embed")


def test_missing_semicolon_points_at_the_right_token():
    with raises(ParseError, "expected `;`") as r:
        parse("int x; proc main() { x += 1 }")
    eq(r.error.span.text, "}")


def test_unterminated_block():
    with raises(ParseError, "unterminated block"):
        parse("int x; proc main() { x += 1;")


def test_embed_bindings():
    s = main_of("int x; proc main() { embed (x ^= r, x += r) { var r = 1; } }")[0]
    eq([b.op for b in s.bindings], ["^", "+"])


def test_embed_requires_a_combining_operator():
    with raises(ParseError, "expected `^=`"):
        parse("int x; proc main() { embed (x = r) { var r = 1; } }")


def test_classical_statements():
    s = main_of(
        "int x; proc main() { embed (x ^= a) { var a = 0; var b[3]; "
        "while (a < 3) { a = a + 1; } "
        "for (var i = 0; i < 3; i = i + 1) { b[i] = i; } "
        "if (a > 1) { a = a - 1; } else { a = a + 1; } } }"
    )[0]
    kinds = [type(x).__name__ for x in s.body.stmts]
    eq(kinds, ["CVar", "CVar", "CWhile", "CFor", "CIf"])


def test_classical_compound_assignment_desugars():
    s = main_of("int x; proc main() { embed (x ^= a) { var a = 1; a += 2; } }")[0]
    assign = s.body.stmts[1]
    is_true(isinstance(assign.expr, ast.BinOp))
    eq(assign.expr.op, "+")


def test_string_literals_only_in_print():
    with raises(ParseError, "only allowed in `print`"):
        parse('int x; proc main() { x += "no"; }')


def test_import_declaration():
    m = parse('import "array.rev"; proc main() { skip; }')
    is_true(isinstance(m.decls[0], ast.Import))
    eq(m.decls[0].path, "array.rev")


def test_stdin_style_module_round_trips_through_the_printer():
    text = (
        "const K = 4;\n\nint x;\nint a[K];\n\n"
        "proc main() {\n    x += 1;\n    a[0] += x;\n}\n"
    )
    eq(print_module(parse(text)), text)
