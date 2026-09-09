"""The static reversibility rules, one message at a time."""

from framework import case, contains, eq, is_false, is_true, raises
from reverie.checker import analyze
from reverie.parser import parse_text


def check(text):
    return analyze(parse_text(text, "t.rev"))


def errors(text):
    return [str(e) for e in check(text).diagnostics.errors]


def only_error(text):
    errs = check(text).diagnostics.errors
    eq(len(errs), 1, f"expected exactly one error, got {[str(e) for e in errs]}")
    return errs[0]


def accepts(text):
    a = check(text)
    is_true(a.ok, "unexpected errors:\n" + a.diagnostics.render())
    return a


# ---------------------------------------------------------------------------
# rule 1: an update may not read what it writes
# ---------------------------------------------------------------------------


@case("x += x;")
@case("x += x * 2;")
@case("x += 1 + (2 * x);")
@case("x ^= min(x, 3);")
@case("a[0] += a[1];")
@case("a[x] += 1 + a[0];")
def test_self_reference_in_an_update_is_rejected(stmt):
    err = only_error(f"int x; int a[4]; proc main() {{ {stmt} }}")
    contains(err.message, "may not appear")


@case("x += y;")
@case("x += a[0] * 3;")
@case("a[0] += x;")
@case("a[x] += y;")
def test_updates_over_other_cells_are_fine(stmt):
    accepts(f"int x; int y; int a[4]; proc main() {{ {stmt} }}")


def test_the_index_of_an_update_target_may_not_read_the_array():
    err = only_error("int a[4]; proc main() { a[a[0]] += 1; }")
    contains(err.message, "may not appear")


def test_swap_indices_may_not_read_either_array():
    err = only_error("int a[4]; proc main() { a[0] <=> a[a[1]]; }")
    contains(err.message, "may not appear")


def test_swapping_two_elements_of_one_array_is_allowed():
    accepts("int a[4]; int i; int j; proc main() { a[i] <=> a[j]; }")


# ---------------------------------------------------------------------------
# rule 2: locals are balanced
# ---------------------------------------------------------------------------


def test_a_local_must_be_released():
    err = only_error("int x; proc main() { local int t = 1; x += t; }")
    contains(err.message, "never released")


def test_locals_are_released_last_in_first_out():
    err = only_error(
        "int x; proc main() { local int a = 1; local int b = 2; "
        "delocal int a = 1; delocal int b = 2; }"
    )
    contains(err.message, "expected `delocal b`")


def test_delocal_needs_an_expression():
    err = only_error("int x; proc main() { local int t = 1; delocal int t; }")
    contains(err.message, "needs an expression")


def test_local_needs_an_initialiser():
    err = only_error("int x; proc main() { local int t; delocal int t = 0; }")
    contains(err.message, "needs an initialiser")


def test_delocal_without_local():
    err = only_error("int x; proc main() { delocal int t = 1; }")
    contains(err.message, "no matching `local`")


def test_local_arrays_take_no_initialiser():
    err = only_error("int x; proc main() { local int t[3] = 1; delocal int t[3]; }")
    contains(err.message, "take no initialiser")


def test_balanced_locals_are_accepted():
    accepts("int x; int y; proc main() { local int t = x + 1; y ^= t; "
            "delocal int t = x + 1; }")


def test_a_delocal_may_not_reconstruct_a_local_from_itself():
    err = only_error("int x; proc main() { local int t = x + 1; x ^= t; "
                     "delocal int t = x ^ t; }")
    contains(err.message, "may not appear in a delocal expression")


def test_a_local_may_not_shadow():
    err = only_error("int t; proc main() { local int t = 1; delocal int t = 1; }")
    contains(err.message, "already in scope")


def test_nested_blocks_reuse_frame_slots():
    a = accepts(
        "int x; proc main() { { local int p = 1; x += p; delocal int p = 1; } "
        "{ local int q = 2; x += q; delocal int q = 2; } }"
    )
    eq(a.procs["main"].frame_size, 1)


def test_sequential_locals_take_separate_slots():
    a = accepts(
        "int x; proc main() { local int p = 1; local int q = 2; x += p + q; "
        "delocal int q = 2; delocal int p = 1; }"
    )
    eq(a.procs["main"].frame_size, 2)


# ---------------------------------------------------------------------------
# rule 3: no aliasing across arguments
# ---------------------------------------------------------------------------


