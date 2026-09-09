"""Random inputs to real programs: a trap is fine, a crash is not.

A Reverie program is allowed to fail at run time -- an index out of range, a
loop assertion that does not hold, a division that is not exact. Those are
diagnostics with a program counter. What it must never do is fall out of the
machine as a Python exception.
"""

import os
import random

from framework import case, contains, eq, is_true, slow
from generator import generate
from support import EXAMPLES
from reverie.diagnostics import ReverieError, RuntimeFault
from reverie.vm import Machine, state_equal
from test_examples import INPUTS, build

REPEAT = int(os.environ.get("REVERIE_FUZZ_REPEAT", "1"))
BASE = int(os.environ.get("REVERIE_SEED", "0")) & 0xFFFF

PROGRAMS = sorted(INPUTS)


def random_state(rng, prog, wild: bool):
    """Random values for a program's globals."""
    out = {}
    for name, g in prog.globals.items():
        if g.kind == "stack":
            out[name] = [rng.randint(-20, 20) for _ in range(rng.randrange(0, 4))]
        elif g.kind == "array":
            out[name] = [_value(rng, wild) for _ in range(g.size)]
        else:
            out[name] = _value(rng, wild)
    return out


def _value(rng, wild: bool) -> int:
    if wild and rng.random() < 0.3:
        return rng.choice([-(2 ** 40), -1, 0, 1, 2 ** 40, 10 ** 12])
    return rng.randint(-30, 30)


def exercise(prog, initial, label, paranoid=False, max_steps=60_000):
    """Run, and if it completes, insist the reversal is exact.

    A random input can send a program somewhere very long-running; hitting the
    step limit is a clean trap and counts as a pass.
    """
    m = Machine(prog, mem_size=1 << 13, max_steps=max_steps, paranoid=paranoid)
    try:
        m.set_globals(initial)
    except (ValueError, KeyError):
        return "rejected"
    before = m.snapshot()
    try:
        m.start_forward().run()
    except RuntimeFault:
        return "trapped"
    except ReverieError:
        return "trapped"
    except RecursionError:
        raise AssertionError(f"{label}: RecursionError")
    except Exception as exc:  # noqa: BLE001 - that is the point
        raise AssertionError(f"{label}: {type(exc).__name__}: {exc}\n{initial}")
    try:
        m.start_backward().run()
    except RuntimeFault:
        raise AssertionError(f"{label}: a completed run failed to reverse\n{initial}")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"{label}: reversal raised {type(exc).__name__}: {exc}\n{initial}"
        )
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, f"{label}: {why}\n{initial}")
    return "completed"


for _name in PROGRAMS:

    def _make(name=_name):
        def test():
            prog = build(os.path.join(EXAMPLES, name))
            rng = random.Random(BASE + hash(name) % 9973)
            seen = set()
            for _ in range(12 * REPEAT):
                initial = random_state(rng, prog, wild=rng.random() < 0.4)
                seen.add(exercise(prog, initial, f"{name} {initial}"))
            is_true(seen, "nothing ran")

        test.__name__ = f"test_random_inputs_{name.replace('.rev', '')}"
        test.__doc__ = f"{name}: random globals either complete or trap cleanly."
        return test

    _fn = _make()
    globals()[_fn.__name__] = _fn


def test_the_shipped_inputs_all_complete():
    for name in PROGRAMS:
        prog = build(os.path.join(EXAMPLES, name))
        eq(exercise(prog, INPUTS[name], name, max_steps=20_000_000), "completed")


def test_random_inputs_reach_both_outcomes():
    """The fuzzer is only meaningful if some inputs really do trap."""
    outcomes = set()
    for name in PROGRAMS:
        prog = build(os.path.join(EXAMPLES, name))
        rng = random.Random(BASE + 77 + hash(name) % 500)
        for _ in range(10):
            outcomes.add(exercise(prog, random_state(rng, prog, wild=True), name))
    is_true("completed" in outcomes, "no random input ever completed")
    is_true("trapped" in outcomes, "no random input ever trapped")


@case(0)
@case(1)
def test_generated_programs_with_random_state(offset):
    from reverie.compiler import compile_text

    rng = random.Random(BASE + 5000 + offset)
    for i in range(20 * REPEAT):
        seed = BASE + 5000 + offset * 1000 + i
        prog = compile_text(generate(seed), f"gen{seed}.rev")
        exercise(prog, random_state(rng, prog, wild=True), f"gen{seed}")


@slow
def test_random_inputs_under_paranoid_mode():
    rng = random.Random(BASE + 9090)
    for name in PROGRAMS:
        prog = build(os.path.join(EXAMPLES, name))
        for _ in range(3 * REPEAT):
            exercise(
                prog, random_state(rng, prog, wild=False), name, paranoid=True
            )


def test_memory_is_never_smaller_than_the_globals():
    """A silly `mem_size` is raised to fit, rather than corrupting the store."""
    prog = build(os.path.join(EXAMPLES, "sorting.rev"))
    m = Machine(prog, mem_size=1)
    is_true(m.mem_size >= prog.n_globals + 64)
    m.set_globals(INPUTS["sorting.rev"])
    m.start_forward().run()
    m.check_clean()


def test_running_out_of_frame_space_traps():
    from reverie.compiler import compile_text

    src = (
        "int n;\n"
        "proc down(int k) {\n"
        "  if k > 0 { k -= 1; call down(k); k += 1; } else { skip; } fi k > 0;\n"
        "}\n"
        "proc main() { call down(n); }"
    )
    prog = compile_text(src, "deep.rev")
    prog.procs["down"].frame_size = 32
    m = Machine(prog, mem_size=200, max_steps=200_000)
    m.set_globals({"n": 100})
    try:
        m.start_forward().run()
    except RuntimeFault as exc:
        contains(str(exc), "out of memory")
        return
    raise AssertionError("expected an out-of-memory trap")


def test_a_step_limit_traps_rather_than_hanging():
    prog = build(os.path.join(EXAMPLES, "critters.rev"))
    m = Machine(prog, mem_size=1 << 13, max_steps=200)
    try:
        m.start_forward().run()
    except RuntimeFault as exc:
        contains(str(exc), "step limit")
        return
    raise AssertionError("expected a step-limit trap")
