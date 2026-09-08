# The Reverie language

A reference. For a gentler introduction see [TUTORIAL.md](TUTORIAL.md).

Reverie is a small imperative language in which every statement has a defined
inverse. That single requirement explains almost every way it differs from a
conventional language: there is no assignment, conditionals carry two
predicates, loops carry two predicates, local variables must be closed with the
value they hold, and arguments are passed by reference under an anti-aliasing
rule.

---

## Lexical structure

| | |
| --- | --- |
| comments | `// line`, `/* block */`, `/// documentation` (attaches to the next `proc`) |
| identifiers | `[A-Za-z_][A-Za-z0-9_]*` |
| integers | `42`, `1_000_000`, `0xFF`, `0b1011`, `0o755`, `'A'`, `'\n'` |
| strings | `"…"` with `\n \t \r \0 \\ \" \'` — only in `print` and `write` |

Integers are arbitrary precision. Division truncates toward zero and the
remainder takes the sign of the dividend, so `(a / b) * b + a % b == a` for
every sign combination.

Keywords: `proc const int stack local delocal call uncall if else fi from do
loop until push pop print unprint write unwrite assert skip undo embed var
while for neg not import`.

---

## Declarations

```reverie
const N = 8;              // compile-time integer
int x;                    // one cell, zero at start
int grid[N];              // N consecutive cells
stack trail;              // a stack of integers
import "array.rev";       // splice in another module

/// Documentation for the procedure below.
proc name(int a, int xs[], int ys[4], stack s) { … }
```

Globals are laid out in declaration order and are the program's input and
output: `rev run prog.rev --set x=5 --set grid=1,2,3` sets them, and the final
values are printed.

Array lengths must be compile-time constants. A *parameter* may instead be
declared `int xs[]`, in which case it takes its length from whatever is passed
— and is bounds-checked against that length, not against any declaration.
`len(xs)` gives it.

Execution starts at `proc main()`, which takes no parameters. A module with no
`main` is a library; `rev check` will report on it.

---

## Types

`int` — an arbitrary-precision integer cell.
`int[n]` — `n` consecutive cells; `int[]` as a parameter only.
`stack` — a stack of integers, declared at module scope, passed by reference.

---

## Expressions

Pure: an expression reads the store and nothing else. There are no function
calls in expressions (procedures are invoked by `call`), so evaluation order is
never observable and the machine can re-evaluate any expression when running
backwards.

Precedence, loosest first:

```
||
&&
|
^
&
==  !=
<  <=  >  >=
<<  >>
+  -
*  /  %
**                       (right associative)
-  !  ~                  (prefix)
a[i]                     (postfix)
```

`&&` and `||` short-circuit and yield `1` or `0`, as do the comparisons.

Builtins: `min(a,b)`, `max(a,b)`, `abs(a)`, `sign(a)`, `len(a)` — the length of
an array — and `empty(s)`, `top(s)`, `size(s)` for stacks.

`len(a)` names an array without reading it. That distinction matters: it is
what makes `a[i] <=> a[len(a) - 1 - i]` legal under the rule below.

---

## Statements

### Updates

```reverie
x += e;     x -= e;     x ^= e;
x *= e;     x /= e;
x <<= e;    x >>= e;
x <=> y;
neg x;      not x;
```

`+=` / `-=` and `*=` / `/=` and `<<=` / `>>=` are inverse pairs; `^=`, `<=>`,
`neg` and `not` are their own inverses. The target may be a variable or an array
element.

Three of these can fail at run time rather than erase:

| | |
| --- | --- |
| `x *= 0` | multiplying by zero is not injective |
| `x /= e` | when `x` is not exactly divisible by `e` |
| `x >>= k` | when any of the `k` low bits is set |

**The rule.** The expression on the right may not read the cell on the left.
For `a[i] += e`, neither `e` nor `i` may mention `a`. A reversible update is
undone by re-evaluating its right-hand side, so the right-hand side must not
depend on the target.

### Conditionals

```reverie
if entry {
    …
} else {
    …
} fi exit;
```

Forwards: `entry` chooses the branch; `exit` is asserted **true** after the
then-branch and **false** after the else-branch. Backwards: `exit` chooses the
branch and `entry` is asserted. The `else` part may be omitted. `else if`
chains.

Writing `fi;` reuses `entry` as the exit predicate. The checker allows it only
when neither branch can disturb the names `entry` reads — including through a
call, since a callee may touch any global.

### Loops

```reverie
from entry do {
    …body…
} loop {
    …step…
} until exit;
```

Meaning:

```
assert entry
body
while not exit:
    step
    assert not entry
    body
```

