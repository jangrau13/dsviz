"""
The shared syntax: decorators, classes, typed functions.

One language across the whole course. A machine is a decorated class whatever
the exercise calls it, a method is a typed function, and a function is a value
that can be passed to another. What differs between exercises is which
decorators exist and what the runtime does with them — not how anything is
written.

    @machine(speed=0.5)
    class MapServer:
        @duration(0.4)
        def handle(chunk: string) -> int:
            return len(split(chunk))

    @machine
    class App:
        def main() -> void:
            n: int = MapServer.handle("a b c", deadline=0.5, retries=2)

Nothing here is invented notation. Decorators configure, keyword arguments pass
options, `def` declares behaviour — Python already means all of that, so a
student reading this reads Python.

This module only produces the shape: which classes exist, what decorates them,
and which functions they hold. What a `@machine` *means* belongs to the
runtime that interprets it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lark import Token

from . import pyspark
from .notation import Diagnostic

# --- grammar -------------------------------------------------------------
# Deliberately anchored to line starts and indentation. The language is
# statement-per-line with Python's block rule, so a line-oriented reader is
# honest about what it accepts rather than pretending to parse expressions it
# then re-parses elsewhere.

DECORATOR_RE = re.compile(
    r"^@(?P<name>\w+)(?:\s*\((?P<args>.*)\))?\s*$")
CLASS_RE = re.compile(
    r"^class\s+(?P<name>\w+)\s*(?:\((?P<bases>[^)]*)\))?\s*:\s*$")
DEF_RE = re.compile(
    r"^def\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*->\s*(?P<ret>[\w\[\]]+))?\s*:\s*$")

# `n: int = Counter.handle("x", deadline=0.5)` — a typed binding whose value is
# a call. The type is required, as everywhere else in this language.
BIND_RE = re.compile(
    r"^(?P<var>\w+)\s*:\s*(?P<type>\[?\w+\]?)\s*=\s*(?P<value>.+)$")

# Keyword arguments, which is how every option is passed.
KWARG_RE = re.compile(r"(?P<name>\w+)\s*=\s*(?P<value>[^,]+)")


def parse_kwargs(text: str) -> dict:
    """`speed=0.5, retries=2` as a dict, with numbers left as numbers."""
    out = {}
    for m in KWARG_RE.finditer(text or ""):
        raw = m.group("value").strip()
        if re.fullmatch(r"-?\d+", raw):
            out[m.group("name")] = int(raw)
        elif re.fullmatch(r"-?\d*\.\d+", raw):
            out[m.group("name")] = float(raw)
        else:
            out[m.group("name")] = raw.strip('"')
    return out


@dataclass
class Decorator:
    name: str
    args: dict = field(default_factory=dict)
    line: int = 0


@dataclass
class RemoteCall:
    """`MapServer.handle("chunk001.txt", deadline=0.5)` — one call, as data."""
    target: str
    method: str
    args: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    line: int = 0
    # The name this call's answer is bound to, from `chf: int = bank.balance(…)`.
    # Threading it through is what lets the next call be given the value rather
    # than the word.
    bind: str = ""
    # The `with parallel():` block this call is inside, identified by that
    # block's line, or 0 for a call written on its own. Calls sharing a group
    # leave together; everything else is one after the other.
    group: int = 0
    # The arguments that were written as bare names rather than literals.
    # `args` cannot say which: it holds the value, and by then `chf` and
    # `"chf"` are the same string. Knowing which is which is what lets the
    # checker tell a call that depends on another's answer from one that
    # merely mentions a word.
    uses: list = field(default_factory=list)


@dataclass
class WithBlock:
    """A `with …():` block inside a function, as written."""
    name: str
    line: int
    nested: bool = False      # does it contain another such block
    calls: int = 0            # how many calls out are inside it


@dataclass
class Method:
    """A function inside a class, with whatever decorates it."""
    name: str
    params: list
    types: list
    ret: str
    body: list                       # (indent, text, line)
    line: int
    decorators: list = field(default_factory=list)
    # Calls this method makes on other machines, read off the parse tree so the
    # runtime never has to re-read the source to find them.
    calls: list = field(default_factory=list)
    # The `with …():` blocks it contains. Kept even when the name is not one
    # the language has, so the checker can say so on the right line.
    blocks: list = field(default_factory=list)

    def decorator(self, name: str) -> Decorator | None:
        return next((d for d in self.decorators if d.name == name), None)


@dataclass
class StateField:
    """
    Something a machine remembers between calls.

        @machine
        class Ledger:
            balance: int = 100

    A machine that only answers is a function with a network in front of it:
    ask it twice and it says the same thing twice, and nothing it did is
    visible afterwards. State is what makes it a machine — the second call
    can see what the first one did — and it is also what a crash destroys,
    which is the reason failure matters at all in this course.

    Written exactly as a local binding is, because it is the same statement
    said about a longer-lived name: a written type, and the value it starts
    at.
    """
    name: str
    type: str
    value: object
    line: int


@dataclass
class ClassDecl:
    """A decorated class: a machine, a process, whatever the exercise means."""
    name: str
    decorators: list
    methods: dict
    line: int
    body: list = field(default_factory=list)   # statements outside any method
    # What machines of this kind remember, by field name. Declared on the
    # class because it is what every machine of this kind holds; the value
    # each one starts at may still differ per instance.
    state: dict = field(default_factory=dict)

    def decorator(self, name: str) -> Decorator | None:
        return next((d for d in self.decorators if d.name == name), None)

    @property
    def kind(self) -> str:
        """The first decorator's name — `machine`, `mapper`, `reducer`, `process`."""
        return self.decorators[0].name if self.decorators else ""


@dataclass
class Instance:
    """
    A machine that actually exists.

        @mapper
        class Worker:
            pass

        fast = Worker(speed=1.0)
        slow = Worker(speed=0.3)

    The class says what kind of machine this is and what it can do; the
    instance is the one that runs, carrying its own speed and failure
    behaviour. Two mappers of the same kind are two instances rather than two
    near-identical class declarations, and the name on the timeline is the name
    of the instance — `slow`, not `Worker`.
    """
    var: str                                  # the name bound to the machine
    cls: str                                  # the class it is an instance of
    settings: dict = field(default_factory=dict)
    line: int = 0


@dataclass
class Rdd:
    """
    One step of a pipeline, and what it was made from.

    `words = lines.flatMap(...)` says words came from lines. Collected in
    order, these assignments *are* the lineage graph — which is the whole
    point of Spark: a lost partition is not reloaded, it is rebuilt from the
    steps that produced it, and those steps are written on the page.
    """
    var: str
    op: str
    parents: list = field(default_factory=list)
    line: int = 0
    # Every call in the chain, as (operation, [argument source]). The argument
    # is kept as the text the student wrote, because what it means is Python's
    # to say: a lambda is handed to Python's own parser rather than being
    # re-derived from a tree that only approximates one. Keeping only the last
    # operation's name — which is all this held before — meant a pipeline could
    # not be run at all, so nothing checked whether it computed anything.
    steps: list = field(default_factory=list)


