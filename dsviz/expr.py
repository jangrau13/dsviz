"""
The expression sub-language.

This is what runs inside the functions a student writes. It is deliberately
small and total: no loops, no recursion, no I/O, no unbounded computation.
Everything terminates, so a wrong submission is wrong rather than hanging the
browser tab.

    def warmest(city: string, readings: [int]) -> int:
        return max(readings)

The example is in a domain no task uses, and that is a rule rather than a
whim: this module ships to students, so whatever it demonstrates is teaching
material. Nothing in dsviz that a student can read may show the body of a
function a task asks them to write. `tests/docs_test.py` enforces it.

Typed: every value is int, string, list or pair, checked before it runs. That
is what makes a mistake a compile error at a line number rather than a
confusing result three phases later.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import Any

from .notation import Diagnostic, NotationError


# --- values -------------------------------------------------------------

class VType:
    """
    The types a student's expression can have.

    Deliberately few. `[int]` and `[string]` are distinct because the mistakes
    worth catching are exactly the ones that confuse a list of counts with a
    list of words.
    """
    INT = "int"
    STR = "string"
    LIST_INT = "[int]"
    LIST_STR = "[string]"
    LIST_PAIR = "[pair]"
    PAIR = "pair"
    VOID = "void"
    ANY = "any"

    LISTS = ("[int]", "[string]", "[pair]")


def elem_type(t: str) -> str:
    """The element type of a list type."""
    return {VType.LIST_INT: VType.INT, VType.LIST_STR: VType.STR,
            VType.LIST_PAIR: VType.PAIR}.get(t, VType.ANY)


def compatible(actual: str, expected: str) -> bool:
    """Whether `actual` may be used where `expected` is required."""
    if VType.ANY in (actual, expected):
        return True
    if expected == "list":
        return actual in VType.LISTS
    return actual == expected


def type_of(v: Any) -> str:
    if isinstance(v, bool) or isinstance(v, int):
        return VType.INT
    if isinstance(v, str):
        return VType.STR
    if isinstance(v, tuple):
        return VType.PAIR
    if isinstance(v, list):
        if not v:
            return VType.LIST_INT
        head = type_of(v[0])
        return {VType.INT: VType.LIST_INT, VType.STR: VType.LIST_STR,
                VType.PAIR: VType.LIST_PAIR}.get(head, VType.LIST_INT)
    return VType.ANY


# --- builtins -----------------------------------------------------------
# Each entry: (arg types, return type, implementation, documentation).
# The table is the single source of truth for the checker, the runtime and the
# editor's completions.
#
# Deliberately general. Nothing here knows about words, documents, urls or any
# other problem: `split` and `lower` are string operations, `sum` is arithmetic.
# A builtin that solved part of an exercise — countWords, extractLinks — would
# be handing over the work the exercise exists to make the student do, and would
# stop being useful the moment the job is not word count. Anything problem
# shaped is a function the student writes.

def _split(s, sep=None):
    return s.split() if sep is None else s.split(sep)


def _hash31(key) -> int:
    """The same 31-hash the Java and JS assignments use."""
    h = 0
    for ch in str(key):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h)


# name: (parameter types, return type, implementation, documentation)
BUILTINS: dict[str, tuple] = {
    "split":   ([VType.STR], VType.LIST_STR, _split,
                "split(text: string) -> [string]"),
    "lower":   ([VType.STR], VType.STR, lambda s: s.lower(),
                "lower(text: string) -> string"),
    "upper":   ([VType.STR], VType.STR, lambda s: s.upper(),
                "upper(text: string) -> string"),
    "strip":   ([VType.STR], VType.STR, lambda s: s.strip(),
                "strip(text: string) -> string"),
    "len":     ([VType.ANY], VType.INT, len,
                "len(x) -> int"),
    "sum":     ([VType.LIST_INT], VType.INT, sum,
                "sum(values: [int]) -> int"),
    "max":     ([VType.LIST_INT], VType.INT, lambda xs: max(xs) if xs else 0,
                "max(values: [int]) -> int"),
    "min":     ([VType.LIST_INT], VType.INT, lambda xs: min(xs) if xs else 0,
                "min(values: [int]) -> int"),
    "hash":    ([VType.ANY], VType.INT, _hash31,
                "hash(key) -> int, a stable 31-hash"),
    "abs":     ([VType.INT], VType.INT, abs, "abs(n: int) -> int"),
}




# --- parsing ------------------------------------------------------------

ENTRY_POINTS = ("map", "reduce", "partition", "combine")

# Names the runtime calls for you, per exercise.
#
# Only these are fixed, and only because something else invokes them: the
# framework calls `map` on every record, Spark calls the function you hand to
# a transformation, a service answers the method a client named. Everything
# else a student writes is an ordinary function with whatever name and
# signature they choose — the checker infers nothing, but it also imposes
# nothing beyond the entry point the exercise needs.
DIALECT_ENTRY_POINTS = {
    "mapreduce": ("map", "reduce", "partition", "combine"),
    "spark":     (),      # transformations take the functions you pass them
    "rpc":       (),      # a service names its own methods
    "clocks":    (),      # processes run the steps you write
}

FUNC_RE = re.compile(
    r"^(?:def\s+)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*->\s*(?P<ret>[\w\[\]]+))?\s*:\s*$")
RETURN_RE = re.compile(r"^return\s+(?P<value>.+)$")
PARAM_RE = re.compile(r"^(?P<name>\w+)\s*:\s*(?P<type>\[?\w+\]?)$")
FOR_RE = re.compile(
    r"^for\s+(?P<var>\w+)\s*(?::\s*(?P<vtype>\[?\w+\]?))?\s+in\s+"
    r"(?P<iter>.+?)\s*:\s*$")
LET_RE = re.compile(
    r"^(?P<var>\w+)\s*:\s*(?P<vtype>\[?\w+\]?)\s*=\s*(?P<value>.+)$")
IF_RE = re.compile(r"^if\s+(?P<cond>.+?)\s*:\s*$")
EMIT_RE = re.compile(r"^emit\s*\(\s*(?P<key>.+?)\s*,\s*(?P<value>.+?)\s*\)\s*$")

class TypeVar:
    """
    A type the program fixes, rather than one the language fixes.

    MapReduce is not word count. A crawler emits (url, source) and reduces a
    list of urls; an inverted index emits (term, docid). Only the *key* is
    forced to string — the partitioner hashes it — while the value type is
    whatever the job is about.

    This is a real variable, not a marker string: it cannot be mistaken for a
    concrete type, `unify` either binds it or reports a genuine conflict, and
    `[V]` is a list of whatever V turns out to be. The student's annotations
    are what bind it, which is what writing the types out is for.
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return self.name


