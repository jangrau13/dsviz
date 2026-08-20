"""
The Spark backend: real PySpark functions, run for real.

A pipeline is written as ordinary `.ds` statements. Only the functions are
PySpark, and they are the PySpark a student would write on a cluster:

    rows   = textFile("departures.csv")
    fields = rows.map(lambda row: row.split(","))
    late   = fields.filter(lambda f: int(f[1]) > 0)
    worst  = late.map(lambda f: (f[0], int(f[1]))).reduceByKey(lambda a, b: a + b)

There is no `SparkContext` to write. What one carries — the data to read and
the executors to read it on — the program already says: the inputs it declares
and the world it builds. Asking for `sc = SparkContext()` on top would be
asking a student to repeat themselves.

How a lambda is read, and why it is not in the grammar. The course grammar
accepts only the *shape* of an argument; the text it spans is sliced out of the
source and handed to Python's own parser. That split was chosen after the
alternative failed: teaching the shared Lark expression rules to speak Python
broke ten of twenty-five test suites, because Assignment 1's functions and
Assignment 3's clocks are written in those same rules — a machine's
`speed=1.0` stopped being a number. Slicing the source keeps PySpark exact
(`ast.parse` accepts what Python accepts) and keeps it out of everyone else's
way.

Two sub-languages, two evaluators, on purpose. `expr.py` stays strict — no
lambda, no attribute access, no indexing — because that is what Assignment 1's
function bodies are checked against, and `tests/sandbox_test.py` pins it there.
This module runs a different language, so it permits lambdas, whitelisted
string methods, indexing and comprehensions, and blocks the escape routes
itself. Neither walker ever calls `getattr` or `eval`, which is the property
that matters: method dispatch here is by name through a table, so there is no
route from a student's expression to an object's attributes.

What is modelled, rather than gestured at: narrow against wide, and therefore
real stage boundaries; the map-side combine that makes `reduceByKey` cheaper
than `groupByKey`; cost that follows the number of records, so dropping rows
before a shuffle actually pays; recomputation from lineage; and whether a
reducer would still give this answer if the rows had been split differently.

Checked against Apache Spark 4.1.3 on thirteen pipelines run on both engines.
Stage counts and the narrow/wide split agreed on all thirteen. Where results
disagreed, this module was wrong and was fixed — see `_apply`'s join and
intersection branches, and `_check_associative`.
"""

from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .notation import Diagnostic, NotationError


# One difference from a cluster that is deliberate rather than a defect: the
# operations here preserve the order records arrived in, through reduceByKey,
# groupByKey and distinct alike. Real Spark promises no such thing. A stable
# order makes a task's output readable and its expectations checkable; it also
# means the ordering a student sees is not one they should rely on, which is
# what `sortByKey` is for. Confirmed against Spark 4.1.3.


# --- the operation table ------------------------------------------------
#
# Whether an operation forces a shuffle is not a detail: it is what divides a
# job into stages, and what makes one line of a pipeline cost more than the
# four around it.

NARROW = {"map", "flatMap", "filter", "mapValues", "flatMapValues",
          "mapPartitions", "keys", "values", "sample", "union", "cache",
          "persist", "coalesce", "zipWithIndex"}

WIDE = {"reduceByKey", "groupByKey", "sortByKey", "aggregateByKey",
        "foldByKey", "combineByKey", "join", "leftOuterJoin",
        "rightOuterJoin", "cogroup", "distinct", "repartition",
        "partitionBy", "groupBy", "sortBy", "subtract", "intersection"}

# Wide operations that can reduce on the mapper before anything is sent. This
# is the difference between `reduceByKey` and `groupByKey`: both end up with
# one entry per key, but only one of them combines first, so only one of them
# ships every record across the network. It is the same argument the combiner
# makes in MapReduce, and it is invisible unless the traffic is counted.
COMBINES = {"reduceByKey", "foldByKey", "aggregateByKey", "combineByKey"}

# Actions, listed so they can be RECOGNISED — not so they can be run.
#
# A .ds program has no syntax for an action and is not getting one: the
# pipeline is handed to a job, the job is run in a world, and the result is
# read off the diagram. `out.collect()` is a line from a different program
# shape, and the useful thing to do with it is to say so by name.
#
# So this table exists for the two refusals. `total = out.count()` is told
# that an action does not make an RDD, and a bare `out.collect()` is told the
# work happens at world.run(...). Neither would be possible if the names were
# not written down somewhere.
#
# There was, until this comment, a third state: an `_act` function that
# implemented most of these and that nothing could call. It advertised a
# capability that could not be invoked, which is worse than either having the
# feature or not — a differential run reads it as untested surface and goes
# looking for behaviour that is not reachable. Deleted. If an action syntax is
# ever wanted, it comes back with a way to write it, in one piece.
ACTIONS = {"collect", "count", "first", "take", "reduce", "foreach",
           "saveAsTextFile", "countByKey", "collectAsMap", "sum", "mean",
           "max", "min", "takeOrdered"}

SOURCES = {"textFile", "parallelize", "wholeTextFiles", "range"}

TRANSFORMS = NARROW | WIDE


# --- the sandbox --------------------------------------------------------
#
# Everything a lambda is allowed to touch. Dispatch is by *name* through these
# tables and never through `getattr`, so there is no route from a student's
# expression to an object's attributes — which is the route that reaches
# `__class__` and from there anything at all.

def _method_split(s, sep=None, maxsplit=-1):
    return s.split(sep, maxsplit) if sep is not None else s.split()


# method name -> (types it is allowed on, implementation)
SAFE_METHODS: dict[str, tuple] = {
    "split":      ((str,), _method_split),
    "strip":      ((str,), lambda s, *a: s.strip(*a)),
    "lstrip":     ((str,), lambda s, *a: s.lstrip(*a)),
    "rstrip":     ((str,), lambda s, *a: s.rstrip(*a)),
    "lower":      ((str,), lambda s: s.lower()),
    "upper":      ((str,), lambda s: s.upper()),
    "title":      ((str,), lambda s: s.title()),
    "replace":    ((str,), lambda s, a, b: s.replace(a, b)),
    "startswith": ((str,), lambda s, p: s.startswith(p)),
    "endswith":   ((str,), lambda s, p: s.endswith(p)),
    "isdigit":    ((str,), lambda s: s.isdigit()),
    "isalpha":    ((str,), lambda s: s.isalpha()),
    "join":       ((str,), lambda s, xs: s.join(str(x) for x in xs)),
    "count":      ((str, list, tuple), lambda c, x: c.count(x)),
    "index":      ((str, list, tuple), lambda c, x: c.index(x)),
}

# Free functions a lambda may call. `sorted` is here rather than as a method
# because Spark code sorts values inside a reducer often enough to matter.
SAFE_FUNCS: dict[str, Any] = {
    "len": len, "int": int, "float": float, "str": str, "bool": bool,
    "abs": abs, "round": round, "sum": sum, "min": min, "max": max,
    "sorted": sorted, "list": list, "tuple": tuple, "set": set,
    "any": any, "all": all, "zip": lambda *xs: list(zip(*xs)),
    "dict": dict,
    "range": lambda *a: list(range(*a)),
    "enumerate": lambda xs, start=0: list(enumerate(xs, start)),
}