@dataclass
class World:
    """
    The system a job runs in.

        world = World(machines=[fast, slow], capacity=8)

    Everything about the *setting* lives here — which machines exist, how fast
    they are, how they fail — and nothing about the computation. That split is
    what makes the same job runnable in a fast world and a broken one, which is
    the comparison most of the course is about. It is also why any distributed
    system can be described: a world is just a set of machines and the
    conditions they run under.
    """
    var: str
    settings: dict = field(default_factory=dict)
    line: int = 0

    @property
    def machines(self) -> list:
        names = self.settings.get("machines", [])
        return [str(n) for n in names] if isinstance(names, list) else []


@dataclass
class Run:
    """
    `world.run(job)` — a job performed in a world.

        world.run(job)                       # every machine in the world
        world.run(job, on=[fast, slow, r1])  # only part of it
        world.run(job, times=3)              # repeated

    A job does not have to occupy the whole world: `on` names the subsystem it
    is given, so one world can host a job on three machines and then the same
    job on ten, which is the comparison that makes scaling visible.
    """
    world: str
    job: str
    options: dict = field(default_factory=dict)
    line: int = 0

    @property
    def times(self) -> int:
        return int(self.options.get("times", 1))

    @property
    def on(self) -> list:
        """The subsystem this job was given, or [] meaning the whole world."""
        chosen = self.options.get("on", [])
        return [str(n) for n in chosen] if isinstance(chosen, list) else []


@dataclass
class Job:
    """
    A job built from the student's own functions.

        job = MapReduce(map=tokenize, reduce=total, partition=byKey)

    Nothing is a mapper because it is spelled `map`. `tokenize` is the mapper
    because it was passed as one, and it is only *allowed* to be passed as one
    if its signature fits that position — which is checkable precisely because
    the student wrote the types out. This is also where the wiring becomes
    visible: the functions are not collected by a hidden whitelist, they are
    handed to the job on a line the student writes.
    """
    var: str                                  # the name bound to the job
    kind: str                                 # MapReduce, Spark, …
    roles: dict = field(default_factory=dict)  # role -> function name
    # How the system is set up to run: mappers=3, reducers=2, capacity=4,
    # combiner=on. These belong on the same line as the functions because they
    # are the same decision — what to run, and what to run it on.
    settings: dict = field(default_factory=dict)
    line: int = 0
    # A PySpark job is handed its pipeline as a block of real PySpark rather
    # than as a set of roles. The text is kept verbatim, with the line it
    # starts on, so a diagnostic inside the block lands on the line the
    # student is actually looking at.
    source: str = ""
    source_line: int = 0


@dataclass
class Module:
    """Everything a source file declares."""
    classes: dict = field(default_factory=dict)
    functions: dict = field(default_factory=dict)   # top-level defs
    statements: list = field(default_factory=list)  # top-level, in order
    jobs: list = field(default_factory=list)        # job = MapReduce(...)
    rdds: list = field(default_factory=list)        # counts = pairs.reduceByKey()
    instances: dict = field(default_factory=dict)   # fast = Worker(speed=1.0)
    worlds: dict = field(default_factory=dict)      # world = World(machines=[…])
    runs: list = field(default_factory=list)        # world.run(job)
    inputs: dict = field(default_factory=dict)      # input rows: "…" | "…"
    # Top-level `a.b(...)` lines nothing claimed. Kept rather than discarded:
    # a line the parser cannot place is a line the student wrote and meant.
    dropped: list = field(default_factory=list)     # (receiver, method, line)

    def machines(self) -> dict:
        """Instances whose class is one of `kinds`, by the name they were given."""
        return dict(self.instances)

    def of_kind(self, kind: str) -> list:
        return [c for c in self.classes.values() if c.kind == kind]


def _params(text: str, line: int, diags: list) -> tuple[list, list]:
    """Split a parameter list, insisting every parameter carries a type."""
    names, types = [], []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        if ":" not in part:
            diags.append(Diagnostic(
                line, 1, "error", f"parameter {part!r} needs a type",
                hint="every parameter is written with its type, "
                     "as in def handle(request: string) -> int"))
            names.append(part)
            types.append("any")
            continue
        name, _, t = part.partition(":")
        names.append(name.strip())
        types.append(t.strip())
    return names, types


LOCAL_ASSIGN_RE = re.compile(r"^(\s+)(?P<var>\w+)\s*=\s*\S")


def _untyped_local(lines: list[str], line: int) -> tuple[int, str] | None:
    """
    The line before a syntax error, when it is a local without a type.

    Inside a function every name carries a written type, and `chf = bank.
    balance("alice")` does not. What the grammar says about it is "syntax
    error here, expected 'if'": the line is right, the advice is about a
    keyword nobody was reaching for, and the missing thing — a type — is not
    mentioned. Saying so costs one regex.

    The search runs backwards from the reported line, because a statement
    that cannot be ended is sometimes only noticed on the next one.

    Only indented lines qualify: at the top level `bank = Bank()` is how a
    machine is made, and has no type on purpose.
    """
    for at in range(min(line, len(lines)), 0, -1):
        text = lines[at - 1]
        if not text.strip() or text.strip().startswith("#"):
            continue
        m = LOCAL_ASSIGN_RE.match(text)
        return (at, m.group("var")) if m and ":" not in text.split("=")[0] \
            else None
    return None


def parse(source: str) -> tuple[Module, list[Diagnostic]]:
    """
    Read a program into classes, functions and top-level statements.

    Indentation defines blocks, as in Python. Decorators accumulate until the
    thing they decorate appears; anything else at column zero is a top-level
    statement, which is what a Spark pipeline is made of.
    """
    mod = Module()
    diags: list[Diagnostic] = []
    pending: list[Decorator] = []      # decorators awaiting their target
    cls: ClassDecl | None = None
    fn: Method | None = None

    for i, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()

        m = DECORATOR_RE.match(text)
        if m:
            pending.append(Decorator(m.group("name"),
                                     parse_kwargs(m.group("args")), i))
            continue

        m = CLASS_RE.match(text)
        if m and indent == 0:
            cls = ClassDecl(m.group("name"), pending, {}, i)
            mod.classes[cls.name] = cls
            pending, fn = [], None
            continue

        m = DEF_RE.match(text)
        if m:
            names, types = _params(m.group("params"), i, diags)
            fn = Method(m.group("name"), names, types,
                        m.group("ret") or "any", [], i, pending)
            pending = []
            # A def indented inside a class belongs to it; one at column zero
            # is an ordinary function, and can be passed as a value.
            if cls is not None and indent > 0:
                cls.methods[fn.name] = fn
            else:
                cls = None
                mod.functions[fn.name] = fn
            continue

        if pending:
            diags.append(Diagnostic(
                pending[-1].line, 1, "error",
                f"@{pending[-1].name} decorates nothing",
                hint="a decorator goes immediately above a class or a def"))
            pending = []

        # Body of the innermost open block.
        if fn is not None and indent > (0 if cls is None else 1):
            fn.body.append((indent, text, i))
        elif cls is not None and indent > 0:
            cls.body.append((indent, text, i))
        else:
            cls, fn = None, None
            mod.statements.append((text, i))

    if pending:
        diags.append(Diagnostic(
            pending[-1].line, 1, "error",
            f"@{pending[-1].name} decorates nothing",
            hint="a decorator goes immediately above a class or a def"))

    return mod, diags


