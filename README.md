# Reverie

**A programming language whose programs run backwards.**

Not "replay from a log" — there is no log. Every instruction has an exact
inverse, the compiler refuses to emit anything that would destroy information,
and running a program in reverse is those inverses executed in the opposite
order. The debugger's `goto 400` from step 900 does not consult a snapshot; it
executes five hundred inverse instructions. The state it lands on is not a
reconstruction of the state you were in. It *is* that state.

```console
$ rev run examples/fibonacci.rev --set n=10
n = 10
forwards:  fib -> (89, 144), and n is consumed to 0
backwards: given the pair (89, 144) ...
           the index is 10 and the pair is now (0, 0)
```

Nobody wrote the second program. `fib` maps an index to a Fibonacci pair;
`uncall fib` maps a Fibonacci pair to its index, because every statement in
`fib` has an inverse and the compiler knows all of them.

---

## Why this is not a toy

Reversible computing is a real corner of computer science with a physical
motivation. Landauer's principle says erasing a bit has a minimum energy cost
of *kT* ln 2, and a conventional processor erases enormously: every `x = y`
throws away whatever `x` held. A machine that never erases has no such floor.
Bennett showed in 1973 that any computation can be made reversible, and in 1989
how to do it without accumulating garbage.

Reverie implements that. `rev doctor` will tell you the bill:

```console
$ rev doctor examples/embedding.rev --set n=1234567
  logical time      8323 steps
  history tape      peak 1115 entries, 25818 bits written
  tape reclaimed    3038/3038 entries
  bits erased       0
  reversible        yes -- forward then backward is the identity
```

Twenty-five thousand bits went onto a tape so that a `while` loop and a
division could happen, and every one of them came back.

---

## The five ideas

### 1. A conditional carries two predicates

```reverie
if x > 3 {
    y += 100;
} else {
    y += 7;
} fi y > 50;
```

The test at the top chooses the branch on the way in. The predicate after `fi`
must be **true** when the then-branch finishes and **false** when the else-branch
does — which is exactly the information a reader of the reverse execution needs
in order to know which way control went. No branch history, no snapshots. Write
`fi;` and the entry test is reused, which the checker allows only when neither
branch can disturb it.

Loops work the same way: `from` carries an entry assertion, `until` an exit test.

### 2. An update may not read what it writes

`x += f(y)` is undone by `x -= f(y)`, which is only correct if `f(y)` evaluates
identically before and after. So `x += x * 2` is rejected — with a note
suggesting the `local` that fixes it. Four rules like this one, checked
statically, are enough to guarantee a whole program is injective.

### 3. Inversion is an operation, not a feature

```console
$ rev invert examples/countdown.rev
proc main() {
    unprint "sum 1..", n, " = ", total;
    local int i = n;
    from i == n do {
        total -= i;
    } loop {
        i -= 1;
    } until i == 0;
    delocal int i = 0;
}
```

A sequence inverts by reversing it and inverting each statement. A conditional
swaps its two predicates. A loop swaps its entry assertion with its exit test.
`call` and `uncall` swap, `local` and `delocal` swap, `print` becomes `unprint`
— because output is information too, and an inverted program consumes the log
the forward one produced.

The same transformation exists as a statement: `undo { ... }` runs a block
backwards, and the compiler implements it by calling `.invert()` on the IR it
just built.

### 4. `embed`: ordinary code, made reversible and garbage-free

Inside an `embed` block you write the code you would write anywhere else —
destructive `=`, `while`, `if`, temporaries you overwrite:

```reverie
embed (root ^= r, rem ^= n - r * r) {
    var r = 0;
    while ((r + 1) * (r + 1) <= n) {
        r = r + 1;
    }
}
```

The compiler turns that into Bennett's construction:

```
local temps…                      scratch, provably zero on both sides
call   __embed_1(inputs, temps)   compute — writes a history tape
root ^= temps[r]                  copy the answer out
uncall __embed_1(inputs, temps)   uncompute — drains the tape to empty
delocal temps…
```