class Budget:
    """Caps evaluation so a wrong pipeline cannot hang the page."""

    def __init__(self, steps: int = 500_000):
        self.left = steps

    def spend(self, n: int = 1):
        self.left -= n
        if self.left <= 0:
            raise NotationError([Diagnostic(
                1, 1, "error", "this pipeline ran too long",
                hint="check for a very large input, or a lambda that builds "
                     "a much bigger record than it was given")])


_BINOPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARYOPS = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a,
             ast.Not: lambda a: not a}
_COMPARES = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


def _reject(node: ast.AST, line: int, why: str = "") -> NotationError:
    name = type(node).__name__
    return NotationError([Diagnostic(
        getattr(node, "lineno", 1) + line - 1, getattr(node, "col_offset", 0) + 1,
        "error", why or f"{name} is not allowed inside a pipeline",
        hint="a lambda may use arithmetic, comparisons, indexing, tuples, "
             "and the string methods Spark code normally uses")])


def evaluate(node: ast.AST, env: dict, line: int, budget: Budget):
    """
    Evaluate one expression node against `env`.

    A whitelist walker, not `eval`. Attribute access exists only in call
    position and only through `SAFE_METHODS`, so `row.__class__` is a parse
    of two nodes that this function refuses rather than a value it produces.
    """
    budget.spend()

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return node.value
        raise _reject(node, line)

    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in SAFE_FUNCS:
            return SAFE_FUNCS[node.id]
        raise NotationError([Diagnostic(
            node.lineno + line - 1, node.col_offset + 1, "error",
            f"unknown name {node.id!r}",
            hint="a lambda can only use its own parameters and the values "
                 "the pipeline gives it")])

    if isinstance(node, ast.Lambda):
        return _make_lambda(node, env, line, budget)

    if isinstance(node, ast.IfExp):
        return (evaluate(node.body, env, line, budget)
                if evaluate(node.test, env, line, budget)
                else evaluate(node.orelse, env, line, budget))

    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](evaluate(node.left, env, line, budget),
                                      evaluate(node.right, env, line, budget))

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](evaluate(node.operand, env, line, budget))

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            out = True
            for v in node.values:
                out = evaluate(v, env, line, budget)
                if not out:
                    return out
            return out
        out = False
        for v in node.values:
            out = evaluate(v, env, line, budget)
            if out:
                return out
        return out

    if isinstance(node, ast.Compare):
        left = evaluate(node.left, env, line, budget)
        for op, right_node in zip(node.ops, node.comparators):
            if type(op) not in _COMPARES:
                raise _reject(node, line)
            right = evaluate(right_node, env, line, budget)
            if not _COMPARES[type(op)](left, right):
                return False
            left = right
        return True

    if isinstance(node, (ast.List, ast.Tuple)):
        items = [evaluate(e, env, line, budget) for e in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)

    if isinstance(node, ast.Subscript):
        # Indexing is how a record's fields are picked apart, so it has to be
        # here. It is confined to the containers a record can actually be:
        # there is no object whose items lead anywhere.
        target = evaluate(node.value, env, line, budget)
        if isinstance(target, dict):
            # A dict is a container of plain values like any other here, and
            # looking one up reaches nothing an object could hide.
            key = evaluate(node.slice, env, line, budget)
            if key not in target:
                raise NotationError([Diagnostic(
                    node.lineno + line - 1, node.col_offset + 1, "error",
                    f"there is no key {key!r} here",
                    hint="keys present: "
                         + (", ".join(repr(k) for k in list(target)[:6]) or "none"))])
            return target[key]
        if not isinstance(target, (list, tuple, str)):
            raise _reject(node, line,
                          "only a list, a tuple, a string or a dict can be "
                          "indexed")
        index = _index_of(node.slice, env, line, budget)
        try:
            return target[index]
        except IndexError:
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"index {index} is past the end of this record",
                hint=f"the record here has {len(target)} field(s)")])

    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        out = _comprehension(node, env, line, budget)
        return set(out) if isinstance(node, ast.SetComp) else out

    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):        # {**other}
            raise _reject(node, line, "** is not allowed in a dict here")
        return {evaluate(k, env, line, budget): evaluate(v, env, line, budget)
                for k, v in zip(node.keys, node.values)}

    if isinstance(node, ast.Set):
        return {evaluate(e, env, line, budget) for e in node.elts}

    if isinstance(node, ast.JoinedStr):
        # An f-string. Every piece is evaluated by this same walker, and only
        # the plain values a record can hold are formatted, so `f"{x}"` cannot
        # reach an object's own __format__.
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                parts.append(str(piece.value))
                continue
            value = evaluate(piece.value, env, line, budget)
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise _reject(piece, line,
                              "only a number or a string can be formatted here")
            spec = (evaluate(piece.format_spec, env, line, budget)
                    if piece.format_spec is not None else "")
            parts.append(format(value, spec) if spec else str(value))
        return "".join(parts)

    if isinstance(node, ast.Call):
        return _call(node, env, line, budget)

    raise _reject(node, line)


def _index_of(node, env, line, budget):
    """An index or a slice, both restricted to integers."""
    if isinstance(node, ast.Slice):
        part = (lambda n: None if n is None else evaluate(n, env, line, budget))
        return slice(part(node.lower), part(node.upper), part(node.step))
    value = evaluate(node, env, line, budget)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _reject(node, line, "an index must be a whole number")
    return value


def _comprehension(node: ast.ListComp, env: dict, line: int, budget: Budget):
    """`[w for w in row.split() if w]`, and the nested form too.

    Nesting used to be refused, on the grounds that it could cost a page
    freeze. The budget already answers that — it caps evaluation whatever
    shape the expression is — so the restriction only ruled out Python that
    Spark code legitimately writes."""
    out: list = []

    def walk(index: int, scope: dict):
        if index == len(node.generators):
            out.append(evaluate(node.elt, scope, line, budget))
            return
        gen = node.generators[index]
        names = _targets(gen.target, line)
        for item in evaluate(gen.iter, scope, line, budget):
            budget.spend()
            if len(names) == 1:
                inner = {**scope, names[0]: item}
            else:
                values = list(item)
                if len(values) != len(names):
                    raise _reject(gen.target, line,
                                  f"this record has {len(values)} field(s), "
                                  f"but {len(names)} were named")
                inner = {**scope, **dict(zip(names, values))}
            if all(evaluate(c, inner, line, budget) for c in gen.ifs):
                walk(index + 1, inner)

    walk(0, env)
    return out


# `sorted(xs, key=lambda x: x[1])` is ordinary Spark code, so the handful of
# functions that take a key are allowed to be given one. Nothing else takes
# keywords: they are a way to reach a function's options, and these are the
# only options worth reaching.
KEYWORD_FUNCS = {"sorted": {"key", "reverse"}, "min": {"key"}, "max": {"key"},
                 "round": {"ndigits"}}


