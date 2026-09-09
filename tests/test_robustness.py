"""No input should ever produce a Python traceback.

Whatever you feed the front end -- random bytes, a valid program with a
character knocked out, five thousand nested parentheses -- the answer must be
either a compiled program or a `ReverieError` with a span. A stack trace is a
bug in the compiler, not in the input.
"""

import os
import random
import string

from framework import case, contains, eq, is_true, slow
from generator import generate
from support import EXAMPLES, ROOT, STDLIB
from reverie.checker import analyze
from reverie.compiler import Compiler
from reverie.diagnostics import ReverieError, RuntimeFault
from reverie.parser import parse_text
from reverie.printer import print_module

REPEAT = int(os.environ.get("REVERIE_FUZZ_REPEAT", "1"))
BASE = int(os.environ.get("REVERIE_SEED", "0")) & 0xFFFF


def front_end(text: str, name: str = "fuzz.rev"):
    """Parse, check and compile; any failure must be a ReverieError."""
    try:
        module = parse_text(text, name)
    except ReverieError:
        return None
    try:
        print_module(module)
    except ReverieError:
        return None
    a = analyze(module, require_main=False)
    if not a.ok or module.find_proc("main") is None:
        return None
    return Compiler(module, a).compile()


def survives(text: str, label: str) -> None:
    try:
        front_end(text)
    except ReverieError:
        pass
    except RecursionError:
        raise AssertionError(f"{label}: RecursionError -- the stack ran out")
    except Exception as exc:  # noqa: BLE001 - that is the point
        raise AssertionError(
            f"{label}: {type(exc).__name__}: {exc}\n--- input ---\n{text[:400]}"
        )


# ---------------------------------------------------------------------------
# pathological shapes
# ---------------------------------------------------------------------------

DEEP = {
    "nested parentheses": "int x; proc main() { x += " + "(" * 5000 + "1" + ")" * 5000 + "; }",
    "long sum": "int x; proc main() { x += " + " + ".join(["1"] * 20000) + "; }",
    "unary chain": "int x; proc main() { x += " + "-" * 5000 + "1; }",
    "nested indexing": "int a[2]; proc main() { a[0] += " + "a[" * 3000 + "0" + "]" * 3000 + "; }",
    "nested builtins": "int x; proc main() { x += " + "min(1," * 3000 + "1" + ")" * 3000 + "; }",
    "nested blocks": "int x; proc main() { " + "{ " * 5000 + "x += 1;" + " }" * 5000 + " }",
    "nested conditionals": "int x; int g; proc main() { "
                           + "if g > 0 { " * 2000 + "x += 1;" + " } fi; " * 2000 + " }",
    "nested loops": "int x; int g; proc main() { "
                    + "from g == 0 do { " * 2000 + "x += 1;" + " } until g == 1; " * 2000 + " }",
    "nested classical": "int x; proc main() { embed (x ^= a) { var a = 1; "
                        + "while (a < 0) { " * 3000 + "a = a + 1;" + " }" * 3000 + " } }",
    "unterminated block": "int x; proc main() {" + " { " * 4000,
    "unterminated string": 'int x; proc main() { print "' + "a" * 10000,
    "unterminated comment": "/*" + "a" * 10000,
    "huge literal": "int x; proc main() { x += " + "9" * 5000 + "; }",
    "huge exponent": "int x; proc main() { x += 2 ** " + "9" * 40 + "; }",
    "many declarations": "".join(f"int v{i};\n" for i in range(5000)) + "proc main() { skip; }",
    "many statements": "int x; proc main() {\n" + "    x += 1;\n" * 20000 + "}",
    "many procedures": "".join(f"proc p{i}() {{ skip; }}\n" for i in range(2000))
                       + "proc main() { skip; }",
}


@case(*[[k] for k in []])
def _noop():  # pragma: no cover
    pass


for _label in DEEP:

    def _make(label=_label):
        def test():
            survives(DEEP[label], label)

        test.__name__ = "test_survives_" + label.replace(" ", "_")
        test.__doc__ = f"{label}: a diagnostic, not a crash."
        return test

    _fn = _make()
    globals()[_fn.__name__] = _fn


def test_deep_input_is_rejected_with_a_span():
    from reverie.parser import MAX_DEPTH

    try:
        parse_text(DEEP["nested parentheses"], "deep.rev")
        raise AssertionError("expected a depth error")
    except ReverieError as exc:
        contains(exc.message, f"more than {MAX_DEPTH} deep")
        is_true(exc.span is not None, "the error should point somewhere")
        contains(exc.render(), "deep.rev:")


def test_input_just_under_the_limit_still_works():
    from reverie.parser import MAX_DEPTH

    n = MAX_DEPTH - 4
    src = "int x; proc main() { x += " + "(" * n + "1" + ")" * n + "; }"
    prog = front_end(src)
    is_true(prog is not None, "a legal depth should compile")


def test_a_long_flat_program_still_works():
    prog = front_end(DEEP["many statements"])
    is_true(prog is not None)
    is_true(len(prog.code) > 20000)


