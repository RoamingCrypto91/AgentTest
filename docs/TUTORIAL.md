# A tour of Reverie

Nothing here assumes you have met a reversible language before. It does assume
you have written a loop in something.

---

## 1. Your first program

```reverie
int n;
int total;

proc main() {
    total += n * 2;
}
```

```console
$ rev run examples/first.rev --set n=21
  n     = 21
  total = 42
```

Two things are already unusual.

There is no `=`. Reverie has `+=`, `-=`, `^=`, `*=`, `/=`, `<<=`, `>>=` and
`<=>`, and that is the whole list. Plain assignment throws away whatever the
target held, and this language does not have a way to do that. If you write
`total = n * 2` the parser stops you and points at `embed`, which we will get
to.

And the program runs backwards:

```console
$ rev back examples/first.rev --set n=21
forward:
  n     = 21
  total = 42

backward:
  n     = 21
  total = 0

  3 steps forward, 3 steps back, state identical.
```

`total -= n * 2` is not written anywhere. The machine derived it.

---

## 2. The rule that shapes everything

Try to double a number in place:

```reverie
proc main() {
    total += total;
}
```

```
error: `total` may not appear in the right-hand side of an update
 --> tour.rev:5:14
  |
5 |     total += total;
  |              ^^^^^ reads the variable being updated
  |
note: a reversible update is undone by re-evaluating this
note: expression, so it must not depend on the target
note: introduce `local int t = ...;` if you need the old value
```

Here is why. `total += e` is undone by `total -= e`, and that is only correct
if `e` evaluates to the same number before and after. If `e` mentions `total`,
it does not.

The fix is to say what you meant:

```reverie
total *= 2;
```

Or, when the arithmetic is not that tidy, to borrow a cell:

```reverie
local int t = total;
total += t;
delocal int t = total / 2;
```

`local` takes a cell that must be zero and puts a value in it. `delocal` gives
it back — but you have to say what the cell contains, and the machine checks.
That is not bureaucracy: a cell that is released without its contents being
reconstructible is a cell whose contents were *erased*, and the whole point is
that nothing is.

---

## 3. Conditionals carry two predicates

```reverie
int x;
int y;

proc main() {
    if x > 3 {
        y += 100;
    } else {
        y += 7;
    } fi y > 50;
}
```

Read it forwards: test `x > 3`, take a branch, and on the way out check
`y > 50`.

Now read it backwards. You arrive at the bottom of a conditional. Which branch
did control come from? A conventional language cannot say — that is exactly the
bit it threw away. Reverie asks you to supply it: **the predicate after `fi`
must be true if the then-branch ran and false if the else-branch did.** Going
backwards, that predicate picks the branch, and the test at the top becomes the
check.

If the branches cannot disturb the entry test, write `fi;` and it is reused:

```reverie
if x > 3 {
    y += 100;
} fi;
```

The checker verifies that. Modify `x` inside, and it tells you to write the
predicate out.

---

## 4. Loops carry an entry assertion

```reverie
local int i = 0;
from i == 0 do {
    total += i;
} loop {
    i += 1;
} until i == n;
delocal int i = n;
```

`from` is asserted **true** the first time control arrives and **false** every
time afterwards; `until` is the exit test. Same trick, same reason: running
backwards, `until` becomes the entry assertion and `from` becomes the exit test.
The inverse of that loop is

```reverie
from i == n do { total -= i; } loop { i -= 1; } until i == 0;
```

which `rev invert` will print for you.

The `do` part runs once more than the `loop` part, so `i` ends at `n` and the
`delocal` closes.

---

## 5. Procedures go both ways

```reverie
proc twice(int a, int b) {
    a += b * 2;
}

proc main() {
    call twice(x, y);
    uncall twice(x, y);   // x is back where it started
}
```

Parameters are passed by reference, so arguments name cells. Two rules follow:
no variable may be passed twice (the callee could then update a cell using
itself), and a global a procedure names directly may not also be handed to it.

Constants are allowed for parameters the procedure only ever *reads*, which the
compiler works out for you:

```console
$ rev check stdlib/array.rev
stdlib/array.rev: ok (library, no `main`)
    reverse_range(int xs[], int lo, int hi)  read-only: lo, hi
    rotate(int xs[], int k)  read-only: k
```

