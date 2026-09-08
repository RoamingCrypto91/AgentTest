# Design notes

Why the pieces are shaped the way they are, and what went wrong on the way.

---

## The premise

Landauer's principle (1961) puts a floor under the energy cost of *erasing*
information: *kT* ln 2 per bit, and nothing else in computation has a
thermodynamic floor at all. Bennett (1973) showed the floor is avoidable —
every computation can be rearranged so that nothing is ever erased — and later
(1989) how to do it without the rearrangement filling memory with garbage.

That is a physical result, but it has a linguistic shadow. A language in which
nothing is erased cannot have `x = y`. It has to be built differently from the
bottom, and the interesting question is what the difference looks like in
practice. Reverie is an answer to that question you can run.

---

## Pipeline

```
source
  ├─ lexer      tokens, with spans
  ├─ parser     AST
  ├─ checker    names, storage layout, the four reversibility rules
  ├─ compiler   AST → RIR
  │              RIR.invert()  ← what `undo` compiles to
  ├─ rir        RIR → flat bytecode, with paired control instructions
  └─ vm         forwards and backwards
```

Two things sit deliberately outside that line.

**`inverter.py`** transforms the *syntax tree*, not the IR, and is what
`rev invert` prints. It shares no code with the IR inverter — which is precisely
why the two can be cross-checked, and
`test_ir_inversion_agrees_with_source_inversion` does.

**`trace.py` / `viz.py`** record deltas. The machine does not need them; a
viewer that is not the machine does.

---

## Decisions

### The IR is a tree, not a graph

Flat bytecode is what the machine runs, but it is the wrong place to *invert* a
program: once control flow is flattened into paired jump targets, undoing it
means re-deriving the block structure. So there is a structured layer in
between, `rir.py`, on which inversion is four lines of insight:

| | |
| --- | --- |
| `S1 ; S2` | `inv(S2) ; inv(S1)` |
| `x += e` | `x -= e` |
| `if a then S else T fi b` | `if b then inv(S) else inv(T) fi a` |
| `from a do S loop T until b` | `from b do inv(S) loop inv(T) until a` |
| `call p` / `uncall p` | each other |

`undo { … }` is implemented by calling `.invert()` on the IR the compiler just
built. Inversion is not a feature bolted onto the language; it is an operation
on the IR that the language happens to expose in three places.

### The program counter is a boundary index

`pc == k` means "between instruction `k-1` and instruction `k`". Forwards
executes `code[pc]`, backwards executes `code[pc-1]`. Both directions agree
about what `pc` means, so `reverse()` is:

```python
self.dir = -self.dir
self.arrow = -self.arrow
```

Nothing else. The alternative — `pc` naming "the next instruction in the current
direction" — requires remembering the previous instruction in order to flip,
which is a saved bit of history in a machine whose entire claim is that it has
none.

The cost is a design constraint on every control instruction: for each boundary,
the instruction below it must be able to identify every forward transition that
could have landed there. A conditional's `fi` distinguishes its two predecessors
with the exit predicate; a loop's `from` distinguishes its two with the entry
assertion. This is why those predicates exist at all — not as a syntactic quirk
inherited from Janus, but as the local information a boundary needs.

### Frames record the call site, not a return address

Because you can enter a procedure going forwards and leave it going backwards,
"where to return to" is not a single address. It is a function of which end you
walk out of and whether you came in through `call` or `uncall`. The frame stores
the call site and the flag; the table in [ISA.md](ISA.md) does the rest.

### Parameter addresses live in the control stack

Arguments are passed by reference, so the callee's frame has to hold the
caller's addresses. Putting them in the *store* would mean zeroing them on the
way out, which is an erasure. Putting them in the frame record — control state,
balanced by construction — costs nothing. The same record holds each array
argument's length, which is how `int xs[]` gets bounds-checked against what was
actually passed rather than what was declared.

### `embed` reuses procedures

Bennett's construction needs the compute phase and its inverse. Rather than
inverting classical bytecode structurally, the compiler puts the compute phase
in a generated procedure and emits:

```
local temps…
call   __embed_k(inputs, temps)
out ^= temps[result]
uncall __embed_k(inputs, temps)
delocal temps…
```

`uncall` already means "run this backwards", so the uncompute step needs no new
machinery. And the shape is self-dual: inverting the sequence gives back a
sequence of the same shape, with only the copy step reversed — which is why an
`embed` block is very nearly its own inverse.

The temporaries live in the *caller's* frame and are passed in, because they
must survive between the call and the uncall for the copy to read them.

