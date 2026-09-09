# Reverie

A reversible programming language: a compiler, a bidirectional bytecode
machine, a time-travel debugger, and the tests that hold them to it. Python
3.11+, standard library only, no dependencies anywhere including the tests.

Start with `README.md`, then `docs/DESIGN.md` for why the pieces are shaped the
way they are.

## Layout

```
reverie/     the language: lexer parser ast printer checker compiler rir
             isa vm inverter debugger trace viz cli
stdlib/      array, math, bits, sort — written in Reverie
examples/    thirteen programs, each making one point
tests/       a dependency-free runner, a random program generator, ~730 cases
tools/       coverage, benchmarks, and the showcase page builder
docs/        tutorial, language reference, ISA, design notes
```

## Working on it

```bash
python3 tests/run_tests.py                    # everything, ~16 s
python3 tests/run_tests.py -k checker         # one module (comma-separated ok)
python3 tests/run_tests.py --slow --repeat 5  # the property tests, harder
python3 tests/run_tests.py --seed 1234        # reproduce a fuzz failure
python3 tools/coverage.py --show vm.py        # what is untested
python3 tools/bench.py                        # what it costs
./rev run examples/turing.rev                 # the CLI
```

Golden files under `tests/golden/` are regenerated with
`REVERIE_UPDATE_GOLDEN=1 python3 tests/run_tests.py -k printer`.

## The invariants

Anything touching `reverie/isa.py` or `reverie/vm.py` has to hold these, and
the tests will say so:

1. **Every instruction has an exact inverse.** Not just over a whole run —
   from *every* reachable point, one step back and one step forward must give
   bit-for-bit the same state and program counter. That is
   `test_every_single_step_is_exactly_invertible`, and it is what caught the
   three real bugs recorded in `docs/DESIGN.md`. Round-tripping a whole
   program is much weaker: errors that cancel survive it.
2. **`pc` is a boundary index.** It names the point *between* two
   instructions, so forwards executes `code[pc]` and backwards executes
   `code[pc - 1]`. This is what makes reversing free. The cost is that every
   control instruction must let the instruction *below* a boundary identify
   every forward transition that could have landed there.
3. **Nothing is ever erased.** An instruction that cannot proceed without
   discarding information traps instead — `x *= 0`, an inexact `/=`, a `>>=`
   that would drop set bits, a `delocal` whose expression does not match.
4. **The four static rules** in `docs/LANGUAGE.md` are what make the whole
   thing provable. If a change needs one of them relaxed, that is a design
   change, not a bug fix.

`rev run --paranoid` checks (1) on any program as it runs, and every shipped
example is tested under it.

## Conventions

- Diagnostics carry a span and, where there is an obvious fix, a note that
  says it. One error per mistake: if a rejected declaration would cascade into
  complaints about its uses, suppress the cascade (see `Symbol.bad`).
- No input should produce a Python traceback — `tests/test_robustness.py`
  fuzzes the front end to make sure. Failures are `ReverieError` or
  `RuntimeFault`, never `ValueError` or `RecursionError`.
- Docs are executed, not just written: `tests/test_docs.py` parses every
  Reverie snippet and runs every `$ rev …` transcript, comparing line by line.
  Change the behaviour, change the transcript.