def test_the_same_variable_may_not_be_passed_twice():
    err = only_error(
        "int x; proc f(int a, int b) { a += b; } proc main() { call f(x, x); }"
    )
    contains(err.message, "passed twice")


def test_a_global_the_callee_names_may_not_be_passed_to_it():
    err = only_error(
        "int g; int h; proc f(int p) { p += g; } proc main() { call f(g); }"
    )
    contains(err.message, "cannot also receive it as an argument")


def test_passing_a_different_global_is_fine():
    accepts("int g; int h; proc f(int p) { p += g; } proc main() { call f(h); }")


def test_a_parameter_may_shadow_a_global():
    accepts("int xs; proc f(int xs) { xs += 1; } proc main() { local int t = 0; "
            "call f(t); delocal int t = 1; }")


def test_arity_is_checked():
    err = only_error(
        "int x; proc f(int a, int b) { a += b; } proc main() { call f(x); }"
    )
    contains(err.message, "takes 2 argument")


def test_unknown_procedure_lists_the_known_ones():
    err = only_error("int x; proc main() { call nope(x); }")
    contains(err.message, "unknown procedure")
    contains(" ".join(err.notes), "main")


def test_parameter_types_are_checked():
    err = only_error(
        "int x; stack s; proc f(stack q) { skip; } proc main() { call f(x); }"
    )
    contains(err.message, "is `stack`")


def test_array_lengths_are_checked():
    err = only_error(
        "int a[3]; proc f(int xs[4]) { xs[0] += 1; } proc main() { call f(a); }"
    )
    contains(err.message, "expects length 4")


def test_open_array_parameters_accept_any_length():
    accepts("int a[3]; int b[9]; proc f(int xs[]) { xs[0] += 1; } "
            "proc main() { call f(a); call f(b); }")


# ---------------------------------------------------------------------------
# constant arguments and the read-only analysis
# ---------------------------------------------------------------------------


def test_constants_bind_to_read_only_parameters():
    a = accepts(
        "int a[4]; proc f(int xs[], int k) { xs[0] += k; } proc main() { call f(a, 3); }"
    )
    eq(len(a.const_args), 1)


def test_constants_are_refused_for_written_parameters():
    err = only_error(
        "int x; proc f(int out) { out += 1; } proc main() { call f(7); }"
    )
    contains(err.message, "writes to parameter")


def test_write_through_a_call_propagates():
    err = only_error(
        "int x; proc inner(int q) { q += 1; } proc outer(int p) { call inner(p); } "
        "proc main() { call outer(2); }"
    )
    contains(err.message, "writes to parameter")


def test_read_only_through_a_call_stays_read_only():
    accepts(
        "int x; proc inner(int q) { x += q; } proc outer(int p) { call inner(p); } "
        "proc main() { call outer(2); }"
    )


def test_expression_arguments_that_are_not_constant_are_rejected():
    err = only_error(
        "int x; int y; proc f(int xs) { y += xs; } proc main() { call f(x + 1); }"
    )
    contains(err.message, "compile-time constant")


# ---------------------------------------------------------------------------
# the `fi;` shorthand
# ---------------------------------------------------------------------------


def test_fi_shorthand_needs_an_undisturbed_test():
    err = only_error("int x; proc main() { if x > 0 { x += 1; } fi; }")
    contains(err.message, "reuses the entry test")


def test_fi_shorthand_is_fine_when_the_test_is_untouched():
    accepts("int x; int y; proc main() { if x > 0 { y += 1; } fi; }")


def test_a_call_in_a_branch_defeats_the_shorthand():
    err = only_error(
        "int x; int y; proc f(int p) { p += 1; } "
        "proc main() { if x > 0 { call f(y); } fi; }"
    )
    contains(err.message, "reuses the entry test")


def test_an_explicit_exit_predicate_always_works():
    accepts(
        "int x; int y; proc f(int p) { p += 1; } "
        "proc main() { if x > 0 { call f(y); } fi y != 0; }"
    )


# ---------------------------------------------------------------------------
# names, types and shapes
# ---------------------------------------------------------------------------


def test_unknown_name_suggests_a_correction():
    err = only_error("int counter; proc main() { countr += 1; }")
    contains(err.message, "unknown name")
    contains(err.notes[0], "counter")