So `body` runs once more than `step`. Both `do` and `loop` sections may be
omitted. Backwards, `exit` becomes the entry assertion and `entry` the exit
test — the inverse of `from a do S loop T until b` is `from b do S⁻¹ loop T⁻¹
until a`.

### Local storage

```reverie
local int t = e;
…
delocal int t = e2;

local int scratch[16];
…
delocal int scratch[16];
```

`local` requires the cell to be zero and puts `e` in it. `delocal` requires the
cell to hold exactly `e2` and zeroes it. Local arrays are created zeroed and
must be zeroed again before release.

Locals must be balanced within their block and released last-in-first-out. A
frame is checked on the way out: if any cell is non-zero, the machine stops
rather than discard it.

### Procedures

```reverie
call name(a, b);
uncall name(a, b);
```

By reference. Arguments name variables, except for parameters the procedure
only reads, which also accept compile-time constants. `uncall` runs the body
backwards, and recursion is fine.

Two anti-aliasing rules:

- no variable may be passed twice to the same call;
- a global that the callee names directly may not also be passed to it.

Both exist for the same reason: two names for one cell would let a procedure
violate the update rule with nothing locally visible to show for it.

### Stacks

```reverie
push(x, s);    // pushes x and leaves x zero
pop(x, s);     // requires x to be zero, pops into it
```

Exact inverses. `push` zeroing its source is what makes the pair reversible:
without it, `push` would leave two copies of the value and `pop` would have to
erase one.

### Output

```reverie
print "x = ", x;    // finishes the line
write x, " ";       // leaves the line open
```

Output is information. Running backwards **un-prints**: the machine takes the
line off the log and checks that what it reproduces is what was there. The
inverted form of `print` is `unprint`, which is what `rev invert` emits, and a
program that ends mid-line is reported as unclean.

### Assertions and `skip`

```reverie
assert x > 0;
skip;
```

`assert` is its own inverse and is checked in both directions.

### `undo`

```reverie
undo {
    …statements…
}
```

Runs the block backwards. Inversion is not a special case in the back end: the
compiler builds the block's IR and calls `.invert()` on it — the same operation
`rev invert` performs on the syntax tree.

`undo` is a *partial* operation: it only makes sense in a state the block could
have produced. `undo { x *= 3 }` traps unless `x` is divisible by 3.

### `embed`

```reverie
embed (out1 ^= e1, out2 += e2) {
    …classical statements…
}
```

Inside the braces, ordinary destructive code:

```reverie
var name = e;          // declare
var buf[n];            // a zeroed classical array
name = e;   buf[i] = e;
name += e;             // and -=, *=, /=, %=, ^=, &=, |=
if (c) { … } else { … }
while (c) { … }
for (var i = 0; c; i = i + 1) { … }
```

Classical code may **read** enclosing state but never write it. Results leave
only through the bindings in the header, one reversible `^=`, `+=` or `-=` each,
evaluated in the block's final classical state.

The compiler emits Bennett's construction — compute, copy, uncompute — so the
block is a pure function applied reversibly, with the history tape empty again
afterwards. See [DESIGN.md](DESIGN.md).

Blocks may not nest. An `embed` is very nearly its own inverse: inverting one
flips the sign of a `+=` binding and nothing else.

---

## The four static rules

1. **No self-reference in an update.** `x += f(…)` is undone by `x -= f(…)`, so
   `f` may not read `x`.
2. **Balanced locals.** Every `local` needs a matching `delocal` in the same
   block, last-in-first-out, with an expression that reconstructs the value.
3. **No aliasing across arguments.** Parameters are references; two bound to
   one cell would defeat rule 1 invisibly.
4. **Classical code stays in its box.** Inside `embed` you may assign
   destructively, but only to variables the block declared.

Everything else the checker reports — arity, types, array lengths, unknown
names — is ordinary.

---

## Command line

```
rev run      prog.rev [--set x=5] [--stats] [--backward] [--check-clean]
rev back     prog.rev              run forwards, then backwards, prove the identity
rev check    prog.rev              analyse without running
rev fmt      prog.rev [-i]         format
rev invert   prog.rev [--proc p] [--keep]     print the program that undoes it
rev disasm   prog.rev              bytecode and storage layout
rev debug    prog.rev [-c CMD]     the time-travel debugger
rev trace    prog.rev [-o t.json]  record an execution
rev viz      prog.rev [-o p.html]  a scrubbable page
rev doctor   prog.rev              reversibility and thermodynamic cost
```

`--set` takes `name=value`, `name=0xFF`, or `name=1,2,3` for arrays and stacks.
Imports are resolved against the importing file's directory, the working
directory, the bundled `stdlib/`, and `$REVERIE_PATH`.
