"""Tokeniser tests."""

from framework import case, contains, eq, is_true, raises
from support import ROOT
from reverie.diagnostics import LexError, Source
from reverie.lexer import TOK_DOC, TOK_EOF, TOK_INT, TOK_STRING, tokenize


def toks(text):
    return [t for t in tokenize(Source("t.rev", text)) if t.kind != TOK_EOF]


def texts(text):
    return [t.text for t in toks(text)]


def test_empty_source_is_just_eof():
    eq([t.kind for t in tokenize(Source("t.rev", ""))], [TOK_EOF])
    eq([t.kind for t in tokenize(Source("t.rev", "   \n\t\n"))], [TOK_EOF])


@case("0", 0)
@case("42", 42)
@case("1_000_000", 1000000)
@case("0xFF", 255)
@case("0xdead_beef", 0xDEADBEEF)
@case("0b1011", 11)
@case("0o755", 493)
@case("'A'", 65)
@case("'\\n'", 10)
@case("'\\\\'", 92)
def test_integer_literals(text, value):
    t = toks(text)
    eq(len(t), 1)
    eq(t[0].kind, TOK_INT)
    eq(t[0].value, value)


@case("0x")
@case("12abc")
def test_bad_numeric_literals(text):
    with raises(LexError):
        toks(text)


def test_string_escapes():
    t = toks(r'"a\nb\tc\"d\\e"')
    eq(t[0].kind, TOK_STRING)
    eq(t[0].value, 'a\nb\tc"d\\e')


def test_unterminated_string():
    with raises(LexError, "unterminated string"):
        toks('"abc')
    with raises(LexError, "unterminated string"):
        toks('"abc\ndef"')


def test_unknown_escape_names_the_valid_ones():
    with raises(LexError, "unknown escape") as r:
        toks(r'"a\qb"')
    contains(r.error.notes[0], "\\n")


def test_line_and_block_comments_are_skipped():
    eq(texts("a // comment\nb /* block\ncomment */ c"), ["a", "b", "c"])


def test_unterminated_block_comment():
    with raises(LexError, "unterminated block comment"):
        toks("/* forever")


def test_doc_comments_are_kept():
    t = toks("/// hello\n/// world\nproc")
    eq([x.kind for x in t[:2]], [TOK_DOC, TOK_DOC])
    eq([x.text for x in t[:2]], ["hello", "world"])
    eq(t[2].text, "proc")


def test_operators_are_matched_greedily():
    eq(texts("<=> <= < <<= << >>= >> ** != == ->"),
       ["<=>", "<=", "<", "<<=", "<<", ">>=", ">>", "**", "!=", "==", "->"])


def test_keywords_versus_identifiers():
    t = toks("proc procedure printer print")
    eq([x.kind for x in t], ["keyword", "ident", "ident", "keyword"])


def test_spans_point_at_the_right_text():
    src = Source("t.rev", "int  x;\ny += 2;")
    ts = [t for t in tokenize(src) if t.kind != TOK_EOF]
    for t in ts:
        eq(t.span.text, t.text)
    eq(src.line_col(ts[-1].span.start)[0], 2)


def test_unexpected_character():
    with raises(LexError, "unexpected character"):
        toks("x $ y")


def test_every_example_tokenises():
    import os

    for folder in ("examples", "stdlib"):
        d = os.path.join(ROOT, folder)
        for name in sorted(os.listdir(d)):
            if not name.endswith(".rev"):
                continue
            with open(os.path.join(d, name)) as fh:
                text = fh.read()
            got = tokenize(Source(name, text))
            is_true(len(got) > 10, f"{name} produced only {len(got)} tokens")