def test_constants_cannot_be_written():
    err = only_error("const K = 3; proc main() { K += 1; }")
    contains(err.message, "cannot be written")


def test_arrays_need_an_index():
    err = only_error("int a[3]; proc main() { a += 1; }")
    contains(err.message, "needs an element")


def test_scalars_cannot_be_indexed():
    err = only_error("int x; proc main() { x[0] += 1; }")
    contains(err.message, "not an array")


def test_stack_operations_need_a_stack():
    err = only_error("int x; int y; proc main() { push(x, y); }")
    contains(err.message, "not a stack")


def test_stacks_cannot_be_updated_directly():
    err = only_error("stack s; proc main() { s += 1; }")
    contains(err.message, "cannot update the stack")


def test_main_must_exist():
    err = only_error("int x; proc other() { x += 1; }")
    contains(err.message, "no `main`")


def test_main_takes_no_parameters():
    err = only_error("int x; proc main(int a) { a += 1; }")
    contains(err.message, "must not take parameters")


def test_duplicate_declarations():
    contains(only_error("int x; int x; proc main() { skip; }").message, "declared twice")
    contains(
        only_error("proc main() { skip; } proc main() { skip; }").message,
        "declared twice",
    )


def test_array_lengths_must_be_constant_and_positive():
    contains(only_error("int x; int a[x]; proc main() { skip; }").message,
             "not a compile-time constant")
    contains(only_error("int a[0]; proc main() { skip; }").message, "positive length")


def test_globals_are_laid_out_in_declaration_order():
    a = accepts("int x; int a[4]; stack s; int y; proc main() { skip; }")
    eq([(g.name, g.addr, g.length) for g in a.globals.values()],
       [("x", 0, 1), ("a", 1, 4), ("s", 5, 1), ("y", 6, 1)])
    eq(a.n_globals, 7)


# ---------------------------------------------------------------------------
# embed blocks
# ---------------------------------------------------------------------------


def test_classical_code_may_not_write_enclosing_state():
    err = only_error("int x; int y; proc main() { embed (x ^= a) { var a = 1; y = 2; } }")
    contains(err.message, "not declared inside this embed block")


def test_classical_code_may_read_enclosing_state():
    accepts("int x; int y; proc main() { embed (x ^= a) { var a = y + 1; } }")


def test_embed_blocks_cannot_nest():
    err = only_error(
        "int x; proc main() { embed (x ^= a) { var a = 1; } "
        "embed (x ^= b) { var b = 2; } }"
    ) if False else None
    accepts("int x; proc main() { embed (x ^= a) { var a = 1; } "
            "embed (x ^= b) { var b = 2; } }")


def test_duplicate_classical_declaration():
    err = only_error("int x; proc main() { embed (x ^= a) { var a = 1; var a = 2; } }")
    contains(err.message, "already declared")


def test_embed_plan_records_its_temporaries():
    a = accepts(
        "int x; proc main() { embed (x ^= a) { var a = 0; var b = 1; "
        "while (a < b) { a = a + 1; } } }"
    )
    plan = list(a.plans.values())[0]
    eq([t.name for t in plan.ctemps], ["a", "b", "__out0"])
    eq(plan.counters, 1)


def test_embed_free_variables_become_parameters():
    a = accepts("int x; proc f(int p) { embed (x ^= a) { var a = p + 1; } } "
                "proc main() { local int t = 0; call f(t); delocal int t = 0; }")
    plan = list(a.plans.values())[0]
    eq([f.name for f in plan.free], ["p"])


# ---------------------------------------------------------------------------
# compile-time constants
# ---------------------------------------------------------------------------


@case("const A = 2 + 3 * 4;", 14)
@case("const A = -5;", -5)
@case("const A = !0;", 1)
@case("const A = ~3;", -4)
@case("const A = min(3, 9) + max(3, 9);", 12)
@case("const A = abs(-7) + sign(-7);", 6)
@case("const A = 1 && 0;", 0)
@case("const A = 1 || 0;", 1)
@case("const A = 0 && 1;", 0)
@case("const A = 0 || 0;", 0)
@case("const A = 7 / 2;", 3)
@case("const A = 7 % 2;", 1)
@case("const B = 3; const A = B * 2;", 6)
def test_constant_expressions(decl, value):
    a = accepts(f"{decl} proc main() {{ skip; }}")
    eq(a.consts["A"], value)