def _tok(node) -> str:
    """A token's text, whatever Lark wrapped it in."""
    return str(node)


def _dec_args(node) -> tuple[list, dict]:
    """A decorator's `(0.4)` and `(speed=0.5)` as positional and named args."""
    args, kwargs = [], {}
    if node is None:
        return args, kwargs
    for child in node.children:
        if getattr(child, "data", None) == "kwarg":
            name, value = child.children
            kwargs[_tok(name)] = _literal(value)
        else:
            args.append(_literal(child))
    return args, kwargs


def _literal(node):
    """A number or string literal as a Python value; anything else as text."""
    kind = getattr(node, "data", None)
    if kind == "number":
        raw = _tok(node.children[0])
        return float(raw) if "." in raw else int(raw)
    if kind == "string":
        return _tok(node.children[0]).strip('"')
    if kind == "var":
        return _tok(node.children[0])
    if kind == "list_lit":
        return [_literal(c) for c in node.children]
    return _tok(node)


def from_tree(source: str) -> tuple[Module, list[Diagnostic]]:
    """
    Read a program into the same `Module` the regex parser produced — but from
    the grammar's parse tree.

    This is the seam that ends the split brain. `parse()` below re-read the
    source with its own regexes, so the grammar could accept a program the
    runtime then rejected, and vice versa. Everything structural now comes from
    one parse: which classes exist, what decorates them, which functions they
    hold, and the type of every parameter.

    Statement bodies are still handed on as source lines, because that is what
    `expr.run_function` executes. Moving those onto the tree as well is the
    remaining half of the collapse.
    """
    from .grammar import parser, position

    from .pyspark import mask_arguments

    mod = Module()
    diags: list[Diagnostic] = []
    lines = source.splitlines()
    # The grammar reads a copy with every PySpark argument blanked out, so it
    # never has to know what a lambda is. The blanks are the same length as
    # what they replaced, so every position it reports still points into the
    # real source — which is what `lines` and `arg_sources` go on using.
    masked, hidden = mask_arguments(source)

    def body_of(node) -> list:
        """
        The statement lines inside a def, as (indent, text, line).

        Bounded by indentation rather than by the last token's position: a
        statement's range runs to the newline that ends it, which is the *next*
        line, so trusting it swallowed whatever followed the block.
        """
        start = position(node)[0]
        header = lines[start - 1] if start <= len(lines) else ""
        outer = len(header) - len(header.lstrip())
        out = []
        for n in range(start + 1, len(lines) + 1):
            text = lines[n - 1].split("#", 1)[0].rstrip()
            if not text.strip():
                continue                       # blank lines do not close a block
            indent = len(text) - len(text.lstrip())
            if indent <= outer:
                break                          # dedented: the block is over
            out.append((indent, text.strip(), n))
        return out

    def receiver(call):
        """The bare name a method was called on, or "" if it was called on an
        expression.

        `bank.balance("savings")` is an RPC because `bank` names something in
        the program. `row.split(" ")` has the identical shape and is not: it
        is a string method inside a lambda. The grammar states the shape once
        and the difference is decided here, on whether the receiver is a plain
        name at all.
        """
        target = call.children[0]
        if isinstance(target, Token):
            return _tok(target)
        return _tok(target.children[0]) if getattr(target, "data", None) == "var" else ""

    def calls_in(node) -> list:
        """Every `Target.method(...)` inside a def, in source order."""
        # `chf: int = bank.balance("savings")` — remember the name, so the
        # value the call returns can be handed to the next one.
        bound = {}
        for let in node.find_data("let_stmt"):
            for inner in let.find_data("remote_call"):
                bound[id(inner)] = _tok(let.children[0])

        # Which calls were written inside a `with parallel():` block. The
        # block's own line names the group: it is stable, it sorts the way the
        # source reads, and it is the line to point an error at.
        grouped = {}
        for block in node.find_data("with_stmt"):
            for inner in block.find_data("remote_call"):
                grouped[id(inner)] = position(block)[0]

        found = []
        for call in node.find_data("remote_call"):
            target, method = receiver(call), call.children[1]
            if not target:
                continue                  # a method on an expression, not a call out
            arglist = next((c for c in call.children[2:]
                            if getattr(c, "data", None) == "args"), None)
            args, options, uses = [], {}, []
            for child in (arglist.children if arglist is not None else []):
                if getattr(child, "data", None) == "kwarg":
                    key, value = child.children
                    options[_tok(key)] = _literal(value)
                else:
                    args.append(_literal(child))
                    if getattr(child, "data", None) == "var":
                        uses.append(_tok(child.children[0]))
            found.append(RemoteCall(target, _tok(method), args, options,
                                    position(call)[0], bound.get(id(call), ""),
                                    grouped.get(id(call), 0), uses))
        return sorted(found, key=lambda c: c.line)

    def blocks_in(node) -> list:
        """Every `with …():` block in a def, in source order."""
        out = []
        for block in node.find_data("with_stmt"):
            inside = [b for b in block.find_data("with_stmt") if b is not block]
            out.append(WithBlock(_tok(block.children[1]), position(block)[0],
                                 bool(inside),
                                 len([c for c in block.find_data("remote_call")
                                      if receiver(c)])))
        return sorted(out, key=lambda b: b.line)

    def method_of(node, decorators) -> Method:
        name, params, ret = "", None, "any"
        for child in node.children:
            if isinstance(child, Token):
                if child.type == "NAME" and not name:
                    name = str(child)
                elif child.type == "TYPE":
                    ret = str(child)
            elif getattr(child, "data", None) == "params":
                params = child
        names, types = [], []
        for p in (params.children if params is not None else []):
            pname, ptype = p.children
            names.append(_tok(pname))
            types.append(_tok(ptype))
        return Method(name, names, types, ret, body_of(node),
                      position(node)[0], decorators, calls_in(node),
                      blocks_in(node))

    def decorators_of(nodes) -> list:
        out = []
        for d in nodes:
            args, kwargs = _dec_args(
                next((c for c in d.children
                      if getattr(c, "data", None) == "dec_args"), None))
            merged = dict(kwargs)
            if args:
                merged["_args"] = args
            out.append(Decorator(_tok(d.children[0]), merged, position(d)[0]))
        return out

    def add_class(node, decorators):
        name = next(str(c) for c in node.children
                    if isinstance(c, Token) and c.type == "NAME")
        cls = ClassDecl(name, decorators, {}, position(node)[0])
        for child in node.children:
            kind = getattr(child, "data", None)
            if kind == "let_stmt":
                # `balance: int = 100` in a class body — what a machine of this
                # kind remembers, and what it starts at.
                fname, ftype, value = child.children
                cls.state[_tok(fname)] = StateField(
                    _tok(fname), _tok(ftype), _literal(value),
                    position(child)[0])
            elif kind == "func_def":
                m = method_of(child, [])
                cls.methods[m.name] = m
            elif kind == "decorated":
                inner = child.children[-1]
                decs = decorators_of(child.children[:-1])
                if getattr(inner, "data", None) == "func_def":
                    m = method_of(inner, decs)
                    cls.methods[m.name] = m
        mod.classes[cls.name] = cls

    def arg_sources(call) -> list:
        """Each argument of a call, as the text the student wrote.

        Sliced out of the source rather than rebuilt from the tree: a lambda
        body is Python, and the only thing that reads Python correctly is
        Python. The grammar's job is to say where the argument ends.
        """
        arglist = next((c for c in call.children[1:]
                        if getattr(c, "data", None) == "args"), None)
        if arglist is None:
            return []
        meta = getattr(arglist, "meta", None)
        if meta is not None and not getattr(meta, "empty", True):
            blanked = hidden.get(meta.start_pos)
            if blanked is not None:
                # A hidden argument list stays one piece here; `pyspark.build`
                # hands it to Python, which is the only thing that can say
                # where one argument ends and the next begins.
                return [blanked[1]]
        out = []
        for child in arglist.children:
            meta = getattr(child, "meta", None)
            if meta is not None and not getattr(meta, "empty", True):
                out.append(source[meta.start_pos:meta.end_pos])
            elif isinstance(child, Token):
                out.append(str(child))
            else:
                out.append(_tok(child))
        return out

    def add_assign(node) -> bool:
        """
        `x = Something(...)` — either a machine or a job.

        Which one is decided by whether `Something` is a class this program
        declares. Instantiating a declared class makes a machine; anything else
        with named arguments is a job. That ordering is why assignments are
        handled after every class has been seen.
        """
        var = _tok(node.children[0])
        call = node.children[1]
        # A chained step reads as `words = lines.flatMap(...)`, whose value is
        # a reference plus a chain rather than a call, so both shapes get in.
        if getattr(call, "data", None) not in ("source_call", "source_ref"):
            return False
        kind = _tok(call.children[0])

        if kind == "World":
            arglist = next((c for c in call.children[1:]
                            if getattr(c, "data", None) == "args"), None)
            settings = {}
            for child in (arglist.children if arglist is not None else []):
                if getattr(child, "data", None) == "kwarg":
                    key, value = child.children
                    settings[_tok(key)] = _literal(value)
            mod.worlds[var] = World(var, settings, position(node)[0])
            return True

        # An RDD step: a source call, or a chain off an existing one.
        chain = next((ch for ch in node.children[1:]
                      if getattr(ch, "data", None) == "chain"), None)
        if kind not in mod.classes and (chain is not None
                                        or getattr(call, "data", None) == "source_ref"
                                        or kind in RDD_SOURCES):
            parents, op, steps = [], kind, []
            if getattr(call, "data", None) == "source_ref":
                parents = [_tok(call.children[0])]
                op = "source"
            else:
                steps.append((kind, arg_sources(call)))
            for step in (chain.children if chain is not None else []):
                op = _tok(step.children[0])
                steps.append((op, arg_sources(step)))
            mod.rdds.append(Rdd(var, op, parents, position(node)[0], steps))
            return True

        if kind in mod.classes:
            arglist = next((c for c in call.children[1:]
                            if getattr(c, "data", None) == "args"), None)
            settings = {}
            for child in (arglist.children if arglist is not None else []):
                if getattr(child, "data", None) == "kwarg":
                    key, value = child.children
                    settings[_tok(key)] = _literal(value)
            mod.instances[var] = Instance(var, kind, settings, position(node)[0])
            return True
        return add_job(node, var, call, kind)

    def add_job(node, var, call, kind) -> bool:
        """`job = MapReduce(map=tokenize, ...)` — the visible wiring."""
        arglist = next((c for c in call.children[1:]
                        if getattr(c, "data", None) == "args"), None)
        if kind == "PySpark":
            return add_pyspark(node, var, arglist)
        roles, settings = {}, {}
        for child in (arglist.children if arglist is not None else []):
            if getattr(child, "data", None) != "kwarg":
                continue
            key, value = child.children
            name = _tok(key)
            # A role is filled by naming a function; a setting is given a value.
            # `map=tokenize` is a bare name, `mappers=3` is a number — so the
            # two are told apart by what was written, not by a list of allowed
            # keywords that would have to grow with every new option.
            (roles if name in ROLES else settings)[name] = _literal(value)
        if not roles and not settings:
            return False
        mod.jobs.append(Job(var, kind, roles, settings, position(node)[0]))
        return True

    def add_pyspark(node, var, arglist) -> bool:
        """
        `job = PySpark(\"\"\"…\"\"\", lose="counts")` — a pipeline written in
        real PySpark.

        The block arrives as one STRING token, so the line it starts on is the
        token's own line. Everything inside is read by `pyspark.py`, and every
        diagnostic it produces is shifted by that offset — otherwise the editor
        would underline line 3 of a string nobody can see.
        """
        block, block_line, settings = "", position(node)[0], {}
        for child in (arglist.children if arglist is not None else []):
            if getattr(child, "data", None) == "kwarg":
                key, value = child.children
                settings[_tok(key)] = _literal(value)
            elif getattr(child, "data", None) == "string" and not block:
                token = child.children[0]
                raw = _tok(token)
                block_line = (token.line or block_line) if hasattr(token, "line") \
                    else block_line
                block = raw[3:-3] if raw.startswith('\"\"\"') else raw.strip('"')
        if not block:
            return False
        mod.jobs.append(Job(var, "PySpark", {}, settings, position(node)[0],
                            source=block, source_line=block_line))
        return True

    deferred = []          # assignments, handled once every class is known

    def add(node, decorators):
        kind = getattr(node, "data", None)
        if kind == "class_def":
            add_class(node, decorators)
        elif kind == "func_def":
            fn = method_of(node, decorators)
            mod.functions[fn.name] = fn
        elif kind in ("assign", "action"):
            # Both need every declaration in hand: an assignment has to know
            # whether its callee is a class, and `world.run(job)` has to know
            # what `world` and `job` are.
            deferred.append(node)
        elif kind in ("input_decl", "split_decl"):
            # The keyword is a token of the rule, so the name is the first
            # NAME rather than the first child.
            names = [c for c in node.children
                     if isinstance(c, Token) and c.type == "NAME"]
            texts = [c for c in node.children
                     if isinstance(c, Token) and c.type == "STRING"]
            lists = [c for c in node.children
                     if getattr(c, "data", None) == "string_list"]
            if names:
                rows = ([_tok(t).strip('"') for t in lists[0].children]
                        if lists else [_tok(t).strip('"') for t in texts])
                mod.inputs[_tok(names[0])] = rows
        else:
            mod.statements.append((_tok(node), position(node)[0]))

    try:
        tree = parser().parse(masked)
    except Exception as e:
        line = getattr(e, "line", 1) or 1
        col = getattr(e, "column", 1) or 1
        # A pipeline line gets a pipeline's answer. "syntax error here" is
        # what the grammar knows; on a line carrying a lambda it is not enough
        # to act on, and Python can say exactly what is wrong.
        from .pyspark import explain
        better = explain(lines[line - 1] if line <= len(lines) else "", line, col)
        if better is not None:
            return mod, [better]
        named = _untyped_local(lines, line)
        if named is not None:
            at, var = named
            return mod, [Diagnostic(
                at, 1, "error",
                f"{var} is given a value but never a type",
                hint=f"write the type you expect, e.g. {var}: int = ...")]
        return mod, [Diagnostic(line, col, "error", "syntax error here",
                                hint="check the line above this one too")]

    for node in tree.children:
        if getattr(node, "data", None) == "decorated":
            add(node.children[-1], decorators_of(node.children[:-1]))
        else:
            add(node, [])

    # Assignments last: `fast = Worker(speed=0.3)` can only be told from a job
    # once it is known that `Worker` is a class this program declares, and a
    # program is not obliged to declare it first.
    def add_action(node) -> bool:
        """`world.run(job, on=[…], times=3)` — perform a job in a world."""
        target, method = _tok(node.children[0]), _tok(node.children[1])
        if target not in mod.worlds or method != "run":
            return False
        arglist = next((c for c in node.children[2:]
                        if getattr(c, "data", None) == "args"), None)
        job, options = "", {}
        for child in (arglist.children if arglist is not None else []):
            if getattr(child, "data", None) == "kwarg":
                key, value = child.children
                options[_tok(key)] = _literal(value)
            elif not job:
                job = str(_literal(child))
        mod.runs.append(Run(target, job, options, position(node)[0]))
        return True

    for node in deferred:
        is_action = getattr(node, "data", None) == "action"
        handled = add_action(node) if is_action else add_assign(node)
        if not handled:
            mod.statements.append((_tok(node), position(node)[0]))
            if is_action:
                mod.dropped.append((_tok(node.children[0]),
                                    _tok(node.children[1]),
                                    position(node)[0]))

    # Every parameter must carry a type; the grammar makes that structural, so
    # a missing one is a parse error rather than something to re-check here.
    return mod, diags