V = TypeVar("V")                    # the value a job carries


class ListOf:
    """`[V]` — a list whose element type is still to be determined."""

    __slots__ = ("elem",)

    def __init__(self, elem):
        self.elem = elem

    def __repr__(self):
        return f"[{self.elem}]"


class FuncType:
    """
    The type of a function used as a value.

    Functions are ordinary values here: a name can be passed to another
    function, which is what `words.flatMap(clean)` means. Its type comes from
    the signature the student already wrote — `(string) -> [string]` — so
    handing `flatMap` something that takes two arguments, or returns an int
    where a list is required, is a type error at the call rather than a
    surprise at run time.
    """

    __slots__ = ("params", "ret")

    def __init__(self, params: list, ret):
        self.params = list(params)
        self.ret = ret

    def __repr__(self):
        return f"({', '.join(str(p) for p in self.params)}) -> {self.ret}"

    def __eq__(self, other):
        return (isinstance(other, FuncType)
                and self.params == other.params and self.ret == other.ret)

    def arity(self) -> int:
        return len(self.params)


def func_type(fn) -> FuncType:
    """A declared function's type, read off its signature."""
    return FuncType(list(fn.types) or [VType.ANY] * len(fn.params), fn.ret)


def unify(pattern, actual: str, bound: dict) -> str | None:
    """
    Match a declared type against a signature, binding type variables.

    Returns None on success, or a human-readable conflict. The first
    annotation to mention V decides it; every later one is held to it, so
    `map` emitting a string and `reduce` taking `[int]` is caught as the
    disagreement it is rather than passing silently.
    """
    if isinstance(pattern, TypeVar):
        seen = bound.get(pattern.name)
        if seen is None:
            bound[pattern.name] = actual
            return None
        return None if seen == actual else f"{actual}, but this job's values are {seen}"

    if isinstance(pattern, ListOf):
        if actual not in VType.LISTS:
            return f"{actual}, but a list is required here"
        return unify(pattern.elem, elem_type(actual), bound)

    if actual == VType.ANY or pattern == actual or compatible(actual, pattern):
        return None
    return f"{actual}, but must be {pattern}"


