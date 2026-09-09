# The Reverie machine

The machine has a store, a control stack, a history tape, an output log — and
one piece of state a conventional VM lacks: a direction.

Every instruction implements two methods, `forward` and `backward`, and is
required to satisfy

```
backward(forward(s)) == s        forward(backward(s)) == s
```

for every reachable state `s`. That is not an aspiration; it is what
`tests/test_roundtrip.py::test_every_single_step_is_exactly_invertible` checks,
at every reachable point, on programs the fuzzer wrote.

---

## Machine state

| | |
| --- | --- |
| `mem` | a flat array of arbitrary-precision integers. Globals occupy the low cells; frames are stacked above them |
| `fp`, `sp` | frame and stack pointers into `mem` |
| `frames` | the control stack: one record per active procedure |
| `stacks` | the `stack`-typed objects |
| `history` | the tape written by classical (`embed`) instructions |
| `output`, `line` | finished lines, and the line currently being built |
| `pc` | a **boundary** index — see below |
| `dir` | the direction the next instruction is executed in |
| `arrow` | the direction of logical time |
| `position` | logical time: `+1` per forward step, `-1` per backward step |

`dir` and `arrow` differ inside an `uncall`, where a procedure runs backwards
within a program that is still going forwards.

---

## The program counter is a boundary

`pc == k` names the point *between* instruction `k-1` and instruction `k`.
Running forwards executes `code[pc]`; running backwards executes `code[pc - 1]`.

Both directions then agree about what `pc` means, so reversing the machine
mid-run is two sign flips and nothing else — no saved history, no "where did I
come from?", no special case. It is the single decision that makes the debugger
possible.

The consequence for instruction design is strict: for every boundary, the
instruction *below* it must be able to identify every forward transition that
could have landed there. Each construct below is built to satisfy that.

---

## Store instructions

| mnemonic | effect | inverse |
| --- | --- | --- |
| `upd a op= e` | `mem[a] op= eval(e)` | the paired operator |
| `un neg\|not a` | negate / complement in place | itself |
| `swap a b` | exchange two cells | itself |
| `assert e` | trap unless `e` is non-zero | itself |
| `local o = e` | require `mem[fp+o] == 0`, then store `e` | `delocal` |
| `delocal o = e` | require `mem[fp+o] == e`, then zero it | `local` |
| `push x s` | push `mem[x]` onto `s`, leave `x` zero | `pop` |
| `pop x s` | require `mem[x] == 0`, pop into it | `push` |
| `emit …` | append to the output (line or log) | `unemit` |
| `unemit …` | remove it, checking it matches | `emit` |
| `nop` | — | itself |
| `halt` | recognised, not executed: costs no logical time | — |

Update operators pair as `+`/`-`, `*`/`/`, `<<`/`>>`, and `^` with itself.
`*= 0`, an inexact `/=`, and a `>>=` that would drop set bits all trap instead
of erasing.

`halt` not being executed is what keeps logical time symmetric: a complete
forward run and its complete reversal have the same length, so "go back to the
beginning" lands exactly on the beginning.

---

## Reversible control flow

Control flow is *structured and paired*. A classical machine can jump anywhere
because forgetting where you came from is free. A reversible machine cannot, so
each construct is a matched set of instructions that records enough in the
**code** — not in a runtime history — to be walked in either direction.

### Conditional

```
  a:  if        c1, else->b, fi->d
      …then…
 ee:  else_end  c2, fi->d
  b:  else_begin c1, if->a
      …else…
  d:  fi        c2, then_end->ee
```

| | forwards | backwards |
| --- | --- | --- |
| `if` at `a` | `c1` ? `a+1` : `b+1` | assert `c1`; → `a` |
| `else_end` at `ee` | assert `c2`; → `d+1` | unreachable |
| `else_begin` at `b` | unreachable | assert `!c1`; → `a` |
| `fi` at `d` | assert `!c2`; → `d+1` | `c2` ? → `ee` : → `d` |

Boundary `d+1` has two forward predecessors, `ee` and `d`, and `c2`
distinguishes them. That is the whole mechanism. Note that `fi` going backwards
lands on `ee` itself, not one past it: it must arrive at the boundary the
forward run *departed from*, or the two directions have different lengths and
reversing mid-branch goes wrong.