# Where a pipeline starts: these make an RDD rather than reading one.
RDD_SOURCES = ("textFile", "parallelize")


# Things every machine can do to itself, whatever it models. Written as calls
# on the machine — `MapServer.crash()` — so there is no statement form to learn.
LIFECYCLE = ("crash", "restart")

# Calls the runtime answers on any machine's behalf, so a class does not have
# to declare them. `crash` and `restart` act on a machine; `send` and
# `broadcast` are how processes talk. None of them is a method a student
# writes, so none may be reported as one the class fails to answer.
BUILTIN_CALLS = (*LIFECYCLE, "send", "broadcast")


def check_calls(mod: Module) -> list[Diagnostic]:
    """
    Every call must name something that exists and answers it.

    Nothing here is specific to any one exercise. "That name is not declared"
    and "that thing does not answer this" are the same mistakes whether the
    call crosses a network, a stage boundary or nothing at all — so there is
    one check, not one per dialect. A decorator says what a class *means* to
    the runtime; it does not change what a call *is*.
    """
    diags: list[Diagnostic] = []

    # Every function that can contain a call, not only the methods. A job's
    # work is an ordinary function — `def story()` — and it was not looked at,
    # so `bank.balance(...)` against a machine with no `balance` was reported
    # nowhere. It ran, too: the call came back `unimplemented` with no reply,
    # the caller bound None, and the next call was handed None as its argument.
    # A typo in a method name produced a run that looked like a run.
    callers = [method
               for cls in mod.classes.values()
               for method in cls.methods.values()]
    callers += list(mod.functions.values())

    for caller in callers:
        for call in caller.calls:
            # A call names a machine that exists — an instance — not the
            # class it is an instance of. `Worker.run()` is a category
            # error: there may be three Workers, and the call has to say
            # which one it is talking to.
            instance = mod.instances.get(call.target)
            if instance is None:
                if call.target in mod.classes:
                    made = [i.var for i in mod.instances.values()
                            if i.cls == call.target]
                    diags.append(Diagnostic(
                        call.line, 1, "error",
                        f"{call.target} is a kind of machine, not one you "
                        f"can call",
                        hint=(f"call one of: {', '.join(made)}" if made else
                              f"make one first, e.g. "
                              f"server = {call.target}()")))
                else:
                    known = ", ".join(mod.instances) or "none yet"
                    diags.append(Diagnostic(
                        call.line, 1, "error",
                        f"there is no machine called {call.target!r}",
                        hint=f"machines in this program: {known}"))
                continue

            target = mod.classes.get(instance.cls)
            if (target is not None and call.method not in BUILTIN_CALLS
                    and call.method not in target.methods):
                answers = ", ".join(target.methods) or "nothing yet"
                diags.append(Diagnostic(
                    call.line, 1, "error",
                    f"{call.target} does not answer {call.method!r}",
                    hint=f"a {instance.cls} answers: {answers}"))
    return diags