def show(pattern, bound: dict) -> str:
    """A signature as the student should read it, with V resolved if known."""
    if isinstance(pattern, TypeVar):
        return bound.get(pattern.name, pattern.name)
    if isinstance(pattern, ListOf):
        return f"[{show(pattern.elem, bound)}]"
    return str(pattern)


# How many parameters each function takes, and what a student may emit.
SIGNATURES = {
    # name: (param names, param types, return type, what it does)
    #
    # V is bound by whichever annotation mentions it first. Writing
    #     def reduce(key: string, values: [string]) -> string
    # says this job carries strings, and emit and combine are then held to it.
    "map":       (["key", "value"], [VType.STR, VType.STR], VType.VOID,
                  "emits pairs"),
    "reduce":    (["key", "values"], [VType.STR, ListOf(V)], V,
                  "returns one value"),
    "partition": (["key", "n"], [VType.STR, VType.INT], VType.INT,
                  "returns an int"),
    "combine":   (["key", "values"], [VType.STR, ListOf(V)], V,
                  "returns one value"),
}


@dataclass
class Func:
    name: str
    params: list[str]
    body: list[tuple]        # (indent, text, line)
    line: int
    types: list = field(default_factory=list)   # declared parameter types
    ret: str = VType.ANY                        # declared return type
    # What this job's values are, resolved from the program's own annotations.
    # A word count binds it to int; a crawler binds it to string.
    value_type: str = VType.ANY


def parse_functions(source: str) -> tuple[dict[str, Func], list[Diagnostic]]:
    """Pull `map:`/`reduce:` blocks out of a program, by indentation."""
    funcs: dict[str, Func] = {}
    diags: list[Diagnostic] = []
    current: Func | None = None
    # What V turned out to be, shared across every function in the program.
    bound: dict[str, str] = {}

    for i, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()

        m = FUNC_RE.match(text)
        if m and indent == 0:
            name = m.group("name")
            raw = [p.strip() for p in m.group("params").split(",") if p.strip()]

            if name not in SIGNATURES:
                # A helper the student defined. Its signature is whatever they
                # wrote, so long as every part of it is written down.
                names, types = [], []
                for p in raw:
                    pm = PARAM_RE.match(p)
                    if not pm:
                        diags.append(Diagnostic(
                            i, 1, "error",
                            f"parameter {p!r} needs a type",
                            hint=f"write 'def {name}({p}: string) -> int:'"))
                        names.append(p.split(":")[0].strip())
                        types.append(VType.ANY)
                    else:
                        names.append(pm.group("name"))
                        types.append(pm.group("type"))
                ret = m.group("ret")
                if not ret:
                    diags.append(Diagnostic(
                        i, 1, "error", f"{name} needs a return type",
                        hint=f"write 'def {name}(...) -> int:'"))
                if name in funcs:
                    diags.append(Diagnostic(
                        i, 1, "error",
                        f"{name} is already defined on line {funcs[name].line}"))
                current = Func(name, names, [], i, types=types,
                               ret=ret or VType.ANY)
                funcs[name] = current
                continue

            want, want_types, want_ret, _ = SIGNATURES[name]
            expected_sig = (f"{name}(" +
                            ", ".join(f"{p}: {show(t, bound)}"
                                      for p, t in zip(want, want_types)) +
                            f") -> {show(want_ret, bound)}")

            names, types = [], []
            for p in raw:
                pm = PARAM_RE.match(p)
                if not pm:
                    diags.append(Diagnostic(
                        i, 1, "error", f"parameter {p!r} needs a type",
                        hint=f"write {expected_sig}"))
                    names.append(p.split(":")[0].strip())
                    types.append(VType.ANY)
                else:
                    names.append(pm.group("name"))
                    types.append(pm.group("type"))

            if len(names) != len(want):
                diags.append(Diagnostic(
                    i, 1, "error",
                    f"{name} takes {len(want)} parameters, got {len(names)}",
                    hint=f"expected {expected_sig}"))
            else:
                for got, exp, pname in zip(types, want_types, names):
                    if got == VType.ANY:
                        continue
                    clash = unify(exp, got, bound)
                    if clash:
                        diags.append(Diagnostic(
                            i, 1, "error",
                            f"{name}: parameter {pname!r} is declared {clash}",
                            hint=f"expected {expected_sig}"))

            declared_ret = m.group("ret")
            clash = unify(want_ret, declared_ret, bound) if declared_ret else None
            if clash:
                diags.append(Diagnostic(
                    i, 1, "error",
                    f"{name} returns {clash}",
                    hint=f"expected {expected_sig}"))
            elif not declared_ret:
                diags.append(Diagnostic(
                    i, 1, "warning", f"{name} has no return type",
                    hint=f"write {expected_sig}"))

            if name in funcs:
                diags.append(Diagnostic(
                    i, 1, "error", f"{name} is already defined on line {funcs[name].line}"))
            current = Func(name, names or list(want), [], i,
                           types=types or list(want_types),
                           ret=declared_ret or want_ret)
            funcs[name] = current
            continue

        if indent == 0:
            current = None          # back to top level: not our business
            continue
        if current is not None:
            current.body.append((indent, text, i))

    # What V resolved to, so the emit check and the runtime agree with the
    # signatures rather than assuming a count.
    for f in funcs.values():
        f.value_type = bound.get("V", VType.ANY)
    return funcs, diags




