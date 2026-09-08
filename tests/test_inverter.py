"""Source-to-source program inversion."""

from framework import case, contains, eq, is_true
from reverie.inverter import invert_module, invert_source, invert_stmt
from reverie.parser import parse_text
from reverie.printer import print_module, print_stmt
from reverie.compiler import compile_text
from reverie.vm import Machine, state_equal


def inv_body(text):
    """Invert `main` and return its statements as source lines."""
    module = parse_text(f"int x; int y; int z; int a[4]; stack s;\n"
                        f"proc helper(int p, int q) {{ p += q; }}\n"
                        f"proc main() {{ {text} }}", "t.rev")
    out = invert_module(module, ("main",))
    proc = out.find_proc("main")
    return "\n".join(print_stmt(st, 0) for st in proc.body.stmts)


@case("x += 1;", "x -= 1;")
@case("x -= 1;", "x += 1;")
@case("x ^= 1;", "x ^= 1;")
@case("x *= 3;", "x /= 3;")
@case("x /= 3;", "x *= 3;")
@case("x <<= 2;", "x >>= 2;")
@case("x >>= 2;", "x <<= 2;")
@case("x <=> y;", "x <=> y;")
@case("neg x;", "neg x;")
@case("not x;", "not x;")
@case("skip;", "skip;")
@case("assert x > 0;", "assert x > 0;")
@case("call helper(x, y);", "uncall helper(x, y);")
@case("uncall helper(x, y);", "call helper(x, y);")
@case("push(x, s);", "pop(x, s);")
@case("pop(x, s);", "push(x, s);")
@case("print x;", "unprint x;")
@case("write x;", "unwrite x;")
@case("unprint x;", "print x;")
def test_single_statement_inversion(source, expected):
    eq(inv_body(source), expected)


def test_a_sequence_reverses():
    eq(inv_body("x += 1; y += 2; z += 3;"), "z -= 3;\ny -= 2;\nx -= 1;")


def test_a_conditional_swaps_its_predicates():
    got = inv_body("if x > 0 { y += 1; } else { y -= 1; } fi y != 0;")
    eq(got, "if y != 0 {\n    y -= 1;\n} else {\n    y += 1;\n} fi x > 0;")


def test_a_loop_swaps_entry_and_exit():
    got = inv_body("from x == 0 do { y += 1; } loop { x += 1; } until x == 5;")
    eq(got, "from x == 5 do {\n    y -= 1;\n} loop {\n    x -= 1;\n} until x == 0;")


def test_local_and_delocal_swap_and_stay_paired():
    got = inv_body("local int t = x; y ^= t; delocal int t = x;")
    eq(got, "local int t = x;\ny ^= t;\ndelocal int t = x;")


def test_undo_inverts_to_a_plain_block():
    eq(inv_body("undo { x += 1; y += 2; }"), "{\n    x += 1;\n    y += 2;\n}")


def test_embed_is_almost_its_own_inverse():
    got = inv_body("embed (x ^= a, y += a) { var a = 1; }")
    contains(got, "y -= a")
    contains(got, "x ^= a")
    # bindings come back in the opposite order, since they are undone in reverse
    is_true(got.index("y -= a") < got.index("x ^= a"))


def test_inverting_a_named_procedure_leaves_the_others_alone():
    src = ("int x;\nproc a() { x += 1; }\nproc b() { x += 2; }\n"
           "proc main() { call a(); call b(); }")
    out = print_module(invert_module(parse_text(src), ("a",)))
    contains(out, "proc a() {\n    x -= 1;\n}")
    contains(out, "proc b() {\n    x += 2;\n}")
    contains(out, "call a();\n    call b();")


def test_keep_original_adds_a_second_procedure():
    src = "int x;\nproc step() { x += 1; }\nproc main() { call step(); }"
    out = print_module(
        invert_module(parse_text(src), ("step",), keep_original=True, suffix="_back")
    )
    contains(out, "proc step()")
    contains(out, "proc step_back()")


def test_the_inverse_note_is_added_to_the_doc_comment():
    src = "int x;\n/// bumps x\nproc main() { x += 1; }"
    out = invert_source(src)
    contains(out, "/// bumps x")
    contains(out, "/// The inverse of `main`.")


def test_inverting_an_unknown_procedure_is_an_error():
    from reverie.diagnostics import CompileError
    from framework import raises

    with raises(CompileError, "unknown procedure"):
        invert_module(parse_text("proc main() { skip; }"), ("nope",))


# ---------------------------------------------------------------------------
# behavioural checks on the examples
# ---------------------------------------------------------------------------


def _behaviour_matches(src, initial=None):
    prog = compile_text(src, "a.rev")
    m = Machine(prog, mem_size=1024)
    if initial:
        m.set_globals(initial)
    before = m.snapshot()
    m.start_forward().run()
    after = m.snapshot()

    inv = compile_text(invert_source(src, "a.rev"), "b.rev")
    m2 = Machine(inv, mem_size=1024).restore(after)
    m2.start_forward().run()
    ok, why = state_equal(before, m2.snapshot())
    is_true(ok, why)


def test_inverse_of_a_program_with_output():
    _behaviour_matches(
        'int x; proc main() { x += 3; print "x is ", x; write "and "; print "done"; }'
    )


def test_inverse_of_a_program_with_stacks():
    _behaviour_matches(
        "int x; stack s;\n"
        "proc main() { local int t = 4; push(t, s); delocal int t = 0;\n"
        "  local int u = 0; pop(u, s); x += u; push(u, s); delocal int u = 0; }"
    )


def test_inverse_of_a_recursive_program():
    _behaviour_matches(
        "int n; int acc;\n"
        "proc down(int k, int a) {\n"
        "  if k > 0 { a += k; k -= 1; call down(k, a); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n, acc); }",
        {"n": 6},
    )


def test_inverse_of_an_embed():
    _behaviour_matches(
        "int n; int root;\n"
        "proc main() { embed (root ^= r) { var r = 0;\n"
        "  while ((r + 1) * (r + 1) <= n) { r = r + 1; } } }",
        {"n": 12345},
    )