def callers_in(mod: Module) -> list:
    """Every function that can contain a call: a method, or a job's work."""
    out = [method
           for cls in mod.classes.values()
           for method in cls.methods.values()]
    return out + list(mod.functions.values())


def check_dropped(mod: Module) -> list[Diagnostic]:
    """
    A line that does nothing must say so.

    `world.run(job)` is a top-level call the parser claims. Anything else of
    the same shape — `counts.collect()`, `out.nonsense()`, `nosuchrdd.count()`
    — was parsed, matched nothing, and was dropped on the floor. No result, no
    error, no squiggle in the editor, so the student concludes it worked.

    Writing `counts.collect()` at the end is the most reflexive thing a person
    carrying PySpark habits does, and silence is the worst possible answer to
    it. A typo'd RDD name is worse still: the line evaporates and the program
    runs as though it had never been written.

    This is the same failure as an engine filling in a default — a program
    scoring for work it never did — one level up. There the engine supplied
    what was missing; here the parser deletes what was present.
    """
    diags: list[Diagnostic] = []
    known = {r.var for r in mod.rdds}
    for receiver, method, line in mod.dropped:
        if receiver in known and method in pyspark.ACTIONS:
            diags.append(Diagnostic(
                line, 1, "error",
                f"{method}() here does nothing",
                hint=f"an action does not belong in the program text: the "
                     f"pipeline is handed to a job and the job is run, so "
                     f"the work happens at world.run(...). Delete this line, "
                     f"or pass {receiver} as the pipeline."))
        elif receiver in known:
            ops = ", ".join(sorted(pyspark.NARROW | pyspark.WIDE))
            diags.append(Diagnostic(
                line, 1, "error",
                f"{receiver} has no {method!r}",
                hint=f"an RDD answers: {ops}"))
        elif receiver in mod.instances:
            # A machine at the top level is doing something to itself — the
            # lifecycle calls, and the ways processes talk. Those are real
            # statements the runtime answers, so they are not dropped and must
            # not be reported. Anything else named on a machine here is a
            # method that does not exist.
            instance = mod.instances[receiver]
            cls = mod.classes.get(instance.cls)
            answers = set(BUILTIN_CALLS) | set(cls.methods if cls else ())
            if method not in answers:
                offer = ", ".join(sorted(answers)) or "nothing yet"
                diags.append(Diagnostic(
                    line, 1, "error",
                    f"{receiver} does not answer {method!r}",
                    hint=f"a {instance.cls} answers: {offer}"))
        elif receiver not in mod.worlds:
            names = ", ".join(sorted(known | set(mod.instances))) or "none yet"
            diags.append(Diagnostic(
                line, 1, "error",
                f"there is nothing called {receiver!r}",
                hint=f"in this program: {names}"))
        else:
            diags.append(Diagnostic(
                line, 1, "error",
                f"{receiver}.{method}() does nothing here",
                hint="a world is asked to run a job: world.run(job)"))
    return diags