### Loop

```
  f:  from    c1, repeat->r
      …body…
  u:  until   c2, repeat->r
      …step…
  r:  repeat  c1, c2, from->f, until->u
```

| | forwards | backwards |
| --- | --- | --- |
| `from` at `f` | assert `c1`; → `f+1` | `c1` ? → `f` : → `r` |
| `until` at `u` | `c2` ? → `r+1` : → `u+1` | assert `!c2`; → `u` |
| `repeat` at `r` | assert `!c1`; → `f+1` | assert `c2`; → `u` |

The loop-back edge lands on `f+1`, *after* the head, which is why boundary `f+1`
has two predecessors (`f` and `r`) distinguished by `c1` — the reason `from`
carries an assertion at all.

### Procedures

```
  E:  entry name        (never executed forwards)
      …body…
  X:  exit name         (never executed backwards)
```

A `call` jumps to `E+1` and walks out through `X`. Its reverse enters at
boundary `X` and walks out through `E`. Both directions execute exactly the same
instructions in opposite order.

A frame does not record a return address, because you can enter a procedure
going forwards and leave it going backwards. It records the *call site*:

| left through | entered by | outcome |
| --- | --- | --- |
| `exit` forwards | `call` | the call completed → after it |
| `entry` backwards | `call` | the call was undone → before it |
| `entry` backwards | `uncall` | the uncall completed → after it |
| `exit` forwards | `uncall` | the uncall was undone → before it |

Argument *addresses* live in the frame record — control state — not in the
store, so entering and leaving a procedure erases nothing. The frame also
records each array argument's length, which is what makes `int xs[]` possible.

On the way out, every cell of the frame must be zero. A procedure that leaks
one stops the machine.

---

## Classical instructions

These are emitted only inside an `embed` block, whose compute phase is always
paired with an uncompute phase that drains the tape back to empty.

| mnemonic | forwards | backwards |
| --- | --- | --- |
| `cset a = e` | push `mem[a]` (and `a`, if indexed), then store `e` | pop and restore |
| `cif` | choose a branch | → `a` |
| `celse_end` | **push 1**; → `d+1` | unreachable |
| `cfi` | **push 0**; → `d+1` | pop; → `ee` or `d` |
| `celse_begin` | unreachable | → `a` |
| `cfrom` | require the counter zero; → `f+1` | counter zero ? → `f` : → `r` |
| `cuntil c` | `c` ? counter+1, → `u+1` : push counter, zero it, → `r+1` | counter−1; → `u` |
| `crepeat` | require counter non-zero; → `f+1` | pop the counter; → `u` |

Two details are load-bearing.

**The branch bit is written at the join, not at the test.** If `cif` pushed it,
the branch body's own tape entries would sit on top of it, and reversing would
pop a value the body owned and use it to choose a branch. That bug passed
several hundred round-trip tests before the per-step property caught it.

**A classical loop has the same skeleton as a reversible one.** It has no
predicate to distinguish first entry from re-entry, so it counts iterations
into a frame cell instead — incremented by `cuntil` on the way in and
decremented by `cuntil` on the way out, so that each individual transition is
an inverse and not merely the loop as a whole.

---

## Checking it as it runs

`--paranoid` turns the property the test suite proves for generated programs
into a switch for real ones. Each step is taken, undone, compared, and redone:

```console
$ rev run examples/sorting.rev --set xs=5,3,9,1,7,2,8,4 --paranoid
```

If anything differs the machine stops and names the instruction and the first
component of the state that moved:

```
trap: `upd @x += 3` is not invertible: undoing it did not restore the machine
note: mem[0] was 0, is now 6
```

It costs about three times the work and a great deal of copying, and it is
invisible in what the machine reports afterwards -- the statistics are frozen
across the check. `tests/test_paranoid.py` sabotages six instructions in turn
to make sure it really does catch them.

## Cost

The machine reports what it spent:

```
steps        the instructions executed, in either direction
position     logical time
history tape entries written, entries reclaimed, bits written
bits erased  zero, by construction
```

There is no instruction in this instruction set that discards information. When
a program would need to, it either traps (`*= 0`) or writes what it would have
discarded onto the tape — and `embed` guarantees the tape comes back.