NAME_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(\()?")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
KNOWN_WORDS = {"mod", "and", "or", "not", "in", "if", "else", "true", "false"}


def infer(expr: str, scope: dict, line: int, diags: list) -> str:
    """
    Infer the type of an expression, recording any type errors.

    `scope` maps names to types. Returns VType.ANY when the type cannot be
    determined, so one unknown does not cascade into a wall of errors.
    """
    e = expr.strip()
    if not e:
        return VType.ANY

    # literals
    if re.fullmatch(r"-?\d+", e):
        return VType.INT
    if re.fullmatch(r'"[^"]*"', e):
        return VType.STR

    # a bare name
    if re.fullmatch(r"[A-Za-z_]\w*", e):
        if e in KNOWN_WORDS:
            return VType.INT
        if e in scope:
            return scope[e]
        # A function named without being called is the function itself —
        # `flatMap(clean)` passes `clean` rather than calling it. Its type is
        # the signature the student wrote.
        if e in USER_FUNCS:
            return func_type(USER_FUNCS[e])
        if e in BUILTINS:
            params, ret, _, _ = BUILTINS[e]
            return FuncType(params, ret)
        diags.append(Diagnostic(
            line, 1, "error", f"unknown name {e!r}",
            hint=f"in scope here: {', '.join(sorted(scope)) or 'nothing'}"))
        return VType.ANY

    # a function call — check the argument types against the signature
    m = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)", e, re.S)
    if m:
        fname, argstr = m.group(1), m.group(2)
        if fname in USER_FUNCS:
            fn = USER_FUNCS[fname]
            args = _split_args(argstr)
            if len(args) != len(fn.params):
                diags.append(Diagnostic(
                    line, 1, "error",
                    f"{fname} takes {len(fn.params)} argument(s), got {len(args)}",
                    hint=f"def {fname}({', '.join(f'{p}: {t}' for p, t in zip(fn.params, fn.types))})"))
                return fn.ret
            for arg, want, pname in zip(args, fn.types, fn.params):
                got = infer(arg, scope, line, diags)
                if not compatible(got, want):
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"{fname}: {pname} expects {want}, got {got}",
                        hint=f"you defined {fname} on line {fn.line}"))
            return fn.ret

        if fname not in BUILTINS:
            known = sorted(set(BUILTINS) | set(USER_FUNCS))
            diags.append(Diagnostic(
                line, 1, "error", f"unknown function {fname!r}",
                hint=f"available: {', '.join(known)}"))
            return VType.ANY
        params, ret, _, doc = BUILTINS[fname]
        args = _split_args(argstr)
        if len(args) != len(params):
            diags.append(Diagnostic(
                line, 1, "error",
                f"{fname} takes {len(params)} argument(s), got {len(args)}",
                hint=doc))
            return ret
        for arg, want in zip(args, params):
            got = infer(arg, scope, line, diags)
            if not compatible(got, want):
                diags.append(Diagnostic(
                    line, 1, "error",
                    f"{fname}: expected {want}, got {got}, in {arg.strip()!r}",
                    hint=doc))
        # `first` returns whatever the list holds.
        if fname == "first" and args:
            return elem_type(infer(args[0], scope, line, diags))
        return ret

    # comparisons produce an int (used as a truth value)
    if re.search(r"(==|!=|<=|>=|<|>)", e):
        for side in re.split(r"==|!=|<=|>=|<|>", e, maxsplit=1):
            infer(side, scope, line, diags)
        return VType.INT

    # arithmetic: every operand must be an int
    if re.search(r"[+\-*/]|\bmod\b", e):
        parts = [p for p in re.split(r"[+\-*/]|\bmod\b", e) if p.strip()]
        types = [infer(p, scope, line, diags) for p in parts]
        # `+` on strings is a common slip and worth naming precisely.
        for t, p in zip(types, parts):
            if not compatible(t, VType.INT):
                diags.append(Diagnostic(
                    line, 1, "error",
                    f"arithmetic needs int, got {t}, in {p.strip()!r}",
                    hint="only numbers can be added, subtracted or divided"))
        return VType.INT

    if e.startswith("(") and e.endswith(")"):
        return infer(e[1:-1], scope, line, diags)

    return VType.ANY