def check_parallel(mod: Module) -> list[Diagnostic]:
    """
    What `with parallel():` means, and the two ways of getting it wrong.

    The block is the one place in the language where two things happen at
    once, so it is also the one place where the ordinary reading of a
    function — this line, then the next — does not hold. Both checks here are
    about that: a block that contains a call needing another call's answer has
    been written as if the lines still ran one after the other, and there is
    no order in which it could work.

    The name is checked rather than fixed by the grammar so the message can
    say what does exist, instead of the parser saying only that the line is
    wrong.
    """
    diags: list[Diagnostic] = []

    for caller in callers_in(mod):
        for block in getattr(caller, "blocks", []):
            if block.name != "parallel":
                diags.append(Diagnostic(
                    block.line, 1, "error",
                    f"there is no {block.name}() to run a block in",
                    hint="the only block is 'with parallel():', which sends "
                         "the calls inside it at the same time"))
                continue
            if block.nested:
                diags.append(Diagnostic(
                    block.line, 1, "error",
                    "a parallel block cannot contain another one",
                    hint="everything in one block already leaves together, "
                         "so a block inside it has nothing left to say"))
            if block.calls == 0:
                diags.append(Diagnostic(
                    block.line, 1, "warning",
                    "this parallel block calls nothing",
                    hint="put the calls that should happen at the same time "
                         "inside it"))
            elif block.calls == 1:
                diags.append(Diagnostic(
                    block.line, 1, "warning",
                    "a parallel block with one call in it is that call",
                    hint="one call is one call however it is written; a "
                         "second one in the block is what makes a difference"))

        # Nothing in the block can be given what another call in the same
        # block returns. Written sequentially this is ordinary — the whole
        # point of `chf` is that the next line uses it — which is exactly why
        # it has to be said here rather than left to produce a null.
        for line in sorted({c.group for c in caller.calls if c.group}):
            group = [c for c in caller.calls if c.group == line]
            bound = {c.bind for c in group if c.bind}
            for call in group:
                needs = [u for u in call.uses if u in bound and u != call.bind]
                if not needs:
                    continue
                diags.append(Diagnostic(
                    call.line, 1, "error",
                    f"this call is given {needs[0]!r}, which nothing has "
                    f"answered yet",
                    hint=f"every call in the block leaves at the same moment, "
                         f"so none of them can have {needs[0]!r} — move this "
                         f"call below the block, where the answers are in"))
    return diags


def check_jobs(mod: Module) -> list[Diagnostic]:
    """
    Every function handed to a job must fit the position it is given.

    This is the check the hardcoded `map`/`reduce`/`partition` whitelist could
    never make. A name told you nothing: a function called `map` was a mapper by
    decree, and one called `tokenize` could not be one at all. Now the signature
    decides, so `reduce=byKey` is caught — both take two parameters, but the
    second types differ, and only the student's own annotation makes that
    visible.
    """
    diags: list[Diagnostic] = []
    for job in mod.jobs:
        bound: dict = {}          # the job's value type, shared across its roles
        for role_name, fn_name in job.roles.items():
            role = ROLES.get(role_name)
            if role is None:
                known = ", ".join(sorted(ROLES))
                diags.append(Diagnostic(
                    job.line, 1, "error",
                    f"{job.kind} takes no {role_name!r}",
                    hint=f"it takes: {known}"))
                continue
            fn = mod.functions.get(fn_name)
            if fn is None:
                declared = ", ".join(sorted(mod.functions)) or "none yet"
                diags.append(Diagnostic(
                    job.line, 1, "error",
                    f"there is no function called {fn_name!r}",
                    hint=f"you have declared: {declared}"))
                continue
            diags += check_role(fn, role, bound, job.line)
    return diags