def _call(node: ast.Call, env: dict, line: int, budget: Budget):
    """A call to a whitelisted free function, a bound lambda, or a method."""
    if any(isinstance(a, ast.Starred) for a in node.args):
        raise _reject(node, line, "starred arguments are not allowed here")
    named = getattr(node.func, "id", "")
    allowed = KEYWORD_FUNCS.get(named, set())
    for kw in node.keywords:
        if kw.arg is None or kw.arg not in allowed:
            raise _reject(node, line,
                          f"{named or 'this call'}() does not take a "
                          f"{kw.arg!r} argument" if kw.arg else
                          "** arguments are not allowed here")
    args = [evaluate(a, env, line, budget) for a in node.args]
    options = {kw.arg: evaluate(kw.value, env, line, budget)
               for kw in node.keywords}

    if isinstance(node.func, ast.Attribute):
        # The only place an attribute may appear. The name is looked up in the
        # table; the object is never asked what it has.
        method = node.func.attr
        if method not in SAFE_METHODS:
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"method {method!r} is not available inside a pipeline",
                hint="available: " + ", ".join(sorted(SAFE_METHODS)))])
        target = evaluate(node.func.value, env, line, budget)
        types, impl = SAFE_METHODS[method]
        if not isinstance(target, types):
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"{method}() does not apply to {type(target).__name__}",
                hint=f"{method}() works on "
                     f"{' or '.join(t.__name__ for t in types)}")])
        try:
            return impl(target, *args)
        except NotationError:
            raise
        except Exception as e:
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"{method}() failed here: {e}")])

    if isinstance(node.func, ast.Name):
        fn = evaluate(node.func, env, line, budget)
        if not callable(fn):
            raise _reject(node, line, f"{node.func.id!r} is not a function")
        try:
            return fn(*args, **options)
        except NotationError:
            raise
        except Exception as e:
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"{node.func.id}() failed here: {e}")])

    raise _reject(node, line)


def _targets(node, line: int) -> list:
    """The names a `for` binds: one, or one per field of a pair."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple) and all(isinstance(e, ast.Name) for e in node.elts):
        return [e.id for e in node.elts]
    raise _reject(node, line, "the loop variable must be a name, or names")


def _make_lambda(node: ast.Lambda, env: dict, line: int, budget: Budget):
    """A lambda becomes a Python callable that the same walker evaluates."""
    a = node.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
        raise _reject(node, line,
                      "a lambda here takes plain positional parameters")
    names = [p.arg for p in a.args]
    defaults = [evaluate(d, env, line, budget) for d in a.defaults]

    def call(*args):
        budget.spend()
        if len(args) > len(names):
            # Spark hands a pair to a one-parameter lambda as one tuple; a
            # two-parameter one wants it spread. Saying which is which beats
            # a Python arity error the student cannot place.
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"this lambda takes {len(names)} parameter(s) but was "
                f"given {len(args)}",
                hint="reduceByKey passes two values; map and filter pass one")])
        filled = list(args) + defaults[len(args) - len(names):] if defaults else list(args)
        if len(filled) < len(names):
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"this lambda takes {len(names)} parameter(s) but was "
                f"given {len(args)}")])
        return evaluate(node.body, {**env, **dict(zip(names, filled))},
                        line, budget)

    return call


# --- the pipeline -------------------------------------------------------

@dataclass
class Step:
    """
    One RDD, and what it was made from.

    `data` is what it actually holds, because a pipeline that is not evaluated
    cannot tell a written transformation from an unwritten one. `stage` is
    what makes the picture worth looking at: it only advances across a wide
    operation, which is where Spark puts a shuffle.
    """
    name: str
    op: str
    parents: list = field(default_factory=list)
    data: list = field(default_factory=list)
    stage: int = 0
    wide: bool = False
    cached: bool = False
    line: int = 0
    named: bool = True          # False for an unnamed link in a chain
    # How many records each partition holds, when the program chose the split
    # itself. This is what makes a bad partitioner visible: the work follows
    # the partitions, so an unequal split is an unequal timeline rather than a
    # remark in the notes.
    partitions: list = field(default_factory=list)


@dataclass
class Pipeline:
    """A whole PySpark block, run."""
    steps: list = field(default_factory=list)         # in creation order
    actions: list = field(default_factory=list)       # (rdd, op, result, line)
    outputs: dict = field(default_factory=dict)       # what print() produced
    context: str = ""                                 # the SparkContext's name
    # Things that are true here but would not be on a cluster. A reducer that
    # is not associative is the one that matters: it makes this run look
    # reproducible when the real one is not.
    warnings: list = field(default_factory=list)

    def by_name(self, name: str):
        for s in reversed(self.steps):
            if s.name == name:
                return s
        return None

    @property
    def stages(self) -> int:
        return max((s.stage for s in self.steps), default=-1) + 1

    def named_steps(self) -> list:
        return [s for s in self.steps if s.named]


def _same(a, b) -> bool:
    """Whether two reduce results agree, allowing for float arithmetic."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
        except (TypeError, ValueError):
            return a == b
    return a == b


def _check_associative(fn, groups: dict, order: list, node, line: int,
                       budget: Budget) -> str:
    """
    Whether this reducer would give the same answer on a real cluster.

    Spark applies a reducer in whatever order the partitions happen to
    produce, and combines partial results from each. A function that is not
    associative and commutative therefore has no single answer — it has one
    per partitioning, and the one you see is an accident of the run.

    Folding left every time hides exactly that: the simulator was reproducible
    where a cluster is not, so a task written on `(a + b) / 2` looked correct
    here and was not correct there. Checked against Spark 4.1.3, where a
    pairwise mean over one key gave 6.0 on two partitions and 8.625 folded
    sequentially.
    """
    for key in order:
        values = groups[key]
        if len(values) < 3:
            continue
        budget.spend(len(values))

        def fold(items):
            acc = items[0]
            for nxt in items[1:]:
                acc = fn(acc, nxt)
            return acc

        try:
            sequential = fold(values)
            half = len(values) // 2
            regrouped = fn(fold(values[:half]), fold(values[half:]))
            swapped = fold(list(reversed(values)))
        except Exception:
            return ""                     # a broken reducer is reported elsewhere
        if not _same(sequential, regrouped):
            return ("this reducer is not associative: grouping the values "
                    f"differently gives {regrouped!r} instead of "
                    f"{sequential!r}")
        if not _same(sequential, swapped):
            return ("this reducer is not commutative: taking the values in "
                    f"another order gives {swapped!r} instead of "
                    f"{sequential!r}")
    return ""