def _split_args(s: str) -> list:
    """Split on top-level commas only, so nested calls stay intact."""
    args, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            args.append(cur)
            cur = ""
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        cur += ch
    if cur.strip():
        args.append(cur)
    return args


# Helper functions in scope while checking. Set by `check_functions` so that
# `infer` can resolve calls to them.
USER_FUNCS: dict = {}


def check_functions(funcs: dict, mapper: str | None = None) -> list[Diagnostic]:
    """
    Type-check every student function.

    Each statement is checked in a scope mapping names to types, so a mistake
    such as `lower(values)` — a string function applied to a list — is a type
    error at the right line, not a confusing result later.
    """
    diags: list[Diagnostic] = []
    USER_FUNCS.clear()
    USER_FUNCS.update({n: f for n, f in funcs.items() if n not in ENTRY_POINTS})

    for fn in funcs.values():
        scope = dict(zip(fn.params, fn.types or [VType.ANY] * len(fn.params)))
        emits = 0
        last_type = VType.VOID

        for indent, text, line in fn.body:
            m = LET_RE.match(text)
            if m:
                declared = m.group("vtype")
                actual = infer(m.group("value"), scope, line, diags)
                if not compatible(actual, declared):
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"{m.group('var')!r} is declared {declared}, "
                        f"but the value is {actual}",
                        hint="the written type and the value must agree"))
                scope[m.group("var")] = declared
                last_type = VType.VOID
                continue

            m = FOR_RE.match(text)
            if m:
                seq_t = infer(m.group("iter"), scope, line, diags)
                if seq_t not in VType.LISTS and seq_t != VType.ANY:
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"cannot loop over {seq_t}: for needs a list",
                        # A hint is documentation too: it must not be the
                        # first line of a function a task asks for.
                        hint="e.g. 'for reading: int in readings:'"))
                item_t = elem_type(seq_t)
                declared = m.group("vtype")
                if not declared:
                    # Types are written, never inferred: the student must say
                    # what the loop variable is.
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"loop variable {m.group('var')!r} needs a type",
                        hint=f"write 'for {m.group('var')}: {item_t} in "
                             f"{m.group('iter').strip()}:'"))
                elif item_t != VType.ANY and not compatible(item_t, declared):
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"{m.group('var')!r} is declared {declared}, but "
                        f"{m.group('iter').strip()!r} holds {item_t}",
                        hint="the written type must match what the list holds"))
                scope[m.group("var")] = declared or item_t
                continue

            m = IF_RE.match(text)
            if m:
                infer(m.group("cond"), scope, line, diags)
                continue

            m = RETURN_RE.match(text)
            if m:
                got = infer(m.group("value"), scope, line, diags)
                if fn.ret and not compatible(got, fn.ret):
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"{fn.name} returns {fn.ret}, but this value is {got}",
                        hint="the returned value must match the declared type"))
                last_type = got
                continue

            m = EMIT_RE.match(text)
            if m:
                emits += 1
                # Emitting belongs to whichever function was passed as the
                # mapper — not to one that happens to be spelled `map`. When no
                # job has been wired up yet there is nothing to check against,
                # so this stays quiet rather than guessing.
                if mapper is not None and fn.name != mapper:
                    described = (SIGNATURES[fn.name][3]
                                 if fn.name in SIGNATURES
                                 else "is not this job's mapper")
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"only the mapper may emit, and this job's mapper is "
                        f"{mapper}, not {fn.name}",
                        hint=f"{fn.name} {described}"))
                infer(m.group("key"), scope, line, diags)
                vt = infer(m.group("value"), scope, line, diags)
                want = getattr(fn, "value_type", VType.ANY)
                if not compatible(vt, want):
                    diags.append(Diagnostic(
                        line, 1, "error",
                        f"emit expects a {want} value, got {vt}",
                        hint=f"this job's reduce takes [{want}], so map must "
                             f"emit {want}"))
                continue

            last_type = infer(text, scope, line, diags)

        # The final expression must match the declared return type.
        want_ret = SIGNATURES[fn.name][2] if fn.name in SIGNATURES else fn.ret
        # Compare against what V resolved to, not the variable itself.
        if isinstance(want_ret, TypeVar):
            want_ret = getattr(fn, "value_type", VType.ANY)
        if fn.name != "map" and want_ret != VType.VOID \
                and last_type != VType.ANY \
                and not compatible(last_type, want_ret):
            diags.append(Diagnostic(
                fn.line, 1, "error",
                f"{fn.name} must return {want_ret}, but its last expression "
                f"is {last_type}",
                hint=f"the last line of {fn.name} is its result")) 

        if fn.name == "map" and emits == 0:
            diags.append(Diagnostic(
                fn.line, 1, "warning", "map never emits anything",
                hint="use emit(key, value); a map that emits nothing "
                     "produces an empty result"))
    return diags