def check_machines(mod: Module) -> list[Diagnostic]:
    """
    A machine's settings have to mean something.

    `on_crash` in particular: it is the one setting whose wrong value is
    invisible. A misspelling silently reads as the default, so a student who
    wrote `on_crash="restarts"` would watch their machine stay dead and
    conclude that restarting does not work.
    """
    diags: list[Diagnostic] = []
    for inst in mod.instances.values():
        cls = mod.classes.get(inst.cls)
        if cls is None or cls.kind not in ("machine", "mapper", "reducer", "process"):
            continue
        for key, value in inst.settings.items():
            if key == "on_crash":
                if str(value) not in CRASH_BEHAVIOURS:
                    diags.append(Diagnostic(
                        inst.line, 1, "error",
                        f"{value!r} is not something a machine can do about crashing",
                        hint='on_crash is "stay_dead" or "restart"'))
                continue
            # `vault = Ledger(balance=500)` — this machine starts out
            # remembering something else. Same field, same type; only the
            # value it begins at is the instance's to choose.
            held = cls.state.get(key)
            if held is not None:
                from .expr import compatible, type_of
                if not compatible(type_of(value), held.type):
                    diags.append(Diagnostic(
                        inst.line, 1, "error",
                        f"{key} is {inst.cls}'s state and is {held.type}, "
                        f"but {value!r} is a {type_of(value)}"))
                continue
            if key not in MACHINE_SETTINGS:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                diags.append(Diagnostic(
                    inst.line, 1, "error",
                    f"{key} needs a number, not {value!r}"))
                continue
            if key == "error_rate" and not 0.0 <= number <= 1.0:
                diags.append(Diagnostic(
                    inst.line, 1, "error",
                    f"error_rate is a likelihood, so it lies between 0 and 1 "
                    f"(got {number})",
                    hint="0.25 means it breaks about one time in four"))
            if key in ("speed", "restart_after") and number <= 0:
                diags.append(Diagnostic(
                    inst.line, 1, "error", f"{key} has to be greater than 0"))
        if (str(inst.settings.get("on_crash", "stay_dead")) == "stay_dead"
                and "restart_after" in inst.settings):
            diags.append(Diagnostic(
                inst.line, 1, "warning",
                f"{inst.var} says how long it takes to restart, but not that "
                f"it restarts",
                hint='add on_crash="restart"'))
    return diags


# The types a machine may remember. `void` is the absence of a value, so
# there is nothing there to hold; everything else the language has is
# something a machine can be handed back later.
STATE_TYPES = ("int", "string", "[int]", "[string]", "pair")


def check_state(mod: Module) -> list[Diagnostic]:
    """
    What a machine remembers has to be something it can remember.

    Four mistakes, all of them silent without this. A field declared `void`
    holds nothing, so the machine would read as one that forgets instantly. A
    field whose starting value disagrees with its written type is the mistake
    the language already refuses inside a function, and it has to be the same
    mistake here. A method whose parameter carries the field's name shadows
    it, so the line the student wrote to update the machine updates the
    argument instead and the state never moves — which looks exactly like the
    simulator being broken. And an update that renames the type is the same
    slip one line further on.
    """
    from .expr import compatible, type_of

    diags: list[Diagnostic] = []
    for cls in mod.classes.values():
        if cls.state and not cls.methods:
            # A field only ever changes from inside a method. A class with
            # state and nothing to run it is a machine that will hold its
            # starting value all run and look, on the diagram, exactly like
            # one that is working.
            first = min(cls.state.values(), key=lambda f: f.line)
            diags.append(Diagnostic(
                first.line, 1, "warning",
                f"{cls.name} remembers something, but has no method that "
                f"could change it",
                hint="what a machine remembers changes inside a method it "
                     "answers; without one it holds its starting value all "
                     "run"))
        for f in cls.state.values():
            if f.type not in STATE_TYPES:
                diags.append(Diagnostic(
                    f.line, 1, "error",
                    f"a machine cannot remember a {f.type}",
                    hint=f"state is one of: {', '.join(STATE_TYPES)}"))
                continue
            got = type_of(f.value)
            if not compatible(got, f.type):
                diags.append(Diagnostic(
                    f.line, 1, "error",
                    f"{f.name} is declared {f.type}, but it starts at a {got}",
                    hint=f"give it a {f.type} to start from"))
            if f.name in cls.methods:
                diags.append(Diagnostic(
                    f.line, 1, "error",
                    f"{cls.name} has both a method and a field called "
                    f"{f.name!r}",
                    hint="one name means one thing"))

        for method in cls.methods.values():
            for param in method.params:
                if param in cls.state:
                    diags.append(Diagnostic(
                        method.line, 1, "error",
                        f"{method.name}'s parameter {param!r} has the same "
                        f"name as {cls.name}'s state",
                        hint=f"rename the parameter — inside a method, "
                             f"{param} is what the machine remembers"))
            # Updating a field is an ordinary typed binding, so the type on it
            # is the field's own. Writing another one would change what the
            # machine holds into something the next method to read it cannot
            # use.
            for _indent, text, line in method.body:
                m = BIND_RE.match(text)
                if m is None:
                    continue
                held = cls.state.get(m.group("var"))
                if held is None or m.group("type") == held.type:
                    continue
                diags.append(Diagnostic(
                    line, 1, "error",
                    f"{held.name} is {cls.name}'s state and is {held.type}, "
                    f"but this line calls it {m.group('type')}",
                    hint=f"write '{held.name}: {held.type} = ...' to update "
                         f"it"))
    return diags


def check_world(mod: Module) -> list[Diagnostic]:
    """
    Nothing runs outside a world.

    A machine on its own is a description; it is the world that says which
    machines exist together and under what conditions. Without one there is no
    system, so there is nothing to run — and inventing a default would be the
    tool quietly answering a question the student was asked.
    """
    diags: list[Diagnostic] = []
    if not mod.worlds:
        if mod.instances or mod.jobs:
            diags.append(Diagnostic(
                1, 1, "error", "no world to run in",
                hint="describe the system first, e.g. "
                     "world = World(machines=[...])"))
        return diags

    known = set(mod.instances)
    for world in mod.worlds.values():
        for name in world.machines:
            if name not in known:
                diags.append(Diagnostic(
                    world.line, 1, "error",
                    f"{name!r} is not a machine in this program",
                    hint=f"machines you have made: {', '.join(known) or 'none'}"))

    for run in mod.runs:
        if run.job and run.job not in {j.var for j in mod.jobs}:
            diags.append(Diagnostic(
                run.line, 1, "error", f"there is no job called {run.job!r}",
                hint="a job is built first, e.g. job = MapReduce(map=...)"))
        for name in run.on:
            if name not in mod.worlds[run.world].machines:
                diags.append(Diagnostic(
                    run.line, 1, "error",
                    f"{name!r} is not in {run.world}",
                    hint="a job can only use machines its world contains"))

    if mod.jobs and not mod.runs:
        world = next(iter(mod.worlds))
        diags.append(Diagnostic(
            mod.jobs[0].line, 1, "error", "the job is never run",
            hint=f"run it in the world you described: {world}.run(job)"))
    return diags


