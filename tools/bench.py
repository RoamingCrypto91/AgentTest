"""Benchmarks for the Reverie machine.

Reversible execution costs roughly what forward execution costs -- there is no
log to write and none to read back -- and the numbers below are here to keep
that true.  Run before and after touching the interpreter.

    python3 tools/bench.py
    python3 tools/bench.py --quick
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from reverie.checker import analyze  # noqa: E402
from reverie.cli import load_module  # noqa: E402
from reverie.compiler import Compiler, compile_text  # noqa: E402
from reverie.parser import parse_text  # noqa: E402
from reverie.vm import Machine  # noqa: E402

COUNT = """
int n;
int acc;
proc main() {
    local int i = 0;
    from i == 0 do {
        acc += i * 3;
    } loop {
        i += 1;
    } until i == n;
    delocal int i = n;
}
"""

CALLS = """
int n;
int acc;
int flag;
proc bump(int a, int b) { a += b * 2; }
proc twiddle(int a) { a ^= 5; }
proc main() {
    local int i = 0;
    from i == 0 do {
        call bump(acc, i);
        call twiddle(flag);
        uncall bump(acc, i);
        call bump(acc, i);
        uncall twiddle(flag);
    } loop {
        i += 1;
    } until i == n;
    delocal int i = n;
}
"""

EMBED = """
int n;
int out;
proc main() {
    embed (out ^= r) {
        var r = 0;
        while (r < n) { r = r + 1; }
    }
}
"""

ARRAYS = """
const N = 64;
int xs[N];
int acc;
proc main() {
    local int r = 0;
    from r == 0 do {
        local int i = 0;
        from i == 0 do {
            acc ^= xs[i] * 3;
        } loop {
            i += 1;
        } until i == N - 1;
        delocal int i = N - 1;
    } loop {
        r += 1;
    } until r == 20;
    delocal int r = 20;
}
"""

CASES = [
    ("straight-line updates", COUNT, {"n": 40_000}),
    ("call / uncall", CALLS, {"n": 8_000}),
    ("embed, tape-heavy", EMBED, {"n": 20_000}),
    ("array indexing", ARRAYS, {}),
]


def timed(fn, repeats: int = 3) -> float:
    """Best-of-n wall time for *fn*."""
    return min(_once(fn) for _ in range(repeats))


def _once(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def timed_phase(setup, run, repeats: int = 3) -> float:
    """Best-of-n time for *run*, with *setup* excluded from the clock."""
    best = []
    for _ in range(repeats):
        state = setup()
        best.append(_once(lambda: run(state)))
    return min(best)


def main() -> int:
    ap = argparse.ArgumentParser(description="benchmark the Reverie machine")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    repeats = 1 if args.quick else 3
    scale = 0.2 if args.quick else 1.0

    print(f"{'case':<28} {'steps':>9} {'forward':>10} {'backward':>10} "
          f"{'fwd steps/s':>13} {'bwd/fwd':>8}")
    print("-" * 84)
    rows = []
    for label, src, initial in CASES:
        initial = {k: int(v * scale) or 1 for k, v in initial.items()}
        prog = compile_text(src, label + ".rev")

        def make():
            m = Machine(prog, mem_size=1 << 12, max_steps=20_000_000)
            m.set_globals(initial)
            return m

        probe = make()
        probe.start_forward().run()
        steps = probe.position

        fwd = timed(lambda: make().start_forward().run(), repeats)

        def at_the_end():
            m = make()
            m.start_forward().run()
            return m

        bwd = timed_phase(at_the_end, lambda m: m.start_backward().run(), repeats)
        print(f"{label:<28} {steps:>9,} {fwd * 1000:>9.1f}ms {bwd * 1000:>9.1f}ms "
              f"{steps / fwd:>13,.0f} {bwd / fwd:>8.2f}")
        rows.append(bwd / fwd)

    print("-" * 84)
    print(f"backward costs {statistics.mean(rows):.2f}x forward on average "
          f"(1.00 would mean exactly the same work)")

    # front end
    print()
    src = open(os.path.join(ROOT, "stdlib", "array.rev")).read()
    text = src * 4
    parse = timed(lambda: parse_text(text, "bench.rev"), repeats)
    print(f"{'parse 4x stdlib/array.rev':<28} {len(text):>9,} chars "
          f"{parse * 1000:>7.1f}ms  {len(text) / parse / 1e6:>5.1f} MB/s")

    path = os.path.join(ROOT, "examples", "sorting.rev")

    def compile_once():
        module = load_module(path)
        a = analyze(module)
        Compiler(module, a).compile()

    comp = timed(compile_once, repeats)
    print(f"{'compile examples/sorting.rev':<28} {'':>9}       "
          f"{comp * 1000:>7.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
