"""
The grammar, as an actual grammar.

One Lark grammar for the whole course language. It replaces the regex parsing
the notations started with — regexes could not handle nested calls such as
`flatMap(split(value))`, and gave error positions that were only ever
line-accurate.

Lark gives real error positions (line *and* column), so the editor can put a
squiggle under the offending token rather than the whole line. It is pure
Python, so it runs unchanged under Pyodide in the browser.
"""

from __future__ import annotations

from lark import Lark, Token, Tree

# One grammar, three dialects. A program is a sequence of statements; which
# statements appear tells us whether it is a MapReduce, Spark or RPC program.
GRAMMAR = r"""
// Statements are newline-separated. Blank lines and comments are skipped.
start: _NL* (statement _NL*)*

?statement: use_decl
          | process_decl
          | event_stmt
          | message
          | config
          | input_decl
          | split_decl
          | decorated
          | class_def
          | func_def
          | assign
          | action
          | assertion
          | budget
          | note
          | lose

// --- decorated classes and functions ---------------------------------
// A machine is a decorated class whatever the exercise calls it; a method is
// a typed function. Decorators configure, keyword arguments pass options —
// Python already means all of that, so a student reading this reads Python.
decorated: decorator+ (class_def | func_def)
decorator: "@" NAME ["(" [dec_args] ")"] _NL+
class_def: KW_CLASS NAME ":" _NL+ _INDENT (member | _NL)+ _DEDENT
// A machine that only carries settings — a mapper with a speed, say — has no
// methods of its own, and `pass` is how Python says that.
?member: decorated | func_def | pass_stmt
pass_stmt: KW_PASS _NL
// `@duration(0.4)` passes a value, `@machine(speed=0.5)` names one. Both read as
// Python, so both are allowed wherever a decorator takes arguments.
dec_args: dec_arg ("," dec_arg)*
?dec_arg: kwarg | expr
kwarg: NAME "=" expr

// --- configuration -------------------------------------------------
config: CONFIG_KEY NUMBER            -> config
      | NAME KW_SPEED NUMBER         -> speed
      | KW_COMBINER ONOFF            -> combiner
CONFIG_KEY.5: /(?:mappers|reducers|executors|partitions|capacity)\b/
ONOFF.5: /(?:on|off)\b/

use_decl: KW_USE NAME

// --- vector clocks --------------------------------------------------
process_decl: KW_PROCESS NAME ("," NAME)*
event_stmt: NAME ":" KW_EVENT /[^\n]+/
message: NAME "->" NAME ":" /[^\n]+/

// --- data ----------------------------------------------------------
input_decl: KW_INPUT NAME ":" string_list
split_decl: KW_SPLIT NAME ":" STRING
string_list: STRING ("|" STRING)*

// --- student-written functions --------------------------------------
// `def` and the return type are both required. Every name a student introduces
// carries a written type — that is what lets a job check that a function fits
// the position it was passed to, and there is no privileged set of names: a
// mapper is a mapper because it was passed as one.
func_def: KW_DEF NAME "(" [params] ")" "->" TYPE ":" _NL+ _INDENT (stmt | _NL)+ _DEDENT
params: param ("," param)*
param: NAME ":" TYPE
TYPE.7: /(?:int|string|\[int\]|\[string\]|\[pair\]|pair|void)/
?stmt: for_stmt | if_stmt | emit_stmt | let_stmt | return_stmt | expr_stmt
return_stmt: KW_RETURN expr _NL
let_stmt: NAME ":" TYPE "=" expr _NL
for_stmt: KW_FOR NAME ":" TYPE KW_IN expr ":" _NL+ _INDENT (stmt | _NL)+ _DEDENT
if_stmt: KW_IF expr ":" _NL+ _INDENT (stmt | _NL)+ _DEDENT
emit_stmt: KW_EMIT "(" expr "," expr ")" _NL
expr_stmt: expr _NL

// --- RDD pipelines --------------------------------------------------
assign: NAME "=" source chain?
source: NAME "(" [args] ")"   -> source_call
      | NAME                  -> source_ref
chain: call+
call: "." NAME "(" [args] ")"
action: NAME "." NAME "(" [args] ")"
// An argument is a value or a named option: `Map("chunk001.txt", deadline=0.5)`
// and `MapReduce(map=tokenize, reduce=total)` are the same shape.
args: arg ("," arg)*
?arg: kwarg | expr

// --- checks -----------------------------------------------------------
assertion: KW_ASSERT NAME "." KW_CLOCK "==" vector    -> assert_clock
         | KW_ASSERT NAME "||" NAME                   -> assert_concurrent
         | KW_ASSERT NAME "->>" NAME                  -> assert_before
         | KW_EXPECT ATOMIC "=" NUMBER                -> expect
vector: "[" [NUMBER ("," NUMBER)*] "]"
budget: KW_BUDGET NAME COMPARE NUMBER
COMPARE: "<=" | ">=" | "<" | ">"
lose: KW_LOSE NAME [KW_ON NAME]
note: KW_NOTE /[^\n]+/

// --- expressions ------------------------------------------------------
// Spark takes its functions as lambdas, so the notation accepts the shape a
// student already knows. Only the *shape* is grammar: what a lambda means is
// Python's, read from the source it spans, which is what keeps this rule from
// having to grow every time PySpark does.
?expr: lambda_expr | conditional
lambda_expr: KW_LAMBDA [lambda_params] ":" expr
lambda_params: NAME ("," NAME)*
// The alias belongs on the long alternative alone. Putting it on an optional
// group aliased *every* expression as a conditional, so `speed=1.0` stopped
// being a number and ten test suites went red.
?conditional: or_expr
            | or_expr KW_IF or_expr KW_ELSE expr -> ifexp
?or_expr: and_expr ("or" and_expr)*
?and_expr: comparison ("and" comparison)*
// A comprehension is how a lambda reshapes a list, and Spark code is full of
// them. Only the shape is here; what it computes is Python's, read from the
// source the argument spans.
comp_for: KW_FOR comp_targets KW_IN or_expr comp_if*
comp_targets: NAME ("," NAME)*
comp_if: KW_IF or_expr

?comparison: sum ((COMPARE_OP | KW_IN | KW_NOT_IN) sum)*
COMPARE_OP: "==" | "!=" | "<=" | ">=" | "<" | ">"
KW_NOT_IN.7: /not\s+in\b/
?sum: product (SUM_OP product)*
SUM_OP: "+" | "-"
?product: unary (MUL_OP unary)*
// A leading minus. Its absence is why `r[:-1]` — the ordinary way to say
// "all but the last" — could not be written at all.
?unary: SUM_OP unary -> unary_op
      | atom
MUL_OP: "*" | "/" | "mod"
?atom: atom "[" expr "]"  -> index
     | atom "[" [expr] ":" [expr] "]" -> slice_of
     | atom "." NAME "(" [args] ")" -> remote_call
     | NUMBER            -> number
     | STRING            -> string
     | NAME "(" [args] ")" -> func_call
     | NAME              -> var
     | "[" [expr ("," expr)*] "]" -> list_lit
     | "[" expr comp_for "]" -> list_comp
     | "(" expr "," [expr ("," expr)*] ")" -> tuple_lit
     | "(" expr ")"
ATOMIC.-1: /[A-Za-z0-9_.\-]+/   // lowest priority: a catch-all payload token

// Keywords, declared explicitly so they always beat NAME in the lexer.
KW_INPUT.6: /input\b/
KW_SPLIT.6: /split\b/
KW_ASSERT.6: /assert\b/
KW_EXPECT.6: /expect\b/
KW_BUDGET.6: /budget\b/
KW_NOTE.6: /note\b/
KW_LOSE.6: /lose\b/
KW_ON.6: /on\b/
KW_SPEED.6: /speed\b/
KW_COMBINER.6: /combiner\b/
KW_CLOCK.6: /clock\b/
KW_FOR.6: /for\b/
KW_IN.6: /in\b/
KW_IF.6: /if\b/
KW_ELSE.6: /else\b/
KW_LAMBDA.6: /lambda\b/
KW_EMIT.6: /emit\b/
KW_PROCESS.6: /process\b/
KW_EVENT.6: /event\b/
KW_DEF.6: /def\b/
KW_RETURN.6: /return\b/
KW_USE.6: /use\b/
KW_CLASS.6: /class\b/
KW_PASS.6: /pass\b/

NAME.1: /[a-zA-Z_][a-zA-Z0-9_\-]*/
// A triple-quoted string carries an embedded sub-language — the PySpark a
// student writes. Keeping it one token is the whole point: the block is opaque
// to this grammar, so Python's own parser reads it and nothing about Spark
// leaks into the rules that MapReduce and the clocks exercises share.
STRING: /\"\"\".*?\"\"\"/s | /"[^"]*"/
NUMBER.2: /\d+(\.\d+)?/

_NL: /(\r?\n[\t ]*)+/
COMMENT: /#[^\n]*/
%import common.WS_INLINE
%ignore WS_INLINE
%ignore COMMENT
%declare _INDENT _DEDENT
"""


def _indenter():
    """Lark's indentation post-lexer, configured for our block syntax."""
    from lark.indenter import Indenter

    class DsvizIndenter(Indenter):
        NL_type = "_NL"
        OPEN_PAREN_types: list = []
        CLOSE_PAREN_types: list = []
        INDENT_type = "_INDENT"
        DEDENT_type = "_DEDENT"
        tab_len = 4

    return DsvizIndenter()


_parser = None


def parser() -> Lark:
    """The shared parser. Built once; Earley handles the ambiguity cheaply."""
    global _parser
    if _parser is None:
        _parser = Lark(GRAMMAR, parser="lalr", propagate_positions=True,
                       maybe_placeholders=True, postlex=_indenter())
    return _parser


def position(node) -> tuple[int, int]:
    """(line, column) for a tree node or token, 1-based."""
    if isinstance(node, Token):
        return node.line or 1, node.column or 1
    meta = getattr(node, "meta", None)
    if meta is not None and not getattr(meta, "empty", True):
        return meta.line, meta.column
    return 1, 1
