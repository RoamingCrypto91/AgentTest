"""Every shipped program is checked, run, and proved reversible."""

import os

from framework import case, contains, eq, is_true, slow
from support import EXAMPLES, ROOT, STDLIB
from reverie.checker import analyze
from reverie.cli import load_module
from reverie.compiler import Compiler
from reverie.printer import print_module
from reverie.parser import parse_text
from reverie.vm import Machine, state_equal


def example_names():
    return sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".rev"))


def stdlib_names():
    return sorted(f for f in os.listdir(STDLIB) if f.endswith(".rev"))


def build(path, require_main=True):
    module = load_module(path)
    a = analyze(module, require_main=require_main)
    is_true(a.ok, f"{path}:\n{a.diagnostics.render()}")
    if not require_main and module.find_proc("main") is None:
        return None
    return Compiler(module, a).compile()


#: initial state each example expects
INPUTS = {
    "first.rev": {"n": 21},
    "tour.rev": {"n": 1000000},
    "countdown.rev": {"n": 7},
    "fibonacci.rev": {"n": 9},
    "rle.rev": {"data": [1, 1, 1, 4, 4, 7, 7, 7, 7, 7, 2, 2, 9, 9, 9, 3]},
    "sorting.rev": {"xs": [5, 3, 9, 1, 7, 2, 8, 4]},
    "cipher.rev": {"plaintext": [1000, 2000, 3000, 4000, 5000, 6000]},
    "arrays.rev": {"xs": [3, 1, 4, 1, 5, 9, 2, 6]},
    "graycode.rev": {"value": 0b10110011},
    "embedding.rev": {"n": 1234567},
    "primes.rev": {},
    "hanoi.rev": {},
    "critters.rev": {},
    "turing.rev": {},
}


def _run_example(name):
    path = os.path.join(EXAMPLES, name)
    prog = build(path)
    m = Machine(prog, mem_size=1 << 14, max_steps=20_000_000)
    m.set_globals(INPUTS.get(name, {}))
    before = m.snapshot()
    m.start_forward().run()
    forward = m.globals_dict()
    output = list(m.output)
    m.check_clean(name)
    eq(m.bits_erased, 0)
    steps = m.position
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, f"{name}: {why}")
    eq(m.position, 0, f"{name}: reversal length differs")
    return forward, output, steps


# One test per example, generated so a new file in examples/ shows up as its
# own case rather than disappearing into a loop.
for _name in sorted(INPUTS):

    def _make(name=_name):
        def test():
            _run_example(name)

        test.__name__ = f"test_example_{name.replace('.rev', '')}"
        test.__doc__ = f"{name}: runs, ends clean, and reverses exactly."
        return test

    _fn = _make()
    globals()[_fn.__name__] = _fn


def test_every_example_is_covered_by_a_case():
    missing = set(example_names()) - set(INPUTS)
    eq(missing, set(), f"examples with no test input: {sorted(missing)}")


def test_every_stdlib_module_checks():
    for name in stdlib_names():
        build(os.path.join(STDLIB, name), require_main=False)


def test_every_shipped_file_is_already_formatted():
    for folder in (EXAMPLES, STDLIB):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".rev"):
                continue
            path = os.path.join(folder, name)
            with open(path) as fh:
                text = fh.read()
            once = print_module(parse_text(text, name))
            twice = print_module(parse_text(once, name))
            eq(twice, once, f"{name}: the formatter is not a fixed point")


# ---------------------------------------------------------------------------
# what the examples are supposed to demonstrate
# ---------------------------------------------------------------------------


def test_fibonacci_inverts_itself():
    forward, output, _ = _run_example("fibonacci.rev")
    eq((forward["f1"], forward["f2"]), (55, 89))  # n = 9
    eq(forward["n"], 0)
    eq(forward["index"], 10)  # recovered from the pair (89, 144)
    eq((forward["probe1"], forward["probe2"]), (0, 0))


def test_rle_drains_its_input():
    forward, _, _ = _run_example("rle.rev")
    eq(forward["data"], [0] * 16)
    eq(forward["outlen"], 12)
    eq(forward["out"][:12], [1, 3, 4, 2, 7, 5, 2, 2, 9, 3, 3, 1])


def test_sorting_unsorts_with_an_empty_trail():
    forward, output, _ = _run_example("sorting.rev")
    eq(forward["xs"], [5, 3, 9, 1, 7, 2, 8, 4])
    eq(forward["trail"], [])
    contains(output[3], "1 2 3 4 5 7 8 9")


def test_cipher_decrypts_by_uncalling():
    forward, output, _ = _run_example("cipher.rev")
    eq(forward["ciphertext"], forward["plaintext"])
    is_true(output[0] != output[1], "the ciphertext should not equal the plaintext")


def test_critters_runs_time_backwards():
    _, output, _ = _run_example("critters.rev")
    marker = output.index("-- now run the automaton backwards --")
    forwards = output[:marker]
    backwards = output[marker + 1 :]
    eq(backwards, list(reversed(forwards)))


def test_hanoi_solves_and_unsolves():
    forward, output, _ = _run_example("hanoi.rev")
    eq(forward["a"], [4, 3, 2, 1])
    eq(forward["c"], [])
    eq(forward["moves"], [])
    contains(output[1], "in 15 moves")


def test_primes_finds_the_right_ones():
    forward, output, _ = _run_example("primes.rev")
    found = [i for i, f in enumerate(forward["flags"]) if f]
    eq(found, [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47])
    eq(forward["count"], 15)


def test_embedding_reports_correct_answers():
    forward, _, _ = _run_example("embedding.rev")
    import math

    n = 1234567
    eq(forward["root"], math.isqrt(n))
    eq(forward["rem"], n - math.isqrt(n) ** 2)
    eq(forward["digits"], n.bit_length() - 1)
    eq(forward["divisor"], math.gcd(n, 1234500))


def test_graycode_is_a_bijection():
    forward, _, _ = _run_example("graycode.rev")
    value = 0b10110011
    eq(forward["coded"], value ^ (value >> 1))
    eq(forward["ones"], bin(value).count("1"))
    eq(forward["mirrored"], int(format(value, "08b")[::-1], 2))


def test_arrays_library_demo():
    forward, output, _ = _run_example("arrays.rev")
    xs = [3, 1, 4, 1, 5, 9, 2, 6]
    eq(forward["total"], sum(xs))
    eq(forward["biggest"], max(xs))
    got = 0
    for v in xs:
        got ^= v
    eq(forward["checksum"], got)
    eq(forward["xs"], list(reversed(xs)))


def test_tour_computes_an_integer_square_root():
    import math

    forward, _, _ = _run_example("tour.rev")
    eq(forward["root"], math.isqrt(1000000))
    eq(forward["rem"], 0)


def test_first_doubles():
    forward, _, _ = _run_example("first.rev")
    eq(forward["total"], 42)


def test_turing_counts_up_and_back_down():
    forward, output, _ = _run_example("turing.rev")
    eq(forward["tape"], [0] * 6)
    eq(forward["trail"], [])
    eq(forward["lengths"], [])
    eq(forward["state"], 0)
    values = [int(l.split("=")[1]) for l in output if "=" in l]
    eq(values, list(range(13)) + list(range(12, -1, -1)))
    contains("\n".join(output), "44 rule numbers")
    contains(output[-1], "empty again: 0")


def test_countdown_sums():
    forward, output, _ = _run_example("countdown.rev")
    eq(forward["total"], sum(range(8)))
    contains(output[0], "= 28")