# --- evaluation ---------------------------------------------------------

class Budget:
    """Caps evaluation so a bad submission cannot hang the page."""

    def __init__(self, steps: int = 200_000):
        self.left = steps

    def spend(self, n: int = 1):
        self.left -= n
        if self.left <= 0:
            raise NotationError([Diagnostic(
                1, 1, "error", "expression ran too long",
                hint="the sub-language has no recursion; check for a very "
                     "large input or a deeply nested loop")])


# Helpers available at run time, set by `run_function`.
RUNTIME_FUNCS: dict = {}


# Operators the sub-language permits, mapped to their implementations. `mod`
# is spelled out as `%` before parsing; everything here is ordinary arithmetic,
# comparison and boolean logic over int/string/list/pair values.
_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_COMPARES = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


def _eval_expr(expr: str, env: dict, line: int, budget: Budget):
    """Evaluate one expression over a whitelisted environment.

    Parses to an AST and walks a fixed set of node types rather than calling
    `eval`. This is a security boundary, not a convenience: `eval` with an empty
    `__builtins__` still lets `(1).__class__.__base__.__subclasses__()` reach
    arbitrary objects through attribute access. The walker below has no Attribute,
    Subscript, comprehension or lambda node, so there is no path from a student
    expression to anything but the values and functions it is handed. It must
    stay that way — do not add nodes that expose attribute or item access.
    """
    budget.spend()
    py = re.sub(r"\bmod\b", "%", expr)
    names = {name: impl for name, (_, _, impl, _) in BUILTINS.items()}
    # A student's own function is callable from anywhere, like Python.
    for name, fn in RUNTIME_FUNCS.items():
        names[name] = _make_callable(fn, budget)
    names.update(env)

    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as e:
        raise NotationError([Diagnostic(
            line, 1, "error", f"cannot parse {expr!r}: {e.msg}",
            hint="check the names and brackets in this expression")])

    try:
        return _eval_node(tree.body, names, line, budget)
    except NotationError:
        raise
    except Exception as e:
        raise NotationError([Diagnostic(
            line, 1, "error", f"cannot evaluate {expr!r}: {e}",
            hint="check the names and brackets in this expression")])