def _partition(values: list, parts: int, rng) -> list:
    """Split a key's values the way a cluster happens to have split them.

    Not a round-robin: which records ended up together is an accident of how
    the input was read and shuffled, and the whole point is that a program may
    not depend on it. Seeded, so one run is reproducible while different seeds
    are different accidents.

    **This is the one place the simulator knowingly differs from Spark, and it
    differs on purpose.** Spark assigns a key's values to partitions by where
    the source records already were, so for a fixed input and partition count
    it gives the same grouping every time — and therefore the same answer,
    even from a reducer that has no right to one. Measured, `(a + b) / 2` over
    [1, 2, 9, 12]:

        real Spark   2 partitions -> 6.0   3 -> 6.0   4 -> 8.625
        simulator    a spread, 5.25 among them, different per seed

    Spark's three answers to one question are the honest evidence that the
    reducer is broken; a student who ran it once would see a stable number and
    conclude it was fine, and only sweeping partition counts would show
    otherwise — which no student does. Drawing the grouping fresh each run
    turns "you happened not to notice" into "you cannot help noticing".

    And it is not a different world: **at two partitions the spread already
    contains every answer Spark gave at any partition count**, 6.0 and 8.625
    both among them. So one cluster size here shows the student the whole set
    of things Spark would say across several. Matching Spark record-for-record
    would make the simulator agree with Spark and stop teaching the thing
    Spark is being used to teach.

    So a differential run *should* find this one disagreeing. It is the
    deliberate one. Everything else should match.
    """
    if parts <= 1 or len(values) < 2 or rng is None:
        return [list(values)]
    buckets: list = [[] for _ in range(min(parts, len(values)))]
    for value in values:
        buckets[rng.randrange(len(buckets))].append(value)
    return [b for b in buckets if b]