### The checker replaced one rule with a better one

An early version forbade a parameter from shadowing a global. That made
libraries fragile — a user global named `xs` would break every library
procedure with an `xs` parameter — while missing the actual hazard, which is the
opposite shape:

```reverie
int g;
proc bad(int p) { p += g; }     // reads g by name
proc main() { call bad(g); }    // …and is handed g
```

Now `p` and `g` are two names for one cell, and `p += g` is secretly `p += p`.
Nothing inside `bad` reveals it. Shadowing is now allowed — it is in fact
*safer*, since a shadowed global is unreachable inside the body — and the
aliasing is caught where it is created, at the call site, using a fixpoint over
the call graph that computes which globals each procedure can reach.

A second fixpoint over the same graph computes which parameters a procedure only
ever *reads*, which is what makes `call reverse_range(xs, 0, 5)` meaningful in a
language with no by-value parameters.

---

## Testing: reversibility as its own oracle

A reversible language admits a specification that needs no expected outputs:

> forward then backward is the identity on machine state.

`tests/generator.py` emits random programs that are valid by construction —
guard variables frozen inside the branches that test them, loops with fresh
counters, locals released in order, no call receiving the same variable twice.
It uses only `+=`, `*=`, `<<=` and `^=`, so inverting one of its programs
produces `-=`, `/=` and `>>=`, whose runtime checks are exercised by the
inverse rather than by the original.

The properties, all checked against generated programs:

- forward then backward is the identity, and takes the same number of steps
- `rev invert` produces a program that really is the inverse
- the AST inverter and the IR inverter agree
- inverting twice is the identity (syntactically, and semantically with `undo`)
- formatting is a fixed point and preserves behaviour
- flipping direction mid-run returns to the same state
- the debugger's `goto` lands exactly where stepping would

### The property that mattered

The strongest one is per-step, not end-to-end:

> from every reachable point, step back and forward again, and demand
> bit-for-bit equality of state **and** program counter.

Round-tripping a whole program only shows that the *composition* of the forward
and backward runs is the identity. Errors that cancel survive it. Three did.

**Conditionals took one extra instruction backwards.** `fi` landed one boundary
past the one the forward run departed from, so `else_end` was executed on the
way back as a no-op. Invisible at the ends of a run; fatal if you reverse in the
middle of a branch, where one backward step and one forward step no longer
cancel.

**Classical `while` counted in one instruction and uncounted in another.** The
increment lived in the head and the decrement in the test, so the loop balanced
overall while individual transitions were not inverses. Flip direction inside
one and the counter drifts.

**The classical branch bit was written before the branch ran.** `cif` pushed it,
then the branch body pushed its own tape entries on top. At the join the bit was
buried, so reversing popped a value the body owned and used it to choose a
branch. It cancelled often enough to pass roughly six hundred round-trip tests.
The bit is now written at the join, where it is the first thing the reverse
execution finds.

All three are the same failure of imagination: treating "the whole thing
reverses" as if it meant "each step reverses". In a machine that supports
stopping and turning around, it does not.

---

## What is borrowed and what is not

The surface language owes its shape to **Janus** (Lutz & Derby, 1986). The
paired conditional, the `from … until` loop, `local`/`delocal`, call/uncall, and
the reversible stack operations are theirs, and they remain the clearest
solution anyone has found to reversible control flow. `embed` is **Bennett's**
compute–copy–uncompute. The thermodynamic framing is **Landauer's**.

New here: a bidirectional bytecode machine in which the program counter is a
boundary index, so reversing time is free; frames that survive being entered
forwards and left backwards; inversion exposed simultaneously as an IR
operation, a statement, and a command-line verb; an `embed` construct that lets
destructive and reversible code share one program and one checker; open array
parameters carried in the control stack; and a test methodology that turns the
language's defining property into the oracle.

---

## Things that are not here

- **Concurrency.** Reversible concurrency is a genuinely hard problem — the
  interleaving is information too, and would have to go on a tape.
- **Fixed-width integers.** Everything is arbitrary precision, which sidesteps
  the question of whether `x *= 3` is invertible modulo 2⁶⁴ (it is, for odd
  multipliers — a real reversible ISA would use that).
- **A garbage-collected heap.** Stacks are the only dynamic structure, and they
  are dynamic in a way that stays balanced.
- **Escape hatches.** There is no `unsafe`, no `forget`, no way to drop a value.
  A program that needs to discard something must say where it goes.
