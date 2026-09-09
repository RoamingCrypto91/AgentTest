"""Features crossing each other: recursion with embed, undo with uncall, and
so on.  Each construct is tested on its own elsewhere; these are the corners
where two of them meet.
"""

from framework import case, contains, eq, is_true, raises
from reverie.compiler import compile_text
from reverie.diagnostics import RuntimeFault
from reverie.vm import Machine, state_equal


def both_ways(src, initial=None, mem=4096, paranoid=True):
    prog = compile_text(src, "x.rev")
    m = Machine(prog, mem_size=mem, max_steps=5_000_000, paranoid=paranoid)
    if initial:
        m.set_globals(initial)
    before = m.snapshot()
    m.start_forward().run()
    forward, output = m.globals_dict(), list(m.output)
    m.check_clean()
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, why)
    return forward, output


def test_an_embed_inside_a_recursive_procedure():
    """Each level of recursion needs its own scratch cells."""
    forward, _ = both_ways(
        "int n; int acc;\n"
        "proc walk(int k, int out) {\n"
        "  embed (out += sq) { var sq = k * k; }\n"
        "  if k > 0 { k -= 1; call walk(k, out); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call walk(n, acc); }",
        {"n": 6},
    )
    eq(forward["acc"], sum(i * i for i in range(7)))
    eq(forward["n"], 6)


def test_an_embed_with_a_classical_array_inside_recursion():
    forward, _ = both_ways(
        "int n; int acc;\n"
        "proc walk(int k, int out) {\n"
        "  embed (out += t[2]) {\n"
        "    var t[4]; var i = 0;\n"
        "    while (i < 4) { t[i] = k + i; i = i + 1; }\n"
        "  }\n"
        "  if k > 0 { k -= 1; call walk(k, out); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call walk(n, acc); }",
        {"n": 5},
    )
    eq(forward["acc"], sum(k + 2 for k in range(6)))


def test_undo_of_a_call_that_prints():
    forward, output = both_ways(
        'int x;\n'
        'proc noisy(int p) { p += 1; print "p is ", p; }\n'
        'proc main() { call noisy(x); undo { call noisy(x); } print "done"; }'
    )
    eq(output, ["done"])
    eq(forward["x"], 0)


def test_nested_undo_is_the_identity():
    forward, _ = both_ways(
        "int x; proc main() { x += 5; undo { undo { x += 5; } } }"
    )
    eq(forward["x"], 10)


def test_undo_around_a_loop_containing_an_embed():
    forward, _ = both_ways(
        "int total;\n"
        "proc main() {\n"
        "  local int i = 0;\n"
        "  from i == 0 do {\n"
        "    embed (total += v) { var v = i * 2; }\n"
        "  } loop { i += 1; } until i == 4;\n"
        "  delocal int i = 4;\n"
        "  undo {\n"
        "    local int j = 0;\n"
        "    from j == 0 do {\n"
        "      embed (total += v) { var v = j * 2; }\n"
        "    } loop { j += 1; } until j == 4;\n"
        "    delocal int j = 4;\n"
        "  }\n"
        "}"
    )
    eq(forward["total"], 0)


def test_a_stack_threaded_through_recursion():
    forward, _ = both_ways(
        "int n; stack s;\n"
        "proc down(int k, stack log) {\n"
        "  if k > 0 {\n"
        "    local int d = 0; d += k; push(d, log); delocal int d = 0;\n"
        "    k -= 1; call down(k, log); k += 1;\n"
        "  } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n, s); }",
        {"n": 5},
    )
    eq(forward["s"], [5, 4, 3, 2, 1])


def test_uncall_unwinds_a_stack_built_by_recursion():
    forward, _ = both_ways(
        "int n; stack s;\n"
        "proc down(int k, stack log) {\n"
        "  if k > 0 {\n"
        "    local int d = 0; d += k; push(d, log); delocal int d = 0;\n"
        "    k -= 1; call down(k, log); k += 1;\n"
        "  } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n, s); uncall down(n, s); }",
        {"n": 5},
    )
    eq(forward["s"], [])


def test_main_may_call_itself():
    forward, _ = both_ways(
        "int n; int acc;\n"
        "proc main() {\n"
        "  if n > 0 { acc += n; n -= 1; call main(); n += 1; } else { skip; } fi n > 0;\n"
        "}",
        {"n": 5},
    )
    eq(forward["acc"], 15)
    eq(forward["n"], 5)


