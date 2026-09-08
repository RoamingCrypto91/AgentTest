"""Bennett's construction: classical code made reversible and garbage-free."""

import math

from framework import case, contains, eq, is_true, raises
from reverie.compiler import compile_text
from reverie.diagnostics import RuntimeFault
from reverie.vm import Machine, state_equal


def build(text):
    return compile_text(text, "t.rev")


def run_embed(text, initial=None, mem_size=4096):
    """Run, assert the tape drained and nothing leaked, then reverse."""
    prog = build(text)
    m = Machine(prog, mem_size=mem_size, max_steps=5_000_000)
    if initial:
        m.set_globals(initial)
    before = m.snapshot()
    m.start_forward().run()
    result = m.globals_dict()
    eq(len(m.history), 0, "the history tape was not drained")
    m.check_clean("after an embed")
    eq(m.bits_erased, 0)
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, why)
    return result, m


# ---------------------------------------------------------------------------
# the shape of the generated code
# ---------------------------------------------------------------------------


def test_embed_compiles_to_compute_copy_uncompute():
    prog = build("int x; proc main() { embed (x ^= a) { var a = 41 + 1; } }")
    body = [
        ins.render()
        for ins in prog.code[prog.procs["main"].entry_at : prog.procs["main"].exit_at]
    ]
    calls = [b for b in body if b.startswith(("call", "uncall"))]
    eq(len(calls), 2)
    is_true(calls[0].startswith("call __embed"), calls[0])
    is_true(calls[1].startswith("uncall __embed"), calls[1])
    copies = [b for b in body if "^=" in b and "upd" in b]
    eq(len(copies), 1)
    # and the two calls sandwich the copy
    eq(body.index(calls[0]) < body.index(copies[0]) < body.index(calls[1]), True)


def test_the_generated_procedure_is_listed():
    prog = build("int x; proc main() { embed (x ^= a) { var a = 1; } }")
    names = [n for n in prog.procs if n.startswith("__embed")]
    eq(len(names), 1)
    eq(prog.procs[names[0]].doc, "compute phase of an embed block")


# ---------------------------------------------------------------------------
# semantics
# ---------------------------------------------------------------------------


def test_a_constant_embed():
    result, _ = run_embed("int x; proc main() { embed (x ^= a) { var a = 41 + 1; } }")
    eq(result["x"], 42)


@case(0)
@case(1)
@case(2)
@case(3)
@case(15)
@case(16)
@case(17)
@case(9999)
@case(1000000)
def test_isqrt(n):
    result, m = run_embed(
        "int n; int root; int rem;\n"
        "proc main() { embed (root ^= r, rem ^= n - r * r) {\n"
        "  var r = 0;\n"
        "  while ((r + 1) * (r + 1) <= n) { r = r + 1; }\n"
        "} }",
        {"n": n},
    )
    eq(result["root"], math.isqrt(n))
    eq(result["rem"], n - math.isqrt(n) ** 2)


@case(0, 0, 0)
@case(12, 18, 6)
@case(-12, 18, 6)
@case(17, 5, 1)
@case(270, 192, 6)
def test_gcd(a, b, want):
    result, _ = run_embed(
        "int a; int b; int out;\n"
        "proc main() { embed (out ^= x) {\n"
        "  var x = abs(a); var y = abs(b); var t = 0;\n"
        "  while (y != 0) { t = x % y; x = y; y = t; }\n"
        "} }",
        {"a": a, "b": b},
    )
    eq(result["out"], want)


def test_classical_if_without_else():
    result, _ = run_embed(
        "int n; int out;\n"
        "proc main() { embed (out ^= v) { var v = n; if (v < 0) { v = 0 - v; } } }",
        {"n": -17},
    )
    eq(result["out"], 17)


def test_classical_for_loop():
    result, _ = run_embed(
        "int n; int out;\n"
        "proc main() { embed (out ^= s) {\n"
        "  var s = 0;\n"
        "  for (var i = 1; i <= n; i = i + 1) { s = s + i * i; }\n"
        "} }",
        {"n": 10},
    )
    eq(result["out"], sum(i * i for i in range(1, 11)))


def test_classical_arrays():
    result, _ = run_embed(
        "int out;\n"
        "proc main() { embed (out ^= t[3]) {\n"
        "  var t[8];\n"
        "  var i = 0;\n"
        "  while (i < 8) { t[i] = i * i; i = i + 1; }\n"
        "} }"
    )
    eq(result["out"], 9)