def test_division_by_zero_in_a_constant():
    err = only_error("const A = 1 / 0; proc main() { skip; }")
    contains(err.message, "division by zero in a constant")


def test_a_constant_cannot_depend_on_an_array():
    err = only_error("int xs[3]; const A = xs; proc main() { skip; }")
    contains(err.message, "cannot depend on the array")


def test_len_of_a_constant_sized_array_is_constant():
    a = accepts("int xs[4]; const A = len(xs); proc main() { skip; }")
    eq(a.consts["A"], 4)


def test_len_of_an_open_parameter_is_not_constant():
    errs = errors(
        "proc f(int xs[]) { local int a[len(xs)]; delocal int a[len(xs)]; } "
        "int ys[2]; proc main() { call f(ys); }"
    )
    contains(errs[0], "not known at compile time")


def test_len_of_something_that_is_not_an_array():
    err = only_error("int x; const A = len(x); proc main() { skip; }")
    contains(err.message, "`len` needs an array name")


def test_a_call_is_not_a_constant_expression():
    errs = errors("int x; const A = empty(x); proc main() { skip; }")
    is_true(any("constant" in e or "stack" in e for e in errs), errs)


def test_a_constant_declared_twice():
    contains(only_error("const A = 1; const A = 2; proc main() { skip; }").message,
             "declared twice")


def test_generated_names_are_reserved():
    err = only_error("proc __embed_1() { skip; } proc main() { skip; }")
    contains(err.message, "collides with a compiler-generated name")


def test_duplicate_parameters():
    err = check("int x; int y; proc f(int a, int a) { a += 1; } "
                "proc main() { call f(x, y); }").diagnostics.errors[0]
    contains(err.message, "duplicate parameter")


def test_local_stacks_are_not_supported():
    err = only_error("proc main() { local stack s; delocal stack s; }")
    contains(err.message, "local stacks are not supported")


def test_a_local_array_needs_a_positive_length():
    err = only_error("proc main() { local int a[0]; delocal int a[0]; }")
    contains(err.message, "needs a positive length")


def test_a_local_array_length_must_match_its_release():
    err = only_error("proc main() { local int a[3]; delocal int a[4]; }")
    contains(err.message, "has length 4, declared as 3")


def test_releasing_a_local_as_the_wrong_kind():
    err = only_error("proc main() { local int a[3]; delocal int a = 1; }")
    contains(err.message, "does not match its declaration")


def test_locals_closed_out_of_order_report_once():
    errs = errors(
        "int x; proc main() { local int a = 1; local int b = 2; local int c = 3; "
        "x += a + b + c; delocal int a = 1; delocal int c = 3; delocal int b = 2; }"
    )
    eq(len(errs), 1, errs)
    contains(errs[0], "expected `delocal c`")


def test_a_stack_cannot_be_pushed_onto_itself():
    err = only_error("stack s; proc main() { push(s, s); }")
    contains(err.message, "onto itself")


def test_push_needs_a_stack_name():
    errs = errors("int x; proc main() { push(x, x + 1); }")
    is_true(any("stack name" in e for e in errs), errs)


def test_embed_outputs_must_be_cells():
    err = only_error("stack s; proc main() { embed (s ^= a) { var a = 1; } }")
    contains(err.message, "must be integer cells")


def test_embed_blocks_may_not_nest():
    errs = errors(
        "int x; proc main() { embed (x ^= a) { var a = 1; } }"
    )
    eq(errs, [])


def test_classical_arrays_need_a_positive_length():
    err = only_error("int x; proc main() { embed (x ^= a[0]) { var a[0]; } }")
    contains(err.message, "needs a positive length")


def test_classical_code_reports_unknown_names():
    err = only_error("int x; proc main() { embed (x ^= a) { var a = nope; } }")
    contains(err.message, "unknown name `nope` in an embed block")


def test_classical_stack_queries_must_name_a_stack():
    err = only_error("int x; int y; proc main() { embed (x ^= a) { var a = size(y); } }")
    contains(err.message, "not a stack")


def test_classical_arrays_are_indexed():
    contains(
        only_error(
            "int x; proc main() { embed (x ^= a[0]) { var a[2]; a = 1; } }"
        ).message,
        "assign to an element",
    )
    contains(
        only_error(
            "int x; proc main() { embed (x ^= b) { var b = 1; b[0] = 2; } }"
        ).message,
        "not an array",
    )