def lint(source: str) -> tuple[Module, list[Diagnostic]]:
    """Parse and check a program. One front end, one checker, every dialect."""
    mod, diags = from_tree(source)
    return mod, diags or (check_calls(mod) + check_parallel(mod)
                          + check_dropped(mod)
                          + check_jobs(mod) + check_world(mod)
                          + check_machines(mod) + check_state(mod))


# Settings every machine has, whatever kind it is, with what each means when
# it was not given. A mapper and a service are the same sort of thing here:
# both take time, both can break, and both have to say what happens after.
MACHINE_SETTINGS = {
    "speed": (1.0, float),
    "error_rate": (0.0, float),
    "on_crash": ("stay_dead", str),
    "restart_after": (1.0, float),
}

# What a machine may say it does after it breaks.
CRASH_BEHAVIOURS = ("stay_dead", "restart")


def declared_machines(source: str) -> list:
    """
    Every machine the running world contains: (name, kind, settings).

    One reading of the world for every dialect. MapReduce used to read only
    `speed` off its mappers and Spark did not read the world at all — it
    invented `executor-1..n` from a count — so the same declaration meant
    three different things depending on which exercise you were in. Settings
    fall back to the class decorator, then to the defaults above.
    """
    mod, _ = from_tree(source)

    chosen: list = []
    for run in mod.runs:
        world = mod.worlds.get(run.world)
        if world is None:
            continue
        chosen = run.on or world.machines
        break
    if not chosen and mod.worlds:
        chosen = list(next(iter(mod.worlds.values())).machines)

    out: list = []
    for var in chosen:
        inst = mod.instances.get(var)
        if inst is None:
            continue
        cls = mod.classes.get(inst.cls)
        declared = cls.decorators[0].args if cls is not None and cls.decorators else {}
        settings = {}
        for key, (default, cast) in MACHINE_SETTINGS.items():
            raw = inst.settings.get(key, declared.get(key, default))
            try:
                settings[key] = cast(raw)
            except (TypeError, ValueError):
                settings[key] = default
        out.append((var, cls.kind if cls else "", settings))
    return out


def to_funcs(mod: Module) -> dict:
    """
    Every function in the module as `expr.Func`, so the existing type checker
    and runtime work on them unchanged — a method is just a function that a
    machine happens to own.
    """
    from .expr import Func

    out = {}
    for name, f in mod.functions.items():
        out[name] = Func(name, f.params, f.body, f.line,
                         types=f.types, ret=f.ret)
    for cls in mod.classes.values():
        for name, f in cls.methods.items():
            # Qualified so two services may both have a `handle`.
            out[f"{cls.name}.{name}"] = Func(
                name, f.params, f.body, f.line, types=f.types, ret=f.ret)
    return out


# --- roles ---------------------------------------------------------------
# What a function must look like to be usable in a given position.
#
# The student writes the whole declaration — its name, how many parameters it
# takes, and the type of each — and then hands it to a job:
#
#     def readSensor(station: string, payload: string) -> void:
#         for reading: string in split(payload):
#             emit(station, 1)
#
#     job = MapReduce(map=readSensor, reduce=hottest, partition=spread)
#
# (The example is deliberately in a domain no task uses. This module ships to
# students, so what it demonstrates is documentation.)
#
# Nothing is called `map` by decree any more. What makes `readSensor` a mapper
# is that it fits: two parameters, both strings, emitting rather than returning.
# That is a property of the signature the student wrote, which is exactly what
# writing signatures out is for — so the check below is the language keeping
# its side of the bargain, not an extra hurdle.

@dataclass
class Role:
    """A position a function can be passed into."""
    name: str
    params: list          # required parameter types; a TypeVar matches anything
    ret: object
    summary: str

    def arity(self) -> int:
        return len(self.params)


# The value type a job carries is written V: it is whatever the student's own
# annotations say, consistent across the functions of one job.
ROLES = {
    "map":       Role("map", ["string", "string"], "void",
                      "called once per input record; emits pairs"),
    "reduce":    Role("reduce", ["string", "[V]"], "V",
                      "called once per key with all of its values"),
    "partition": Role("partition", ["string", "int"], "int",
                      "chooses a reducer for a key"),
    "combine":   Role("combine", ["string", "[V]"], "V",
                      "reduces on the mapper, before the shuffle"),
    # A job that is simply a sequence of calls. MapReduce is handed the three
    # functions it orchestrates; this one is handed the single function that
    # *is* the work, so a program of plain calls is a job like any other rather
    # than something smuggled inside a machine method.
    "run":       Role("run", [], "void",
                      "performs the calls this job is made of"),
}


def check_role(fn, role: Role, bound: dict, line: int) -> list:
    """
    Whether a student's function can be used in this position.

    Reports what is wrong in terms of the declaration they wrote — the number
    of parameters, then each type in turn — rather than announcing that some
    fixed signature was expected. `bound` carries the job's value type between
    functions, so a mapper emitting ints and a reducer taking [string] is
    caught as the disagreement it is.
    """
    diags = []
    want, got = role.arity(), len(fn.params)
    if want != got:
        names = ", ".join(f"{p}: {t}" for p, t in zip(fn.params, fn.types))
        diags.append(Diagnostic(
            line, 1, "error",
            f"{fn.name} takes {got} parameter(s), but a {role.name} is called "
            f"with {want}",
            hint=f"you wrote {fn.name}({names}), but a {role.name} "
                 f"{role.summary}, so it needs "
                 f"{', '.join(role.params)}"))
        return diags

    for i, (declared, required) in enumerate(zip(fn.types, role.params)):
        if declared == "any":
            continue
        expected = _resolve(required, bound, declared)
        if expected is None or declared == expected:
            continue
        diags.append(Diagnostic(
            line, 1, "error",
            f"{fn.name}'s parameter {fn.params[i]!r} is {declared}, but a "
            f"{role.name} receives {expected} there",
            hint=f"a {role.name} {role.summary}"))
    return diags


def _resolve(required: str, bound: dict, declared: str):
    """
    The concrete type a position requires, binding V on first sight.

    Returns None when the requirement cannot be pinned down, so an unknown
    never turns into a wrong error message.
    """
    if required == "V":
        return bound.setdefault("V", declared)
    if required == "[V]":
        seen = bound.get("V")
        if seen is None:
            if declared.startswith("[") and declared.endswith("]"):
                bound["V"] = declared[1:-1]
                return declared
            # V is still open, but that a list belongs here is not: whatever the
            # job's value type turns out to be, `[V]` is a list of it. Returning
            # None here let a plain `int` stand where a list was required, which
            # is how a partitioner could be passed as a reducer unnoticed.
            return "a list"
        return f"[{seen}]"
    return required