def test_nested_classical_loops():
    result, _ = run_embed(
        "int out;\n"
        "proc main() { embed (out ^= total) {\n"
        "  var total = 0; var i = 0; var j = 0;\n"
        "  while (i < 5) {\n"
        "    j = 0;\n"
        "    while (j < 4) { total = total + i * j; j = j + 1; }\n"
        "    i = i + 1;\n"
        "  }\n"
        "} }"
    )
    eq(result["out"], sum(i * j for i in range(5) for j in range(4)))


def test_a_loop_that_never_runs():
    result, m = run_embed(
        "int out;\n"
        "proc main() { embed (out ^= n) { var n = 5; while (n > 100) { n = n + 1; } } }"
    )
    eq(result["out"], 5)


def test_multiple_bindings():
    result, _ = run_embed(
        "int n; int q; int r;\n"
        "proc main() { embed (q ^= a / 7, r ^= a % 7) { var a = n * 3; } }",
        {"n": 11},
    )
    eq((result["q"], result["r"]), (33 // 7, 33 % 7))


def test_addition_bindings_accumulate():
    result, _ = run_embed(
        "int acc;\n"
        "proc main() { embed (acc += a) { var a = 10; } embed (acc += a) { var a = 5; } }"
    )
    eq(result["acc"], 15)


def test_embed_reads_enclosing_arrays():
    result, _ = run_embed(
        "int xs[5]; int out;\n"
        "proc main() { embed (out ^= best) {\n"
        "  var best = xs[0]; var i = 1;\n"
        "  while (i < len(xs)) { if (xs[i] > best) { best = xs[i]; } i = i + 1; }\n"
        "} }",
        {"xs": [3, 9, 2, 7, 5]},
    )
    eq(result["out"], 9)


def test_embed_inside_a_procedure_passes_locals_as_parameters():
    result, _ = run_embed(
        "int out;\n"
        "proc work(int seed, int acc) { embed (acc ^= v) { var v = seed * seed; } }\n"
        "proc main() { local int s = 12; call work(s, out); delocal int s = 12; }"
    )
    eq(result["out"], 144)


def test_embed_inside_a_loop():
    result, _ = run_embed(
        "int total;\n"
        "proc main() {\n"
        "  local int i = 0;\n"
        "  from i == 0 do {\n"
        "    embed (total += sq) { var sq = i * i; }\n"
        "  } loop { i += 1; } until i == 5;\n"
        "  delocal int i = 5;\n"
        "}"
    )
    eq(result["total"], sum(i * i for i in range(6)))


def test_undo_of_an_embed_removes_its_contribution():
    result, _ = run_embed(
        "int out;\n"
        "proc main() { embed (out += a) { var a = 7; } undo { embed (out += a) { var a = 7; } } }"
    )
    eq(result["out"], 0)


def test_xor_embeds_are_their_own_inverse():
    result, _ = run_embed(
        "int out;\n"
        "proc main() { embed (out ^= a) { var a = 7; } embed (out ^= a) { var a = 7; } }"
    )
    eq(result["out"], 0)


# ---------------------------------------------------------------------------
# the tape
# ---------------------------------------------------------------------------


def test_the_tape_grows_and_then_drains_completely():
    prog = build(
        "int n; int out;\n"
        "proc main() { embed (out ^= r) { var r = 0; while (r < n) { r = r + 1; } } }"
    )
    m = Machine(prog, mem_size=4096, max_steps=1_000_000)
    m.set_globals({"n": 200})
    m.start_forward().run()
    eq(len(m.history), 0)
    is_true(m.stats.history_peak > 200, f"peak was only {m.stats.history_peak}")
    eq(m.stats.history_pushes, m.stats.history_pops)
    is_true(m.stats.history_bits > 0)
    eq(m.bits_erased, 0)


def test_the_tape_is_empty_between_top_level_embeds():
    prog = build(
        "int out;\n"
        "proc main() {\n"
        "  embed (out ^= a) { var a = 0; while (a < 20) { a = a + 1; } }\n"
        "  embed (out ^= b) { var b = 0; while (b < 20) { b = b + 1; } }\n"
        "}"
    )
    m = Machine(prog, mem_size=4096)
    m.start_forward()
    seen_empty_between = 0
    while m.step():
        if not m.history and m.frames and len(m.frames) == 1:
            seen_empty_between += 1
    eq(len(m.history), 0)
    is_true(seen_empty_between > 0)


def test_classical_arrays_are_restored_to_zero():
    prog = build(
        "int out;\n"
        "proc main() { embed (out ^= t[2]) { var t[4]; var i = 0;\n"
        "  while (i < 4) { t[i] = i + 10; i = i + 1; } } }"
    )
    m = Machine(prog, mem_size=256)
    m.start_forward().run()
    m.check_clean()
    eq(m.globals_dict()["out"], 12)