def _eval_node(node: ast.AST, names: dict, line: int, budget: Budget):
    """Evaluate one AST node against the whitelisted `names` environment."""
    budget.spend()

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return node.value
        raise _reject(node, line)

    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise NotationError([Diagnostic(
            line, 1, "error", f"unknown name {node.id!r}",
            hint="only your parameters, your functions and the builtins are "
                 "available here")])

    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](
            _eval_node(node.left, names, line, budget),
            _eval_node(node.right, names, line, budget))

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval_node(node.operand, names, line, budget))

    if isinstance(node, ast.BoolOp):
        vals = [lambda n=n: _eval_node(n, names, line, budget) for n in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in vals:
                result = v()
                if not result:
                    return result
            return result
        result = False
        for v in vals:
            result = v()
            if result:
                return result
        return result

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, names, line, budget)
        for op, comparator in zip(node.ops, node.comparators):
            if type(op) not in _COMPARES:
                raise _reject(node, line)
            right = _eval_node(comparator, names, line, budget)
            if not _COMPARES[type(op)](left, right):
                return False
            left = right
        return True

    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_eval_node(e, names, line, budget) for e in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)

    if isinstance(node, ast.Call):
        # Only bare-name calls: `split(value)`, never `x.method()` — the callee
        # must be a Name so there is no attribute-access node to exploit.
        if not isinstance(node.func, ast.Name):
            raise _reject(node.func, line)
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise _reject(node, line)
        fn = _eval_node(node.func, names, line, budget)
        if not callable(fn):
            raise NotationError([Diagnostic(
                line, 1, "error", f"{node.func.id!r} is not a function")])
        args = [_eval_node(a, names, line, budget) for a in node.args]
        return fn(*args)

    raise _reject(node, line)


def _reject(node: ast.AST, line: int) -> NotationError:
    """A uniform diagnostic for any construct the sub-language does not allow."""
    return NotationError([Diagnostic(
        line, 1, "error",
        f"this expression uses {type(node).__name__}, which is not allowed here",
        hint="the sub-language is arithmetic, comparisons and function calls, "
             "no attribute access, indexing or comprehensions")])


def _make_callable(fn: "Func", budget: Budget):
    """Wrap a student helper so it can be called from an expression."""
    def call(*args):
        return run_function(fn, dict(zip(fn.params, args)), budget)
    return call


def bind_helpers(funcs: dict) -> None:
    """Make the student's helpers callable at run time."""
    RUNTIME_FUNCS.clear()
    RUNTIME_FUNCS.update({n: f for n, f in funcs.items()
                          if n not in ENTRY_POINTS})


def run_function(fn: Func, args: dict, budget: Budget,
                 *, collect_emits: bool = False) -> Any:
    """
    Execute one student function.

    Returns the pairs it emitted when `collect_emits` is asked for, otherwise
    the value of its final expression. The caller says which it wants, because
    only the caller knows what position the function was passed in — this used
    to be decided by testing whether the function was *named* `map`, which
    stopped working the moment students chose their own names.
    """
    emitted: list[tuple] = []
    result = None
    env = dict(args)

    def execute(body: list[tuple], env: dict):
        nonlocal result
        i = 0
        while i < len(body):
            indent, text, line = body[i]
            budget.spend()

            m = LET_RE.match(text)
            if m:
                env[m.group("var")] = _eval_expr(
                    m.group("value"), env, line, budget)
                i += 1
                continue

            m = FOR_RE.match(text)
            if m:
                block, i = _block(body, i, indent)
                seq = _eval_expr(m.group("iter"), env, line, budget)
                for item in seq:
                    execute(block, {**env, m.group("var"): item})
                continue

            m = IF_RE.match(text)
            if m:
                block, i = _block(body, i, indent)
                if _eval_expr(m.group("cond"), env, line, budget):
                    execute(block, env)
                continue

            m = RETURN_RE.match(text)
            if m:
                result = _eval_expr(m.group("value"), env, line, budget)
                return

            m = EMIT_RE.match(text)
            if m:
                emitted.append((_eval_expr(m.group("key"), env, line, budget),
                                _eval_expr(m.group("value"), env, line, budget)))
                i += 1
                continue

            result = _eval_expr(text, env, line, budget)
            i += 1

    execute(fn.body, env)
    return emitted if collect_emits else result


def _block(body, i, indent):
    """The indented lines following a `for`/`if` header at `indent`."""
    block, j = [], i + 1
    while j < len(body) and body[j][0] > indent:
        block.append(body[j])
        j += 1
    return block, j