def test_mutual_recursion():
    forward, _ = both_ways(
        "int n; int evens; int odds;\n"
        "proc even(int k) { if k > 0 { evens += 1; k -= 1; call odd(k); k += 1; }"
        " else { skip; } fi k > 0; }\n"
        "proc odd(int k) { if k > 0 { odds += 1; k -= 1; call even(k); k += 1; }"
        " else { skip; } fi k > 0; }\n"
        "proc main() { call even(n); }",
        {"n": 7},
    )
    eq((forward["evens"], forward["odds"]), (4, 3))


def test_an_array_passed_down_several_levels():
    forward, _ = both_ways(
        "int xs[5]; int acc;\n"
        "proc inner(int a[], int out) { out += a[0] + a[len(a) - 1]; }\n"
        "proc middle(int a[], int out) { call inner(a, out); }\n"
        "proc main() { call middle(xs, acc); }",
        {"xs": [3, 0, 0, 0, 9]},
    )
    eq(forward["acc"], 12)


def test_an_array_slice_length_follows_the_caller():
    forward, _ = both_ways(
        "int a[3]; int b[7]; int acc;\n"
        "proc size_of(int xs[], int out) { out += len(xs); }\n"
        "proc main() { call size_of(a, acc); call size_of(b, acc); }"
    )
    eq(forward["acc"], 10)


def test_uncall_inside_a_conditional_inside_a_loop():
    forward, _ = both_ways(
        "int g; int acc;\n"
        "proc add(int out, int k) { out += k * 2; }\n"
        "proc main() {\n"
        "  local int i = 0;\n"
        "  from i == 0 do {\n"
        "    if g > 0 { call add(acc, i); } else { uncall add(acc, i); } fi g > 0;\n"
        "  } loop { i += 1; } until i == 4;\n"
        "  delocal int i = 4;\n"
        "}",
        {"g": 1},
    )
    eq(forward["acc"], 20)
    forward, _ = both_ways(
        "int g; int acc;\n"
        "proc add(int out, int k) { out += k * 2; }\n"
        "proc main() {\n"
        "  local int i = 0;\n"
        "  from i == 0 do {\n"
        "    if g > 0 { call add(acc, i); } else { uncall add(acc, i); } fi g > 0;\n"
        "  } loop { i += 1; } until i == 4;\n"
        "  delocal int i = 4;\n"
        "}",
        {"g": 0},
    )
    eq(forward["acc"], -20)


def test_a_local_array_used_as_scratch_across_a_call():
    forward, _ = both_ways(
        "int acc;\n"
        "proc fill(int xs[]) {\n"
        "  local int i = 0;\n"
        "  from i == 0 do { xs[i] += i * i; } loop { i += 1; } until i == len(xs) - 1;\n"
        "  delocal int i = len(xs) - 1;\n"
        "}\n"
        "proc main() {\n"
        "  local int buf[5];\n"
        "  call fill(buf);\n"
        "  acc += buf[4];\n"
        "  uncall fill(buf);\n"
        "  delocal int buf[5];\n"
        "}"
    )
    eq(forward["acc"], 16)


def test_constant_arguments_inside_recursion():
    forward, _ = both_ways(
        "int n; int acc;\n"
        "proc add_scaled(int out, int k, int scale) { out += k * scale; }\n"
        "proc walk(int k, int out) {\n"
        "  call add_scaled(out, k, 3);\n"
        "  if k > 0 { k -= 1; call walk(k, out); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call walk(n, acc); }",
        {"n": 4},
    )
    eq(forward["acc"], 3 * sum(range(5)))


def test_output_built_across_procedures():
    forward, output = both_ways(
        'int xs[3];\n'
        'proc row(int a[]) {\n'
        '  local int i = 0;\n'
        '  from i == 0 do { write a[i], " "; } loop { i += 1; }'
        ' until i == len(a) - 1;\n'
        '  delocal int i = len(a) - 1;\n'
        '  print "|";\n'
        '}\n'
        'proc main() { call row(xs); call row(xs); }',
        {"xs": [1, 2, 3]},
    )
    eq(output, ["1 2 3 |", "1 2 3 |"])
