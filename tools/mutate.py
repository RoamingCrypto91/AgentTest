"""Mutation testing for the machine.

A test suite that passes tells you the code does what the tests expect. It does
not tell you the tests would notice if the code were wrong. So: break the
machine on purpose, one instruction at a time, and check that something fails.

A *surviving* mutant -- a deliberate bug that nothing catches -- is a hole in
the tests, and this tool reports them.

    python3 tools/mutate.py                 # every instruction, every mutation
    python3 tools/mutate.py --only Fi       # one instruction
    python3 tools/mutate.py --full          # use the whole test suite as oracle
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from reverie import isa  # noqa: E402
from reverie.checker import analyze  # noqa: E402
from reverie.cli import load_module  # noqa: E402
from reverie.compiler import Compiler  # noqa: E402
from reverie.diagnostics import ReverieError  # noqa: E402
from reverie.vm import Machine, state_equal  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")

#: programs to run as the fast oracle, with the state they expect
from test_examples import INPUTS  # noqa: E402

# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------


def shift_pc(delta: int, which: str):
    """After the instruction runs, nudge the program counter."""

    def make(original):
        def mutated(self, m):
            original(self, m)
            m.pc += delta

        return mutated

    make.label = f"{which} leaves pc off by {delta:+d}"
    make.method = which
    return make


def skip_body(which: str):
    """Do nothing but the ordinary pc bookkeeping."""

    def make(original):
        def mutated(self, m):
            m.pc += 1 if which == "forward" else -1

        return mutated

    make.label = f"{which} does nothing"
    make.method = which
    return make


def swap_update_operator(original):
    """Undo an update with the same operator instead of its inverse."""

    def mutated(self, m):
        a = self.addr.resolve(m)
        m.mem[a] = isa._apply_update(self.op, m.mem[a], self.expr.eval(m), m)
        m.pc -= 1

    return mutated


swap_update_operator.label = "backward reuses the forward operator"
swap_update_operator.method = "backward"


def flip_predicate(which: str):
    """Take the other branch."""

    def make(original):
        def mutated(self, m):
            before = self.exit_cond if hasattr(self, "exit_cond") else self.cond

            class Flipped:
                def eval(_, machine):
                    return 0 if before.eval(machine) else 1

                def render(_):
                    return "!" + before.render()

            name = "exit_cond" if hasattr(self, "exit_cond") else "cond"
            saved = getattr(self, name)
            object.__setattr__(self, name, Flipped()) if False else setattr(
                self, name, Flipped()
            )
            try:
                original(self, m)
            finally:
                setattr(self, name, saved)

        return mutated

    make.label = f"{which} tests the opposite predicate"
    make.method = which
    return make


#: mutants that cannot be killed because they are not really mutations --
#: the replacement does exactly what the original did
EQUIVALENT = {
    # `nop` doing nothing is precisely what `nop` does
    ("Nop", "forward does nothing"),
    ("Nop", "backward does nothing"),
    # `cif` going backwards is only a pc move, and the replacement moves it
    # the same way
    ("CIf", "backward does nothing"),
}

MUTATIONS = [
    shift_pc(+1, "backward"),
    shift_pc(-1, "backward"),
    shift_pc(+1, "forward"),
    shift_pc(-1, "forward"),
    skip_body("backward"),
    skip_body("forward"),
]

#: instruction-specific mutations, keyed by class name
SPECIFIC = {
    "Update": [swap_update_operator],
    "Fi": [flip_predicate("backward")],
    "CFi": [],
    "Until": [flip_predicate("forward")],
    "If": [flip_predicate("forward")],
}


# ---------------------------------------------------------------------------
# oracles
# ---------------------------------------------------------------------------


def compile_example(name: str):
    module = load_module(os.path.join(EXAMPLES, name))
    a = analyze(module)
    a.diagnostics.raise_if_errors()
    return Compiler(module, a).compile()


class Oracle:
    """Runs a handful of programs and reports whether anything went wrong."""

    def __init__(self, names) -> None:
        self.programs = [(n, compile_example(n)) for n in names]
        self.expected = {}
        for name, prog in self.programs:
            m = Machine(prog, mem_size=1 << 13, max_steps=5_000_000)
            m.set_globals(INPUTS[name])
            m.start_forward().run()
            # the number of instructions executed is part of the contract, not
            # an implementation detail: it is what makes an extra no-op step
            # visible
            self.expected[name] = (
                m.globals_dict(),
                list(m.output),
                m.position,
                m.stats.steps,
            )

    def executed(self) -> set:
        """Which instruction classes the oracle actually runs."""
        seen = set()
        for name, prog in self.programs:
            m = Machine(prog, mem_size=1 << 13, max_steps=5_000_000)
            m.set_globals(INPUTS[name])
            m.trace = lambda _m, ins, seen=seen: seen.add(type(ins).__name__)
            m.start_forward().run()
            m.start_backward().run()
        return seen

    def check(self) -> bool:
        """True if everything still behaves; False if the mutant was caught."""
        for name, prog in self.programs:
            try:
                m = Machine(prog, mem_size=1 << 13, max_steps=200_000)
                m.set_globals(INPUTS[name])
                before = m.snapshot()
                m.start_forward().run()
                got = (m.globals_dict(), list(m.output), m.position, m.stats.steps)
                if got != self.expected[name]:
                    return False
                m.check_clean()
                steps = m.stats.steps
                m.start_backward().run()
                ok, _ = state_equal(before, m.snapshot())
                if not ok or m.position != 0 or m.stats.steps != 2 * steps:
                    return False
            except Exception:
                return False
        return True


def full_suite() -> bool:
    """Run the whole test suite as the oracle.  Slow but definitive."""
    from framework import _REGISTRY, main as run_tests

    _REGISTRY.clear()
    buf = io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        status = run_tests(["--no-color", "-x"])
    finally:
        sys.stdout, sys.stderr = old
    return status == 0


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="mutation-test the Reverie machine")
    ap.add_argument("--only", default="", help="one instruction class")
    ap.add_argument("--label", default="", help="one mutation, by its label")
    ap.add_argument("--full", action="store_true", help="use the whole test suite")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--min", type=float, default=0.0,
                    help="fail below this percentage of mutants caught")
    args = ap.parse_args()

    names = [n for n in sorted(INPUTS) if n not in ("critters.rev",)]
    oracle = Oracle(names)
    reached = oracle.executed()
    check = full_suite if args.full else oracle.check

    print(
        "oracle: "
        + ("the whole test suite" if args.full
           else f"{len(names)} example programs, run both ways")
    )
    classes = [
        cls
        for cls in isa.INSTRUCTIONS.values()
        if not args.only or cls.__name__ == args.only
    ]
    killed = survived = skipped = 0
    started = time.time()
    print(f"{'instruction':<14} {'mutation':<40} result")
    print("-" * 72)
    for cls in classes:
        name = cls.__name__
        if name not in reached:
            skipped += 1
            if not args.quiet:
                print(f"{name:<14} {'—':<40} not exercised by the oracle")
            continue
        for make in MUTATIONS + SPECIFIC.get(name, []):
            if args.label and args.label not in make.label:
                continue
            method = make.method
            original = getattr(cls, method, None)
            if original is None or getattr(cls, f"{method}_unreachable", False):
                continue
            setattr(cls, method, make(original))
            try:
                alive = check()
            finally:
                setattr(cls, method, original)
            if alive:
                if (name, make.label) in EQUIVALENT:
                    if not args.quiet:
                        print(f"{name:<14} {make.label:<40} equivalent")
                    continue
                survived += 1
                print(f"{name:<14} {make.label:<40} SURVIVED")
            else:
                killed += 1
                if not args.quiet:
                    print(f"{name:<14} {make.label:<40} caught")
    print("-" * 72)
    total = killed + survived
    score = 100.0 * killed / total if total else 100.0
    print(
        f"{killed} caught, {survived} survived, {skipped} instructions not "
        f"exercised — {score:.1f}% in {time.time() - started:.1f}s"
    )
    if not args.full and survived:
        print(
            "note: the fast oracle cannot reach assertions that only fire on\n"
            "      states a valid program never reaches.  `--full` uses the\n"
            "      whole test suite, which does."
        )
    if args.min:
        if score < args.min:
            print(f"\nmutation score {score:.1f}% is below the required {args.min:.1f}%")
            return 1
        return 0
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