# ---------------------------------------------------------------------------
# random and mutated input
# ---------------------------------------------------------------------------

ALPHABET = string.ascii_letters + string.digits + " \n\t{}()[];,+-*/%^&|!~<>=\"'.:_#@$`\\"


@case(0)
@case(1)
@case(2)
def test_random_bytes(offset):
    rng = random.Random(BASE + offset)
    for _ in range(200 * REPEAT):
        n = rng.randrange(0, 300)
        text = "".join(rng.choice(ALPHABET) for _ in range(n))
        survives(text, f"random {text[:40]!r}")


def _sources():
    out = []
    for folder in (EXAMPLES, STDLIB):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".rev"):
                with open(os.path.join(folder, name)) as fh:
                    out.append((name, fh.read()))
    for seed in range(4):
        out.append((f"generated{seed}", generate(BASE + seed)))
    return out


SOURCES = _sources()


@case(0)
@case(1)
@case(2)
@case(3)
def test_mutated_programs(offset):
    """Knock a character out of a real program and see what happens."""
    rng = random.Random(BASE + 1000 + offset)
    for _ in range(150 * REPEAT):
        name, src = rng.choice(SOURCES)
        text = list(src)
        for _ in range(rng.randint(1, 4)):
            if not text:
                break
            i = rng.randrange(len(text))
            roll = rng.random()
            if roll < 0.4:
                del text[i]
            elif roll < 0.7:
                text.insert(i, rng.choice(ALPHABET))
            else:
                text[i] = rng.choice(ALPHABET)
        survives("".join(text), f"mutated {name}")


@case(0)
@case(1)
def test_truncated_programs(offset):
    """Every prefix of every shipped program must fail cleanly."""
    rng = random.Random(BASE + 2000 + offset)
    for _ in range(200 * REPEAT):
        name, src = rng.choice(SOURCES)
        survives(src[: rng.randrange(len(src) + 1)], f"truncated {name}")


def test_every_prefix_of_one_program():
    with open(os.path.join(EXAMPLES, "rle.rev")) as fh:
        src = fh.read()
    for i in range(0, len(src), 7):
        survives(src[:i], f"rle.rev[:{i}]")


@slow
@case(0)
@case(1)
def test_heavy_mutation(offset):
    rng = random.Random(BASE + 3000 + offset)
    for _ in range(min(2000 * REPEAT, 6000)):
        name, src = rng.choice(SOURCES)
        text = list(src)
        for _ in range(rng.randint(1, 30)):
            if not text:
                break
            i = rng.randrange(len(text))
            roll = rng.random()
            if roll < 0.35:
                del text[i]
            elif roll < 0.7:
                text.insert(i, rng.choice(ALPHABET))
            else:
                text[i] = rng.choice(ALPHABET)
        survives("".join(text), f"heavily mutated {name}")


# ---------------------------------------------------------------------------
# diagnostics have to stay readable whatever the input looks like
# ---------------------------------------------------------------------------


def test_a_long_line_is_windowed_around_the_caret():
    from reverie.diagnostics import LINE_WINDOW

    src = "int x; proc main() { x += " + "1 + " * 4000 + "1; }"
    try:
        parse_text(src, "wide.rev")
        raise AssertionError("expected a depth error")
    except ReverieError as exc:
        rendered = exc.render()
        is_true(
            len(rendered) < 20 * LINE_WINDOW,
            f"the diagnostic is {len(rendered)} characters long",
        )
        contains(rendered, "...")
        for line in rendered.splitlines():
            is_true(len(line) < 6 * LINE_WINDOW, f"line too long: {len(line)}")


def test_a_huge_literal_is_diagnosed():
    src = "int x; proc main() { x += " + "9" * 500_000 + "; }"
    try:
        parse_text(src, "huge.rev")
        raise AssertionError("expected a literal error")
    except ReverieError as exc:
        contains(exc.message, "digits")
        is_true(len(exc.render()) < 2000)


def test_large_values_still_compute_and_print():
    from reverie.vm import Machine
    from reverie.compiler import compile_text

    src = (
        "int n; int out;\n"
        "proc main() {\n"
        "  embed (out += v) { var v = 1; var i = 0;\n"
        "    while (i < n) { v = v * 7; i = i + 1; } }\n"
        "  print out;\n"
        "}"
    )
    prog = compile_text(src, "big.rev")
    m = Machine(prog, mem_size=256, max_steps=1_000_000)
    m.set_globals({"n": 6000})
    m.start_forward().run()
    eq(len(m.output[0]), len(str(7 ** 6000)))
    eq(m.globals_dict()["out"], 7 ** 6000)


def test_windowing_keeps_short_lines_intact():
    from reverie.diagnostics import Source, Span, ReverieError as RE

    src = Source("t.rev", "int x;\nproc main() { x += x; }\n")
    err = RE("boom", Span(src, 20, 21), label="here")
    rendered = err.render()
    contains(rendered, "proc main() { x += x; }")
    is_true("..." not in rendered)