The middle line is the only thing that survives. Everything the classical code
scribbled — intermediate values, loop counters, branch decisions — is put back
exactly where it was found. Watch it happen in `rev viz`: the tape fills, holds
while the answer is copied, and drains.

### 5. Time is a coordinate you can move along

```console
$ rev debug examples/sorting.rev --set xs=5,3,9,1,7,2,8,4
(rev) watch comparisons
(rev) break 33
(rev) run
breakpoint
[t=491  ] ->    26:33  delocal %0 = len(&xs)
  watch comparisons: 0 -> 49  (t = 494)
  watch comparisons: 49 -> 0  (t = 499)
(rev) goto 500
[t=500  ] ->~   27:33  delocal %0 = len(&xs)
(rev) back 120
  watch comparisons: 0 <- 49  (t = 498)
  watch comparisons: 49 <- 0  (t = 493)
[t=380  ] ->    14:21  nop
```

`step`, `back`, `goto`, `rewind`, breakpoints, watches — and watches fire in
both directions, because a variable changing is a change whichever way you are
travelling.

The `~` on the arrow at `t=500` is worth a second look. Logical time is still
running forwards there; the machine is inside `uncall sort`, executing a
procedure *against* the arrow. Those are two different things, and the
debugger shows both: the machine has one extra bit of state a conventional VM
lacks — a direction — and lacks one thing a conventional VM has: any way to
throw information away.

---

## Try it

```console
$ ./rev run    examples/critters.rev      # a cellular automaton that un-evolves
$ ./rev back   examples/rle.rev  --set data=1,1,1,4,4,7,7,7   # encode, then decode
$ ./rev doctor examples/primes.rev        # the thermodynamic bill
$ ./rev run    examples/hanoi.rev --paranoid   # check every step as it runs
$ ./rev viz    examples/sorting.rev --set xs=5,3,9,1,7,2,8,4 -o sorting.html
$ ./rev verify stdlib/sort.rev            # hunt for inputs it cannot undo
$ python3 tests/run_tests.py
```

No dependencies. Python 3.11 and the standard library, tests included —
8,000 lines for the language and 7,000 more for the tests that try to break
it.

### The examples

| file | what it shows |
| --- | --- |
| `first.rev` | the smallest complete program |
| `countdown.rev` | a loop, and the local that closes it |
| `tour.rev` | the worked example from the tutorial |
| `fibonacci.rev` | `uncall` inverts a function nobody wrote an inverse for |
| `rle.rev` | the encoder, run backwards, is the decoder |
| `sorting.rev` | sorting is not injective, so the permutation has to be kept |
| `cipher.rev` | a Feistel network; decryption is `uncall` |
| `critters.rev` | a reversible cellular automaton, evolved and un-evolved |
| `turing.rev` | a Turing machine, and the history that lets it be unrun |
| `hanoi.rev` | reversible recursion over three stacks |
| `embedding.rev` | destructive code made reversible and garbage-free |
| `primes.rev` | compute–copy–uncompute written out by hand |
| `graycode.rev` | in-place bit manipulation under the no-self-reference rule |
| `arrays.rev` | the array library, forwards and backwards |

---

## Testing

A reversible language admits a specification that needs no expected-output
files:

> running a program forwards and then backwards must return the machine to
> exactly the state it started in — every cell, every stack, every line of
> output, every entry of the history tape.

`tests/generator.py` writes random programs that are valid by construction, and
the properties check that identity, that `rev invert` really produces the
inverse, that the two independent inverters (syntax tree and structured IR)
agree, that formatting preserves behaviour, and that the debugger's `goto`
lands exactly where stepping would.

### The tests a program writes for itself

`rev verify` turns that specification on your own code. It builds a driver
around each procedure in a file, hands it random arguments, and checks that
running it forwards and then backwards restores the starting state, that
`call f` followed by `uncall f` changes nothing, and that the procedure
`rev invert` prints really undoes the original. No expected outputs, no
oracle, no test file — the procedure's own `fi` predicates and `delocal`
expressions are the specification, and the machine already checks them.