def _apply(op: str, args: list, data: list, node, line: int,
           budget: Budget, warnings: list | None = None,
           rng=None, partitions: int = 1) -> list:
    """Apply one transformation to a list of records."""
    fn = args[0] if args else None

    def need_fn():
        if not callable(fn):
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"{op}() needs a function",
                hint=f"write it as a lambda, e.g. {op}(lambda x: ...)")])
        return fn

    def pairs():
        for rec in data:
            if not (isinstance(rec, tuple) and len(rec) == 2):
                raise NotationError([Diagnostic(
                    node.lineno + line - 1, node.col_offset + 1, "error",
                    f"{op}() needs (key, value) pairs, but a record here is "
                    f"{type(rec).__name__}",
                    hint="make pairs first, e.g. map(lambda x: (key, value))")])
            yield rec

    if op in ("cache", "persist"):
        return data
    if op in ("coalesce", "repartition"):
        return data

    if op == "partitionBy":
        # Spark's is partitionBy(numPartitions, partitionFunc=portable_hash) —
        # the number comes first. Reading args[0] as the function meant
        # partitionBy(2), which is what the documentation shows, was refused
        # as "needs a function", while partitionBy(lambda …) was accepted and
        # runs nowhere else. Same failure as foldByKey: it succeeded while
        # teaching an API that does not exist.
        if not args or not isinstance(args[0], int) or isinstance(args[0], bool):
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                "partitionBy() takes numPartitions first",
                hint="write it as partitionBy(2), optionally with a "
                     "partitioning function after the number")])
        list(pairs())          # it is a pair operation, so insist on pairs
        # Which partition a record lands in is not modelled: Spark hashes the
        # key with portable_hash, and no Python hash reproduces that. What is
        # modelled is the shuffle it costs, which is why this is in WIDE.
        return list(data)

    if op == "sample":
        # sample(withReplacement, fraction, seed=None). This returned its
        # input unchanged whatever the fraction, so a pipeline that sampled
        # a tenth of the data went on to process all of it — and the cost
        # model priced the full volume too, so nothing looked wrong.
        if len(args) < 2 or not isinstance(args[1], (int, float)):
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                "sample() takes withReplacement, fraction",
                hint="write it as sample(False, 0.1) for a tenth of the "
                     "rows")])
        with_replacement, fraction = bool(args[0]), float(args[1])
        if fraction < 0:
            raise NotationError([Diagnostic(
                node.lineno + line - 1, node.col_offset + 1, "error",
                f"sample() needs a fraction of 0 or more, not {fraction}",
                hint="a fraction is a proportion of the rows, so 0.1 is a "
                     "tenth")])
        if rng is None:                 # nothing to draw with: keep it whole
            return list(data)
        out = []
        for rec in data:
            if with_replacement:
                # Spark draws a Poisson count per record; drawing repeatedly
                # from the same Bernoulli is close enough to show that a row
                # can appear more than once.
                count, draw = 0, fraction
                while draw > 0:
                    if rng.random() < min(draw, 1.0):
                        count += 1
                    draw -= 1.0
                out.extend([rec] * count)
            elif rng.random() < fraction:
                out.append(rec)
        return out

    if op == "map":
        f = need_fn()
        return [f(rec) for rec in data]

    if op == "flatMap":
        f = need_fn()
        out = []
        for rec in data:
            budget.spend()
            produced = f(rec)
            if isinstance(produced, (list, tuple)):
                out.extend(produced)
            else:
                out.append(produced)
        return out

    if op == "filter":
        f = need_fn()
        return [rec for rec in data if f(rec)]

    if op == "mapValues":
        f = need_fn()
        return [(k, f(v)) for k, v in pairs()]

    if op == "flatMapValues":
        f = need_fn()
        return [(k, item) for k, v in pairs() for item in f(v)]

    if op == "keys":
        return [k for k, _ in pairs()]
    if op == "values":
        return [v for _, v in pairs()]

    if op == "distinct":
        seen, out = set(), []
        for rec in data:
            if rec not in seen:
                seen.add(rec)
                out.append(rec)
        return out

    if op == "union":
        other = fn
        return list(data) + list(other if isinstance(other, list) else [])

    # --- the four combining operators, which are one operator ------------
    #
    # reduceByKey, foldByKey, aggregateByKey and combineByKey differ only in
    # where each partition's accumulator starts and whether the function that
    # folds a value in is the same one that merges two partitions. Spark's
    # signatures say so:
    #
    #   reduceByKey(func)                     start at the first value
    #   foldByKey(zeroValue, func)            start at zeroValue
    #   aggregateByKey(zero, seqFunc, comb)   start at zero, merge differently
    #   combineByKey(create, merge, mergeC)   start at create(first value)
    #
    # Writing them as one branch is not tidying: it is what makes the shape
    # visible. Every one of them reduces inside a partition and then merges
    # across partitions, which is the reason a combining operator is cheap and
    # the reason its function has to be associative and commutative.
    if op in COMBINES:
        def want(shape: str, seeds: int = 0):
            """Insist on Spark's own signature, and say it when it is missing.

            A student who copies the operator out of the Spark documentation
            must not be told they got it wrong. The previous code read the
            *first* argument as the function for every combining operator, so
            `foldByKey(0, lambda a, b: a + b)` — which is exactly what Spark
            takes — was rejected as "needs a function", while the form that
            works nowhere but here was accepted.
            """
            # `seeds` is how many leading arguments are values rather than
            # functions — the zeroValue that foldByKey and aggregateByKey
            # start each partition from. Everything after them must be
            # callable, and there must be exactly as many arguments as the
            # signature names.
            wanted = shape.count(",") + 1
            if (len(args) != wanted
                    or not all(callable(a) for a in args[seeds:])):
                raise NotationError([Diagnostic(
                    node.lineno + line - 1, node.col_offset + 1, "error",
                    f"{op}() takes {shape}",
                    hint=f"write it as {op}({shape})")])

        if op == "reduceByKey":
            want("func")
            init, seq, comb = None, args[0], args[0]
        elif op == "foldByKey":
            want("zeroValue, func", seeds=1)
            init, seq, comb = args[0], args[1], args[1]
        elif op == "aggregateByKey":
            want("zeroValue, seqFunc, combFunc", seeds=1)
            init, seq, comb = args[0], args[1], args[2]
        else:                                   # combineByKey
            want("createCombiner, mergeValue, mergeCombiners")
            init, seq, comb = args[0], args[1], args[2]

        order, collected = [], {}
        for k, v in pairs():
            if k not in collected:
                collected[k] = []
                order.append(k)
            collected[k].append(v)

        # Only where the accumulator and the value are the same thing. For
        # aggregateByKey and combineByKey the merge runs on accumulators, so
        # feeding it raw values would invent a complaint about a function that
        # is never called that way.
        if warnings is not None and op in ("reduceByKey", "foldByKey"):
            problem = _check_associative(comb, collected, order, node, line, budget)
            if problem:
                warnings.append(Diagnostic(
                    line, 1, "warning", problem,
                    hint="Spark combines partial results from each partition, "
                         "so a reducer has to give the same answer whatever "
                         "the order and grouping. A mean is sum divided by "
                         "count, not a fold of halves."))

        # Each partition reduces what it holds, then the partial results are
        # merged — which is what Spark does, and why a reducer that is not
        # associative and commutative has no single answer. Folding the whole
        # list left-to-right hid that: the simulator was reproducible where a
        # cluster is not, so a wrong reducer looked right.
        out = []
        for k in order:
            partials = []
            for bucket in _partition(collected[k], partitions, rng):
                if op == "reduceByKey":
                    acc, rest = bucket[0], bucket[1:]
                elif op == "combineByKey":
                    acc, rest = init(bucket[0]), bucket[1:]
                else:
                    # A zero shared between partitions must not be a zero they
                    # can each write into.
                    acc, rest = copy.deepcopy(init), bucket
                for nxt in rest:
                    budget.spend()
                    acc = seq(acc, nxt)
                partials.append(acc)
            acc = partials[0]
            for nxt in partials[1:]:
                budget.spend()
                acc = comb(acc, nxt)
            out.append((k, acc))
        return out

    if op == "mapPartitions":
        f = need_fn()
        # The whole point of the operator is that the function sees a
        # partition rather than a record, so the partitions have to be real.
        # These are contiguous, which is what `parallelize` gives you and what
        # makes the result reproducible; the random split is kept for the
        # combining operators, where varying it is the lesson.
        # Spark's own boundaries, which are `(i * n) // slices`. The rule
        # matters here more than anywhere else: partition boundaries are the
        # entire subject of this operator, so a student calling it to see what
        # a partition holds must be shown the partition Spark would give them
        # for the same parallelize. Handing out an even split with the
        # remainder at the front instead put [3,1,4,1,5] into [[3,1,4],[1,5]]
        # where Spark gives [[3,1],[4,1,5]] — same total, wrong subject.
        out = []
        n = max(1, min(partitions, len(data))) if data else 1
        total = len(data)
        for i in range(n):
            chunk = data[(i * total) // n:((i + 1) * total) // n]
            budget.spend()
            produced = f(iter(chunk))
            out.extend(list(produced))
        return out

    if op == "groupByKey":
        order, groups = [], {}
        for k, v in pairs():
            groups.setdefault(k, []).append(v)
            if k not in order:
                order.append(k)
        return [(k, groups[k]) for k in order]

    if op == "groupBy":
        f = need_fn()
        order, groups = [], {}
        for rec in data:
            k = f(rec)
            groups.setdefault(k, []).append(rec)
            if k not in order:
                order.append(k)
        return [(k, groups[k]) for k in order]

    if op == "sortByKey":
        return sorted(pairs(), key=lambda kv: kv[0])

    if op == "sortBy":
        f = need_fn()
        return sorted(data, key=f)

    if op in ("join", "leftOuterJoin", "rightOuterJoin", "cogroup"):
        # A join is a product per key, not a lookup. Building the right side
        # as a dict kept only the last value for each key, so a join could
        # never produce more rows than the left side had — two repeated keys
        # on the right returned 2 rows where Spark returns 4. Checked against
        # Spark 4.1.3.
        right: dict = {}
        right_order = []
        for rec in (fn or []):
            if not (isinstance(rec, tuple) and len(rec) == 2):
                raise NotationError([Diagnostic(
                    node.lineno + line - 1, node.col_offset + 1, "error",
                    f"{op}() needs (key, value) pairs on both sides")])
            k, v = rec
            if k not in right:
                right[k] = []
                right_order.append(k)
            right[k].append(v)

        left: dict = {}
        left_order = []
        for k, v in pairs():
            if k not in left:
                left[k] = []
                left_order.append(k)
            left[k].append(v)

        if op == "cogroup":
            keys = left_order + [k for k in right_order if k not in left]
            return [(k, (left.get(k, []), right.get(k, []))) for k in keys]

        out = []
        if op in ("join", "leftOuterJoin"):
            for k in left_order:
                partners = right.get(k)
                if partners:
                    out += [(k, (v, w)) for v in left[k] for w in partners]
                elif op == "leftOuterJoin":
                    out += [(k, (v, None)) for v in left[k]]
        else:                                     # rightOuterJoin
            for k in right_order:
                partners = left.get(k)
                if partners:
                    out += [(k, (v, w)) for v in partners for w in right[k]]
                else:
                    out += [(k, (None, w)) for w in right[k]]
        return out

    if op == "subtract":
        drop = set(fn or [])
        return [rec for rec in data if rec not in drop]

    if op == "intersection":
        # Spark's intersection returns distinct elements; `subtract` does not.
        # Keeping duplicates here made [1,1,2,3,3] ∩ [1,3,4] answer [1,1,3,3]
        # where Spark answers [1,3]. Checked against Spark 4.1.3.
        keep = set(fn or [])
        seen, out = set(), []
        for rec in data:
            if rec in keep and rec not in seen:
                seen.add(rec)
                out.append(rec)
        return out

    if op == "zipWithIndex":
        return [(rec, i) for i, rec in enumerate(data)]

    if op == "partitionBy":
        # Choosing the split is the point: everything that must be compared
        # together has to land together. The records are unchanged — what
        # changes is which machine holds them, so they come back grouped by
        # the partition the function chose.
        f = need_fn()
        placed = []
        for k, v in pairs():
            budget.spend()
            where = f(k)
            if not isinstance(where, int) or isinstance(where, bool):
                raise NotationError([Diagnostic(
                    line, 1, "error",
                    "partitionBy needs a whole number for each key",
                    hint="return which partition the key belongs in, "
                         "e.g. partitionBy(lambda k: hash(k) % 4)")])
            placed.append((where, (k, v)))
        placed.sort(key=lambda item: item[0])
        return [rec for _, rec in placed]

    raise NotationError([Diagnostic(
        node.lineno + line - 1, node.col_offset + 1, "error",
        f"{op}() cannot be applied here")])


def resolve_input(name, inputs: dict, line: int, whole: bool = False):
    """
    What `textFile("…")` reads.

    A name is looked up in what the program declared first, so the same
    pipeline can be re-run on input it has not seen — which is how a hand-in is
    graded. Only then is it looked for on disk, which is what lets a task ship
    a corpus next to itself.

    **Blank lines are kept.** Spark's `textFile` yields one record per line
    including the empty ones, and dropping them changed the answer to a word
    count: the empty string came out with a count of 5 where Spark said 6.
    Worse, the two paths through this function disagreed with each other — a
    declared input kept its blank lines and a file on disk did not, so the same
    program gave different answers depending on where the data came from.
    Tidier task data is not worth a student's file counting differently here
    than it would on a cluster.

    With `whole=True` the file arrives as one string, newlines and all, which
    is what `wholeTextFiles` means: the record is the file verbatim, so the
    final byte belongs in it.
    """
    import pathlib as _pathlib

    key = str(name).strip('"').strip("'")
    for candidate in (key, _pathlib.Path(key).stem):
        if candidate in inputs:
            value = inputs[candidate]
            if whole:
                return ("\n".join(value) if isinstance(value, list)
                        else str(value))
            return list(value) if isinstance(value, list) else str(value).splitlines()
    # Data files belong to the exercise, not to dsviz: the package ships no
    # tasks, so where they live is whatever exercise was loaded. `TASKS` is
    # None until one has been, which is an ordinary state — free play declares
    # its own input rather than reading a file.
    from . import assignment
    if assignment.TASKS is not None:
        path = assignment.TASKS / key
        if path.exists():
            text = path.read_text()
            return text if whole else text.splitlines()
    raise NotationError([Diagnostic(
        line, 1, "error", f"there is no input called {key!r}",
        hint="declare it, e.g. 'input rows: \"…\"', or name a file the task "
             "ships; declared here: " + (", ".join(sorted(inputs)) or "none"))])


def _arg(text: str, line: int, budget: Budget):
    """One argument, as the value the text denotes.

    Read by Python's own parser. That is the point of keeping the source: a
    lambda means exactly what it means in PySpark, and a student pasting from
    the Spark documentation gets Python's answer rather than an approximation
    of it.
    """
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        raise NotationError([Diagnostic(
            line, (e.offset or 1), "error",
            f"this is not valid Python: {e.msg}",
            hint="the functions a transformation takes are real PySpark "
                 "lambdas, so Python's own rules apply")])
    return evaluate(tree.body, {}, line, budget)


def build(rdds: list, inputs: dict, *, budget: Budget | None = None,
          rng=None, partitions: int = 1) -> Pipeline:
    """
    Run the pipeline a program declares, and return what it produced.

    There is no `SparkContext` to write. What one would carry — the data to
    read and the executors to read it on — is what the program already says:
    the inputs it declares and the world it builds. Making the student write
    `sc = SparkContext()` would be asking them to repeat it.

    Every transformation becomes a step, not only the ones that were given a
    name, so a stage boundary inside a chain is visible instead of collapsed
    into whichever operation came last.
    """
    budget = budget or Budget()
    pipe = Pipeline(context="sc")
    known: dict = {}

    def collected() -> dict:
        """Earlier results, as the driver would hold them.

        A function handed to a transformation can name an RDD built earlier,
        and gets the records that RDD holds. That is what a real driver does
        between rounds of an iterative job: it collects the last result and
        closes over it, so the loop is sequential on one machine while each
        pass over the data stays parallel.

        Without it there was no way to feed a round's output into the next
        one, and the only way to write k-means was to run it once and paste
        the centroids back in as literals — code fitted to the data it was
        developed on, which is exactly what the held-out run exists to catch.
        """
        return {name: list(step.data) for name, step in known.items()}

    for rdd in rdds:
        steps = rdd.steps or [(rdd.op, [])]
        if rdd.parents:
            parent = known.get(rdd.parents[0])
            if parent is None:
                raise NotationError([Diagnostic(
                    rdd.line, 1, "error", f"unknown RDD {rdd.parents[0]!r}",
                    hint="defined so far: " + (", ".join(known) or "none"))])
        else:
            parent = None

        for i, (op, args) in enumerate(steps):
            last = i == len(steps) - 1
            name = rdd.var if last else f"{rdd.var}·{op}"

            if parent is None:
                if op not in SOURCES:
                    raise NotationError([Diagnostic(
                        rdd.line, 1, "error",
                        f"a pipeline starts by reading something, not with {op!r}",
                        hint="start with textFile(\"…\") or parallelize([…])")])
                values, _ = (parse_arguments(args[0], rdd.line, budget,
                                             collected())
                             if args else ([], {}))
                if op == "parallelize":
                    data = list(values[0]) if values else []
                elif op == "range":
                    # sc.range(start, end=None, step=1), like Python's. It was
                    # in SOURCES and fell through to resolve_input, which read
                    # the number as a filename and produced a pipeline with no
                    # rows — an empty answer rather than an error.
                    try:
                        data = list(range(*[int(v) for v in values]))
                    except (TypeError, ValueError):
                        raise NotationError([Diagnostic(
                            rdd.line, 1, "error",
                            "range() takes whole numbers",
                            hint="write it as range(10), or "
                                 "range(1, 10, 2)")]) from None
                elif op == "wholeTextFiles":
                    # Spark gives (path, entire contents) rather than a row per
                    # line, which is the whole difference from textFile: the
                    # file arrives as one record, so it cannot be split across
                    # machines.
                    key = str(values[0]) if values else ""
                    data = [(key, resolve_input(key, inputs, rdd.line,
                                                whole=True))]
                else:
                    data = resolve_input(values[0] if values else "",
                                         inputs, rdd.line)
                step = Step(name=name, op=op, parents=[], data=data,
                            stage=0, line=rdd.line, named=last)
            else:
                if op in ACTIONS:
                    raise NotationError([Diagnostic(
                        rdd.line, 1, "error",
                        f"{op}() is an action, so it does not make an RDD",
                        hint=f"run the job, then read the result; {op}() is "
                             "not part of building the pipeline")])
                if op not in TRANSFORMS:
                    raise NotationError([Diagnostic(
                        rdd.line, 1, "error", f"unknown transformation {op!r}",
                        hint="try one of: " + ", ".join(sorted(TRANSFORMS)))])
                # A stage is a property of where this step sits in the
                # lineage, not of how far the file has been read. Carrying a
                # running maximum instead put a second branch off a cached RDD
                # into whatever stage the first branch had reached, so an
                # iterative job appeared to gain a stage per round that it does
                # not actually have.
                stage = parent.stage + 1 if op in WIDE else parent.stage
                # An RDD named as an argument — `left.join(right)` — arrives
                # as its records rather than as an unknown name.
                blob = args[0] if args else ""
                named = known.get(blob.strip())
                if named is not None:
                    values, options = [named.data], {}
                else:
                    values, options = parse_arguments(
                        blob, rdd.line, budget, collected()) \
                        if blob.strip() else ([], {})
                values = list(values) + list(options.values())
                data = _apply(op, values, parent.data,
                              _Where(rdd.line), rdd.line, budget,
                              warnings=pipe.warnings, rng=rng,
                              partitions=partitions)
                sizes = []
                # partitionBy(numPartitions, partitionFunc). Only the
                # function can say how big each partition comes out, and it is
                # optional — Spark defaults to portable_hash, which is not
                # reproducible here. With no function there is nothing to size
                # with, so the step keeps the sizes it already had rather than
                # inventing them. This read values[0] as the partitioner,
                # which was true before the signature was corrected and became
                # `TypeError: 'int' object is not callable` after.
                if op == "partitionBy" and len(values) > 1 and callable(values[1]):
                    counts: dict = {}
                    for key, _ in data:
                        where = values[1](key)
                        counts[where] = counts.get(where, 0) + 1
                    sizes = [counts[k] for k in sorted(counts)]
                step = Step(name=name, op=op, parents=[parent.name], data=data,
                            stage=stage, wide=op in WIDE,
                            cached=op in ("cache", "persist"),
                            line=rdd.line, named=last, partitions=sizes)
            pipe.steps.append(step)
            parent = step
        known[rdd.var] = parent

    if not pipe.steps:
        raise NotationError([Diagnostic(
            1, 1, "error", "there is no pipeline here",
            hint="read some input, e.g. rows = textFile(\"…\")")])
    return pipe


class _Where:
    """Carries a line number for the error messages `_apply` raises."""

    def __init__(self, line: int):
        self.lineno = 1
        self.col_offset = 0
        self._line = line


# --- running it on a world ----------------------------------------------

def simulate(pipe: Pipeline, cluster, executors: list, *, lose: str = "",
             step_cost: float = 0.15, shuffle_cost: float = 0.25):
    """
    Run a pipeline's steps across a world's executors, and record what happened.

    Two things are modelled rather than gestured at, because they are what
    Assignment 2 asks students to see.

    *Stages.* A stage holds every step that can be pipelined without moving
    data. Its steps run together, and the stage ends at a barrier, because the
    wide operation that follows cannot start until every partition of the
    previous one exists.

    *Cost follows the data.* A step's work is proportional to the number of
    records it handles, and a shuffle's cost to the number crossing the
    boundary. So moving a filter earlier makes the shuffle cheaper here in the
    same way it does on a cluster — the timeline moves, rather than the
    student being told it would.

    A record costs more to ship than to compute on, and compute is shared
    between the executors while a shuffle is not. That ordering is the reason
    dropping records before a wide operation pays for the extra pass it takes,
    and a model where shipping were free would have taught the opposite.
    """
    from .patterns import Lineage

    lineage = Lineage()
    for step in pipe.steps:
        lineage.rdd(step.name, parents=step.parents, op=step.op)

    def live() -> list:
        return [m for m in executors if m.up_at(m.clock)]

    by_stage: dict[int, list] = {}
    for step in pipe.steps:
        by_stage.setdefault(step.stage, []).append(step)

    lost: list = []
    order = sorted(by_stage)
    for i, stage in enumerate(order):
        for step in by_stage[stage]:
            crew = live()
            if not crew:
                cluster.note(f"every executor is down — {step.name} cannot be computed")
                break
            # Split across the executors: each takes a share, so a slow one
            # shows up as a straggler rather than being averaged away.
            duration = max(len(step.data), 1) * step_cost / len(crew)
            for machine in crew:
                machine.work(f"{step.name} ({step.op})", duration=duration)
                if not machine.alive:
                    # It was holding a partition of this step. The lineage says
                    # how to make that partition again, which is the entire
                    # argument for writing the lineage down.
                    lost.append((step.name, machine.name))
                    cluster.note(f"{machine.name} died on {step.name} — its "
                                 f"partition goes back through the lineage")
            if step.cached:
                for machine in live():
                    machine.hold(step.name)

        # The next stage begins with a wide operation, so the data has to move.
        if i + 1 < len(order):
            leaving = by_stage[stage][-1].data
            wide = next((s.op for s in by_stage[order[i + 1]] if s.wide), "shuffle")
            if wide in COMBINES:
                # Combined on the mapper: one partial result per key travels,
                # not one per record.
                #
                # This overstates the advantage. Measured on Spark 4.1.3 with
                # distinct values and shuffle compression off, 200k records
                # over 471 keys moved 102x less through reduceByKey than
                # through groupByKey; counting one record per key predicts
                # 425x. The direction and the order of magnitude are right and
                # the lesson survives, but the number is not a measurement.
                #
                # If you re-measure: do not use word count. Its payload is
                # 200k identical 1s, which LZ4 flattens to nothing, and the
                # comparison then reads 1.5x — a fact about the compressor
                # rather than about Spark.
                keys = {r[0] for r in leaving if isinstance(r, tuple) and len(r) == 2}
                crossing = max(len(keys) or len(leaving), 1)
            else:
                crossing = max(len(leaving), 1)
            crew = live()
            for n, machine in enumerate(crew):
                target = crew[(n + 1) % len(crew)]
                if target is not machine:
                    machine.send(target, f"{wide}: {crossing} records",
                                 latency=crossing * shuffle_cost)
        cluster.barrier(f"stage {stage + 1}")

    # An RDD that feeds more than one branch is computed once per branch —
    # unless it was cached, which is the entire reason `cache()` is a line
    # worth writing. Without this, a job that reads the same data twice cost
    # the same as one that read it once, and taking the cache away changed
    # nothing on the timeline, so the lesson had to be taken on faith.
    consumers: dict[str, int] = {}
    for step in pipe.steps:
        for parent in step.parents:
            consumers[parent] = consumers.get(parent, 0) + 1
    for step in pipe.steps:
        extra = consumers.get(step.name, 0) - 1
        if extra <= 0:
            continue
        if step.cached or any(a.cached for a in _ancestors(pipe, step.name)):
            cluster.note(f"{step.name} is cached — its {extra + 1} readers "
                         f"share one copy instead of recomputing it")
            continue
        again = [n for n in lineage.recompute_set(step.name)]
        cluster.note(f"{step.name} is read {extra + 1} times and is not cached "
                     f"— {' → '.join(again)} runs {extra + 1} times")
        for _ in range(extra):
            crew = live()
            if not crew:
                break
            for node in again:
                other = pipe.by_name(node)
                crew[0].work(f"{node} again (not cached)",
                             duration=max(len(other.data) if other else 1, 1)
                             * step_cost / len(crew))

    # Anything the program asked to lose, plus anything a crash took with it.
    if lose:
        holder = next((m for m in executors if m.alive), None)
        if holder is not None:
            lost.append((str(lose), holder.name))

    for name, who in lost:
        if name not in lineage.g:
            cluster.note(f"there is no step called {name!r} to lose")
            continue
        machine = cluster.machines.get(who) or executors[0]
        if machine.alive:
            machine.crash(at=machine.clock, lose_state=True)
        # What a loss costs is what depended on it. The ancestors are still
        # on live executors — only this partition went — so the rebuild starts
        # from them and runs forward through everything already derived from
        # the lost data. Replaying the ancestors instead made an early loss
        # the cheap one and losing the source of a pipeline free, which is the
        # opposite of what Assignment 2 asks the student to observe.
        needed = lineage.rebuild_set(name)
        cluster.note(f"lost {name} — recomputing {' → '.join(needed)} "
                     f"from lineage, not from disk")
        if machine.on_crash == "restart":
            machine.restart(at=machine.clock + machine.restart_after)
        else:
            # It stays down, so the work moves. Recomputation does not need
            # *that* machine back, only the lineage.
            machine = next((m for m in executors if m.alive), machine)
        for node in needed:
            if not machine.alive:
                machine = next((m for m in executors if m.up_at(m.clock)), None)
                if machine is None:
                    cluster.note(f"nothing left alive to recompute {node} on")
                    break
            step = pipe.by_name(node)
            machine.work(f"recompute {node}",
                         duration=max(len(step.data) if step else 1, 1) * step_cost)

    for rdd, op, result, line in pipe.actions:
        shown = result if not isinstance(result, list) else f"{len(result)} record(s)"
        cluster.note(f"{op}() on {rdd} → {shown}")
    return cluster


def _ancestors(pipe: Pipeline, name: str) -> list:
    """Every step `name` was built from, itself included."""
    out, seen = [], set()
    stack = [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        step = pipe.by_name(current)
        if step is None:
            continue
        out.append(step)
        stack.extend(step.parents)
    return out


def explain(line_text: str, line_no: int, column: int = 1) -> Diagnostic | None:
    """
    A better message for a pipeline line the grammar could not read.

    The grammar's own answer is "syntax error here", which is true and useless
    when the thing it could not read is a lambda. Python's parser is asked
    instead: if it also refuses, its message and column are the real ones. If
    it accepts, the expression is valid Python that this language does not
    allow — which is a different sentence and the one worth showing.
    """
    text = line_text.split("#", 1)[0].strip()
    if not text:
        return None
    expression = text.split("=", 1)[1].strip() if "=" in text.split("(", 1)[0] \
        else text
    if "lambda" not in text and not any(
            f".{op}(" in text for op in TRANSFORMS | ACTIONS | SOURCES):
        return None
    try:
        ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return Diagnostic(
            line_no, (e.offset or column), "error",
            f"this is not valid Python: {e.msg}",
            hint="the functions a transformation takes are real PySpark "
                 "lambdas, so Python's own rules apply")
    return Diagnostic(
        line_no, column, "error",
        "this is valid Python, but not something a pipeline may do",
        hint="a lambda may use arithmetic, comparisons, indexing, tuples, "
             "and the string methods Spark code normally uses — no attribute "
             "access, imports or calls to anything else")


# --- keeping PySpark out of the grammar ---------------------------------

# The operations whose arguments are PySpark. A leading dot is required, and
# that is not cosmetic: `sum`, `min`, `max`, `count`, `first` and `take` are
# all Spark actions *and* the names of ordinary builtins that Assignment 1's
# functions call directly. Matching those without the dot would blank out the
# body of a function this module has no business touching — and it would do it
# silently, because the result still parses.
_METHOD_CALL = re.compile(r"\.\s*(" + "|".join(sorted(TRANSFORMS | ACTIONS)) + r")\s*\(")

# A pipeline's first step. These two name a source and nothing else, so they
# are recognised without a dot.
_SOURCE_CALL = re.compile(r"\b(" + "|".join(sorted(SOURCES)) + r")\s*\(")


def _comment_spans(source: str) -> list:
    """Where a comment runs on each line. A call inside one is prose."""
    spans, at = [], 0
    for raw in source.splitlines(keepends=True):
        quote = ""
        for i, ch in enumerate(raw):
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                spans.append((at + i, at + len(raw)))
                break
        at += len(raw)
    return spans


def _closing(source: str, start: int) -> int:
    """The index of the ')' that closes the '(' just before `start`."""
    depth, i, quote = 1, start, ""
    while i < len(source) and depth:
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2                      # an escaped character, whatever it is
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        i += 1
    return i - 1 if depth == 0 else -1


def mask_arguments(source: str) -> tuple[str, dict]:
    """
    Hide every PySpark argument list from the course grammar.

    Each argument list is replaced by a run of underscores of exactly the same
    length, so the masked text has the same lines, the same columns and the
    same length as the original. That is what lets the grammar report a
    position the editor can still use, and lets the argument be recovered by
    slicing the original at the same offsets.

    The grammar therefore never sees a lambda, a comprehension, a slice or a
    tuple, and does not need rules for any of them — which is the point. Those
    rules had to live in the *shared* expression grammar, where Assignment 1's
    functions and Assignment 3's clocks are also written, and every one of them
    was a chance to break those. Here the two languages cannot reach each other:
    nothing is hidden in a program that has no pipeline in it.

    Returns the masked source and {start offset: (end offset, original text)}.
    """
    comments = _comment_spans(source)

    def in_comment(pos: int) -> bool:
        return any(a <= pos < b for a, b in comments)

    hits = []
    for pattern in (_METHOD_CALL, _SOURCE_CALL):
        for m in pattern.finditer(source):
            if not in_comment(m.start()):
                hits.append(m.end())
    hits.sort()

    out, spans, guard = list(source), {}, 0
    for start in hits:
        if start < guard:
            continue                        # already inside a hidden argument
        end = _closing(source, start)
        if end < 0:
            continue                        # unbalanced: let the grammar say so
        text = source[start:end]
        if not text.strip():
            continue                        # `.cache()` has nothing to hide
        # Newlines are kept so line numbers survive; everything else becomes
        # an underscore, which the grammar reads as one ordinary name.
        out[start:end] = [("\n" if ch == "\n" else "_") for ch in text]
        spans[start] = (end, text)
        guard = end
    return "".join(out), spans


def parse_arguments(text: str, line: int, budget: Budget,
                    env: dict | None = None) -> tuple:
    """
    One hidden argument list, as the values it denotes.

    Split by Python rather than by counting brackets. A hand-written splitter
    got `lambda a, b: a + b` wrong — a lambda's own parameter comma sits at
    bracket depth zero and reads exactly like a separator. Wrapping the text in
    a call and letting Python parse it removes the question.
    """
    try:
        tree = ast.parse("_f(" + text + ")", mode="eval")
    except SyntaxError as e:
        raise NotationError([Diagnostic(
            line, (e.offset or 1), "error",
            f"this is not valid Python: {e.msg}",
            hint="the functions a transformation takes are real PySpark "
                 "lambdas, so Python's own rules apply")])
    call = tree.body
    scope = env or {}
    args = [evaluate(a, scope, line, budget) for a in call.args]
    options = {kw.arg: evaluate(kw.value, scope, line, budget)
               for kw in call.keywords if kw.arg}
    return args, options