So `call reverse_range(xs, 0, 5)` compiles; the constants become cells the
caller owns for the duration of the call.

---

## 6. `undo` — inversion as a statement

```reverie
call expensive(x, scratch);
y ^= scratch;
undo {
    call expensive(x, scratch);
}
```

`undo { S }` runs `S` backwards. The compiler implements it by building the IR
for `S` and calling `.invert()` on it, which is the same code path `rev invert`
uses. The idiom above is Bennett's construction written by hand: compute, copy
the answer out, uncompute. `scratch` ends at zero.

---

## 7. `embed` — destructive code, without the erasure

Sometimes you just want a `while` loop and a division:

```reverie
int n;
int root;
int rem;

proc main() {
    embed (root ^= r, rem ^= n - r * r) {
        var r = 0;
        while ((r + 1) * (r + 1) <= n) {
            r = r + 1;
        }
    }
}
```

Inside `embed` the ordinary rules are suspended: `=` assigns, `while` loops,
`var` declares. The compiler pays for it with a history tape, and then hands
the tape back:

```console
$ rev doctor examples/tour.rev --set n=1000000
  history tape      peak 1004 entries, 17982 bits written
  tape reclaimed    2008/2008 entries
  bits erased       0
```

The rule inside an `embed` block is that classical code may **read** enclosing
state but never write it — anything it wrote would be undone by the uncompute
step anyway. Results leave through the bindings in the header, one reversible
`^=` or `+=` each.

---

## 8. Watch it happen

```console
$ rev viz examples/tour.rev --set n=1000000 -o tour.html
```

Open that and drag the scrubber. During the compute phase the history tape
climbs; it holds flat while the answer is copied out; it drains to nothing
through the uncompute. That shape is Bennett's construction, and it is the
reason the last line of `rev doctor` says zero.

Or step through it yourself:

```console
$ rev debug examples/tour.rev --set n=1000000
(rev) break proc __embed_1
(rev) run
(rev) watch root
(rev) goto 2000
(rev) back 50
```

`goto` and `back` are not replays. They are the machine, running the other way.

---

## 9. Let the language test itself

A procedure in a normal language needs a test with an expected answer, written
by hand. A procedure in Reverie already carries one: every `fi` predicate,
every loop's entry assertion, every `delocal` expression is a claim the machine
checks. Feed a procedure an input and it either completes or tells you the
input was outside the region where it is invertible.

So the tests can write themselves:

```console
$ rev verify stdlib/array.rev --cases 20
```

`rev verify` builds a driver around every procedure in the file, hands it
random arguments, and checks two things on each one: that running it forwards
and then backwards restores the starting state, and that `call f` followed by
`uncall f` leaves the state alone. When one fails, the input is shrunk until no
smaller one still fails, and what you get is the counterexample and the line
that rejected it:

```
  drain(int x, int y)     3 cases   FAILED
      running it backwards did not restore the starting state: exit assertion
      `(&x > 0)` must hold after the then-branch
      smallest input found: x = 1, y = 0
```

Not every procedure is total. `swap_at(xs, i, j)` is only reversible when `i`
and `j` are real indices, and a search that ignores that is reporting a missing
sentence of documentation as a bug. So a procedure may say what it needs, in a
doc comment, in ordinary Reverie:

```reverie
/// Swap two elements.  Self-inverse.
/// requires: 0 <= i && i < len(xs) && 0 <= j && j < len(xs)
proc swap_at(int xs[], int i, int j) {
    xs[i] <=> xs[j];
}
```

There are three of these, and `docs/LANGUAGE.md` spells them out: `requires:`
filters generated inputs, `given:` pins one to a value or fixes an array's
length, and `setup:` names code that builds a state no predicate could
describe — a stocked peg for `hanoi`, say. Every procedure in `stdlib/` and
`examples/` carries whatever it needs, and all of them pass.

---

## Where next

- `docs/LANGUAGE.md` — every construct, precisely
- `docs/ISA.md` — what the machine actually executes
- `docs/DESIGN.md` — why the pieces are shaped this way, and three bugs the
  fuzzer found
- `examples/` — fourteen programs, each making one point
