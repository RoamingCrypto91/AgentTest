"""The formatter, and golden snapshots of what the compiler emits."""

from framework import case, contains, eq, golden, is_true
from support import EXAMPLES, ROOT
from reverie.compiler import compile_text
from reverie.parser import parse_text
from reverie.printer import print_expr, print_module, print_stmt


def fmt(text):
    return print_module(parse_text(text, "t.rev"))


def expr_of(text):
    stmts = parse_text(f"int x; int y; int a[4]; proc main() {{ x += {text}; }}",
                       "t.rev").find_proc("main").body.stmts
    return print_expr(stmts[0].expr)


@case("1+2*3", "1 + 2 * 3")
@case("(1+2)*3", "(1 + 2) * 3")
@case("1-(2-3)", "1 - (2 - 3)")
@case("1-2-3", "1 - 2 - 3")
@case("2**3**4", "2 ** 3 ** 4")
@case("(2**3)**4", "(2 ** 3) ** 4")
@case("-x", "-x")
@case("-(x+y)", "-(x + y)")
@case("!(x>y)", "!(x > y)")
@case("~x & y", "~x & y")
@case("x|y&1", "x | y & 1")
@case("(x|y)&1", "(x | y) & 1")
@case("a[x+1]", "a[x + 1]")
@case("min(x,max(y,1))", "min(x, max(y, 1))")
@case("x<1 && y>2 || x==0", "x < 1 && y > 2 || x == 0")
def test_expression_parenthesisation_is_minimal(source, expected):
    eq(expr_of(source), expected)


def test_formatting_normalises_whitespace():
    got = fmt("int x;proc main(){x+=1;if x>0{x-=1;}fi x==0;}")
    eq(
        got,
        "int x;\n\nproc main() {\n    x += 1;\n    if x > 0 {\n        x -= 1;\n"
        "    } fi x == 0;\n}\n",
    )


def test_empty_blocks_become_skip():
    eq(fmt("proc main() { }"), "proc main() {\n    skip;\n}\n")
    contains(fmt("int x; proc main() { if x > 0 { } fi; }"), "skip;")


def test_declarations_are_grouped():
    got = fmt("const A = 1; const B = 2; int x; int y; proc main(){skip;} proc f(){skip;}")
    eq(got.count("\n\n"), 3)


def test_doc_comments_survive():
    contains(fmt("/// hello\nproc main() { skip; }"), "/// hello")


def test_string_escaping_round_trips():
    text = 'proc main() { print "a\\nb\\tc\\"d\\\\e"; }'
    once = fmt(text)
    eq(fmt(once), once)
    contains(once, '\\n')


@case("countdown.rev")
@case("fibonacci.rev")
@case("rle.rev")
@case("sorting.rev")
def test_formatting_examples_is_a_fixed_point(name):
    import os

    with open(os.path.join(EXAMPLES, name)) as fh:
        text = fh.read()
    once = print_module(parse_text(text, name))
    eq(print_module(parse_text(once, name)), once)


# ---------------------------------------------------------------------------
# golden snapshots
# ---------------------------------------------------------------------------

GOLDEN_SOURCE = """const K = 4;

int x;
int y;
int a[K];
stack s;

/// A little of everything, so the snapshot notices any change in codegen.
proc helper(int p, int q) {
    p += q * 2;
    q ^= 3;
}

proc main() {
    x += 1;
    if x > 0 {
        a[0] += x;
    } else {
        a[1] -= x;
    } fi a[0] != 0;
    local int i = 0;
    from i == 0 do {
        y += a[i];
    } loop {
        i += 1;
    } until i == K - 1;
    delocal int i = K - 1;
    call helper(x, y);
    undo {
        call helper(x, y);
    }
    local int t = 0;
    push(t, s);
    delocal int t = 0;
    embed (y ^= r) {
        var r = 0;
        while (r < x) {
            r = r + 1;
        }
    }
    print "y = ", y;
}
"""


def test_formatter_golden():
    golden("format.rev", print_module(parse_text(GOLDEN_SOURCE, "g.rev")))


def test_disassembly_golden():
    prog = compile_text(GOLDEN_SOURCE, "g.rev")
    text = "globals:\n" + prog.globals_layout() + "\n\n" + prog.disassemble() + "\n"
    golden("disassembly.txt", text)


def test_inverted_source_golden():
    from reverie.inverter import invert_source

    golden("inverted.rev", invert_source(GOLDEN_SOURCE, "g.rev"))


def test_rir_golden():
    from reverie.checker import analyze
    from reverie.compiler import Compiler

    module = parse_text(GOLDEN_SOURCE, "g.rev")
    a = analyze(module)
    a.diagnostics.raise_if_errors()
    c = Compiler(module, a)
    body = c.stmt(module.find_proc("main").body)
    golden("rir.txt", body.render() + "\n\n--- inverted ---\n" + body.invert().render() + "\n")


def test_diagnostics_golden():
    from reverie.checker import analyze

    bad = """int x;
int a[3];
proc f(int p) { p += x; }
proc main() {
    x += x * 2;
    a[a[0]] += 1;
    local int t = 1;
    call f(x);
    call f(x, x);
    if x > 0 { x += 1; } fi;
    y += 1;
}
"""
    a = analyze(parse_text(bad, "bad.rev"))
    golden("diagnostics.txt", a.diagnostics.render() + "\n")