```console
$ ./rev verify examples/hanoi.rev --cases 12
```

A failing input is shrunk until no smaller one still fails, so what comes back
is a counterexample you can read and the line that rejected it. Procedures that
are only defined on part of their input space say so in a doc comment —
`requires:` for a condition, `given:` to pin an argument, `setup:` to build a
state no condition could describe — and the search respects it. Writing those
three lines for `stdlib/` turned up two undocumented preconditions and one
genuine interpreter bug: a negative shift count raised a Python error instead
of a trap.

The strongest property is per-step rather than end-to-end: from every reachable
point, step back and forward again and demand bit-for-bit equality of state
*and* program counter. Round-tripping a whole program only shows that the
composition is the identity, so errors that cancel survive it. Three did — see
`docs/DESIGN.md`.

```console
$ python3 tests/run_tests.py                 # 825 cases
$ python3 tests/run_tests.py --slow --repeat 8   # several thousand generated programs
$ python3 tests/run_tests.py --seed 1234     # reproduce a fuzz failure
$ python3 tools/coverage.py                  # 97% of reverie/, no dependencies
$ python3 tools/mutate.py                    # would the tests notice if it broke?
$ python3 tools/bench.py                     # and what it costs
```

There is also `rev run --paranoid`, which holds *any* program to the per-step
property as it runs: take the step, undo it, check nothing moved, redo it.
Every shipped example is tested under it.

And because a passing suite only tells you the code does what the tests
expect, `tools/mutate.py` breaks the machine on purpose — one instruction at a
time — and reports any deliberate bug that nothing catches. It found two: an
oracle that compared final state but not how much work it took, which let a
mutant slip in an extra step. The number of instructions executed is part of
the machine's contract, so it is compared now.

Reversal is not a slow path. About 1.4 million instructions a second in
CPython, with backward execution within noise of forward — there is no log to
write on the way out and none to read on the way back:

```
case                             steps    forward   backward   bwd/fwd
straight-line updates          160,007     112.4ms     102.8ms      0.91
call / uncall                  144,021     143.3ms     144.1ms      1.01
embed, tape-heavy              120,019      77.2ms      78.2ms      1.01
```

---

## Documentation

- **[docs/TUTORIAL.md](docs/TUTORIAL.md)** — write your first reversible program
- **[docs/LANGUAGE.md](docs/LANGUAGE.md)** — the full language reference
- **[docs/ISA.md](docs/ISA.md)** — the instruction set, and why control flow is paired
- **[docs/DESIGN.md](docs/DESIGN.md)** — architecture, the theory, and the bugs

## Layout

```
reverie/
  lexer.py parser.py ast.py printer.py   surface syntax
  checker.py                             the static proof that a program is injective
  compiler.py rir.py                     lowering, and inversion as an IR operation
  isa.py vm.py                           the bidirectional machine
  inverter.py                            source-to-source program inversion
  debugger.py trace.py viz.py            stepping and watching
  verify.py                              the search for inputs it cannot undo
  cli.py                                 rev
stdlib/    array, math, bits, sort
examples/  fourteen programs
tests/     a dependency-free runner, a program generator, 825 cases
tools/     coverage, benchmarks, and the page builder
```

## Prior art

The surface language owes its shape to **Janus** (Lutz & Derby, 1986) — the
paired conditional and the `from … until` loop are theirs, and they remain the
clearest solution anyone has found to reversible control flow. `embed` is
**Bennett's** compute–copy–uncompute (1973, 1989). The thermodynamic framing is
**Landauer's** (1961).

What is new here is the assembly: a real bidirectional bytecode machine where
the program counter is a boundary index so reversing time is two sign flips; a
compiler that exposes inversion as both a statement and a command-line verb; an
`embed` construct that lets destructive code and reversible code share a
program; a search that holds any procedure to its own stated domain; and a
test methodology that turns reversibility itself into the oracle.
