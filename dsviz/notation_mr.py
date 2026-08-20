"""
MapReduce notation.

The student-facing syntax for the MapReduce exercise. Shares the diagnostic
and type machinery with the vector-clock notation, so the editor treats both
the same way.

    partitions 2

    split doc1: "the cat sat"
    split doc2: "the dog ran"

    combiner on              # aggregate locally before the shuffle
    capacity 8

    expect the = 3
"""

from __future__ import annotations

import re
from collections import Counter

from .contest import CaseResult, Submission, Verdict
from .expr import (Budget, bind_helpers, check_functions, parse_functions,
                   run_function)
from .machine_types import DEFAULT_TYPE, get as machine_type
from .notation import Diagnostic, NotationError, _strip
from .patterns import map_reduce


def job_settings(source: str) -> dict:
    """How the student asked the job to be run: machines, partitions, capacity."""
    from .syntax import from_tree

    mod, _ = from_tree(source)
    return dict(mod.jobs[0].settings) if mod.jobs else {}


def job_workers(source: str) -> tuple[list, list, dict]:
    """
    The machines the student declared, with the settings each was given.

    A machine is a machine. Which half of a job it does is not written on its
    class and never was true of one — a worker in a real cluster is handed a
    map task or a reduce task by the master, and the same worker can be handed
    both. So there is nothing to sort here: these are the machines the world
    has, and the job decides what they do.

        @machine
        class Worker:
            pass

        fast = Worker(speed=1.0)
        slow = Worker(speed=0.3)

    Returning those names means the timeline shows `slow` rather than
    `machine-2`, which is the point of naming it — a straggler is recognisable
    on the picture only if the picture uses the name the student gave it.
    """
    from .syntax import from_tree

    mod, _ = from_tree(source)
    if not mod.jobs:
        return [], {}

    settings = mod.jobs[0].settings
    traits: dict = {}

    def traits_of(var: str) -> dict:
        """
        Everything this machine was declared with, not just how fast it is.

        A mapper carries the same settings a service does — how likely it is to
        break, and what it does about it — because it is the same kind of thing
        and fails the same way. Reading only `speed` here is what used to make
        a MapReduce program incapable of failing however it was written.
        """
        inst = mod.instances.get(var)
        if inst is None:
            return dict(machine_type(DEFAULT_TYPE).settings())
        cls = mod.classes.get(inst.cls)
        declared = cls.decorators[0].args if cls is not None and cls.decorators else {}
        out: dict = {}
        for key, default, cast in (("error_rate", 0.0, float),
                                   ("restart_after", 1.0, float),
                                   ("on_crash", "stay_dead", str)):
            out[key] = cast(inst.settings.get(key, declared.get(key, default)))
        # What the machine is, as opposed to how it behaves when it breaks:
        # the type it was bought as decides its processor and its room.
        chosen = str(inst.settings.get("type", declared.get("type", DEFAULT_TYPE)))
        out.update(machine_type(chosen).settings())
        return out

    def kind_of(var: str) -> str:
        inst = mod.instances.get(var)
        cls = mod.classes.get(inst.cls) if inst else None
        return cls.kind if cls else ""

    # Machines come from the world the job was run in, narrowed to whatever
    # subsystem that run was given. What each one *is* — a mapper or a reducer —
    # is already written on its class, so the job never has to be told again.
    chosen: list = []
    for run in mod.runs:
        world = mod.worlds.get(run.world)
        if world is None:
            continue
        chosen = run.on or world.machines
        break

    if not chosen:
        # Machines named on the job itself, for a program that wires them there
        # rather than describing a world. Without either there is nothing to
        # run on, and `check_world` has already said so.
        value = settings.get("machines")
        if isinstance(value, list):
            chosen += [str(v) for v in value]

    machines = [m for m in chosen if kind_of(m) in ("machine", "process")]
    for m in machines:
        traits[m] = traits_of(m)
    return machines, traits


def job_roles(source: str) -> dict:
    """
    Which function the student passed for each position.

    Read off the one parse tree, from the line they wrote:

        job = MapReduce(map=tokenize, reduce=total, partition=byKey)

    A program with no such line declares no job, so nothing runs — which is the
    honest outcome, rather than silently adopting whatever happens to be called
    `map`.
    """
    from .syntax import from_tree

    mod, _ = from_tree(source)
    return dict(mod.jobs[0].roles) if mod.jobs else {}


def job_line(source: str) -> int:
    """The line the job was declared on, or 0 if there is no job."""
    from .syntax import from_tree

    mod, _ = from_tree(source)
    return mod.jobs[0].line if mod.jobs else 0


def last_line(source: str) -> int:
    """
    Where a line that is missing should have gone.

    A complaint about something absent has no line of its own, and reporting it
    against line 1 puts the editor's squiggle on the first line of the task
    description — the one place the student certainly does not need to change.
    The end of what they wrote is where the missing line belongs.
    """
    lines = source.replace("\r\n", "\n").split("\n")
    for n in range(len(lines), 0, -1):
        text = lines[n - 1].strip()
        if text and not text.startswith("#"):
            return n
    return 1

RULES_MR = [
    ("partitions", re.compile(r"^partitions\s+(?P<n>\d+)$", re.I)),
    ("split",     re.compile(r'^split\s+(?P<name>[\w.-]+)\s*:\s*"(?P<text>[^"]*)"$', re.I)),
    ("combiner",  re.compile(r"^combiner\s+(?P<state>on|off)$", re.I)),
    ("capacity",  re.compile(r"^capacity\s+(?P<n>\d+)$", re.I)),
    ("crash",     re.compile(r"^(?P<who>[\w-]+)\s+crashes(?:\s+at\s+(?P<at>[\d.]+))?$", re.I)),
    ("expect",    re.compile(r"^expect\s+(?P<key>\S+)\s*=\s*(?P<count>\d+)$", re.I)),
    ("note",      re.compile(r"^note\s+(?P<text>.+)$", re.I)),
    # `use` is resolved before checking; see project.py.
    ("use",       re.compile(r"^use\s+[\w./-]+$", re.I)),
    # `job = MapReduce(map=…, reduce=…)` — the wiring line. It is read off the
    # parse tree by `job_roles`, and checked there against each function's
    # signature; this entry only stops the line-oriented pass from calling it
    # unparseable.
    ("job",       re.compile(r"^\w+\s*=\s*\w+\s*\(.*\)$")),
]

FUNC_HEAD = re.compile(r"^def\s+\w+\s*\(")

# Declarations the grammar owns: decorated classes and their bodies. The
# line-oriented pass below predates them and would call each one unparseable,
# so it steps over them and leaves the checking to `syntax.lint`.
DECLARATION = re.compile(
    r"^(?:@\w+|class\s+\w+\s*:|pass$|\w+\s*\.\s*\w+\s*\(.*\)$)")


def parse_mr(source: str) -> tuple[list[tuple], list[Diagnostic]]:
    stmts, diags = [], []
    in_func = False
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip(raw).strip()
        if not line:
            continue
        # Function definitions and their indented bodies are handled by
        # `expr.parse_functions`, so skip them here.
        if raw[:1] in (" ", "\t") or FUNC_HEAD.match(line) or DECLARATION.match(line):
            in_func = True
            continue
        if in_func and not raw[:1].isspace():
            in_func = False
        for kind, pat in RULES_MR:
            m = pat.match(line)
            if m:
                stmts.append((kind, m.groupdict(), i))
                break
        else:
            diags.append(Diagnostic(
                i, 1, "error", f"cannot parse: {line!r}",
                hint='expected e.g. \'split doc1: "the cat sat"\', '
                     "'partitions 2', 'combiner on', 'expect the = 3'"))
    return stmts, diags


def lint_mr(source: str) -> list[Diagnostic]:
    """Static checks: a program must define work, and stay within its limits."""
    from .syntax import lint as lint_program

    stmts, diags = parse_mr(source)
    funcs, fdiags = parse_functions(source)
    diags += fdiags + check_functions(funcs, job_roles(source).get("map"))
    # The declarative half — world, machines, job wiring — is checked by the
    # one checker rather than re-stated here.
    diags += lint_program(source)[1]
    splits = [g for k, g, _ in stmts if k == "split"]
    partitions = [g for k, g, _ in stmts if k == "partitions"]

    if not splits:
        diags.append(Diagnostic(
            1, 1, "error", "no input splits declared",
            hint='add e.g. \'split doc1: "the cat sat"\''))

    seen: dict[str, int] = {}
    for kind, g, line in stmts:
        if kind == "split":
            if g["name"] in seen:
                diags.append(Diagnostic(
                    line, 1, "error", f"split {g['name']!r} is already defined "
                    f"on line {seen[g['name']]}",
                    hint="split names must be unique"))
            seen[g["name"]] = line
        elif kind == "partitions" and int(g["n"]) < 1:
            diags.append(Diagnostic(
                line, 1, "error", "need at least one partition"))
        elif kind == "speed" and float(g["v"]) <= 0:
            diags.append(Diagnostic(
                line, 1, "error", "speed must be greater than zero",
                hint="0.5 runs at half rate; 1.0 is nominal"))

    if not partitions and "partitions" not in job_settings(source):
        diags.append(Diagnostic(
            1, 1, "warning", "no partition count given — defaulting to 2",
            hint="say so on the job: MapReduce(..., partitions=2)"))
    return diags


def build_mr(source: str, *, seed: int | None = None):
    """Run a MapReduce program. Returns (cluster, expectations).

    `seed` fixes the failure draws, so one run out of a hundred can be pulled
    back and looked at.
    """
    diags = [d for d in lint_mr(source) if d.severity == "error"]
    if diags:
        raise NotationError(diags)

    stmts, _ = parse_mr(source)
    splits: dict[str, str] = {}
    n_map = n_red = None
    speeds: dict[str, float] = {}
    capacity = None
    combiner = False
    crash = None
    expects: list[tuple[str, int, int]] = []     # key, count, line

    for kind, g, line in stmts:
        if kind == "split":
            splits[g["name"]] = g["text"]
        elif kind == "partitions":
            n_red = int(g["n"])
        elif kind == "speed":
            speeds[g["who"]] = float(g["v"])
        elif kind == "capacity":
            capacity = int(g["n"])
        elif kind == "combiner":
            combiner = g["state"].lower() == "on"
        elif kind == "crash":
            crash = (g["who"], float(g["at"]) if g.get("at") else 0.0)
        elif kind == "expect":
            expects.append((g["key"], int(g["count"]), line))

    funcs, _ = parse_functions(source)
    bind_helpers(funcs)          # the student's own functions become callable
    budget = Budget()

    # How the student asked for it to be run: `partitions=2` on the job line.
    # Saying how many ways the keys are split belongs next to the functions
    # being run, not in a separate configuration dialect.
    settings = job_settings(source)
    machines, traits = job_workers(source)
    # `m1 speed 0.5` in the old configuration lines still works; a machine
    # declared in the world wins, because that is where it is described.
    for name, speed in speeds.items():
        traits.setdefault(name, {})["speed"] = traits.get(name, {}).get("speed", speed)
    if machines:
        n_map = len(machines)
    if "capacity" in settings:
        capacity = int(settings["capacity"])
    if "combiner" in settings:
        combiner = str(settings["combiner"]).lower() in ("on", "true", "1")

    # Which function fills each position. The student writes that down —
    # `job = MapReduce(map=tokenize, reduce=total, partition=byKey)` — so
    # nothing is a mapper by virtue of being spelled `map`, and the wiring is
    # visible on the page instead of happening by convention.
    roles = job_roles(source)

    # Every position must be filled by a function the student wrote. There used
    # to be a default behind each one — a built-in word count, a built-in sum,
    # a built-in hash — so a submission that bound nothing still produced
    # almost-right answers and scored most of the marks off the engine's own
    # implementation. A reference solution written before `job =` existed
    # scored 5/7 that way, and the two it missed were the two that needed
    # `lower()`: the giveaway was output that had never been through the
    # student's mapper at all.
    #
    # Nothing is filled in for them now. A job that does not say who does the
    # work is not a job.
    required = ("map", "reduce", "partition")
    if not roles:
        raise NotationError([Diagnostic(
            last_line(source), 1, "error",
            "this program never says which functions do the work",
            hint="wire them up with e.g. "
                 "job = MapReduce(map=…, reduce=…, partition=…) and then "
                 "world.run(job)")])
    # How many ways the keys are split. This is the `n` the partitioner is
    # handed, so it is the student's to choose and not the framework's to
    # assume — a job that does not say gets told to say. Checked after the
    # roles, because a program with no job at all has a better error waiting
    # for it above than one about partitions.
    if "partitions" in settings:
        n_red = int(settings["partitions"])
    elif n_red is None:
        raise NotationError([Diagnostic(
            job_line(source) or last_line(source), 1, "error",
            "this job does not say how many partitions it has",
            hint="write partitions=2 on the MapReduce line — it is the `n` "
                 "your partitioner is handed")])

    missing = [r for r in required if not roles.get(r)]
    if missing:
        raise NotationError([Diagnostic(
            job_line(source) or last_line(source), 1, "error",
            f"the job does not say which function is the "
            f"{', '.join(missing)}",
            hint="every position has to be filled: "
                 "MapReduce(map=…, reduce=…, partition=…)")])
    def bound(role: str):
        """The function passed for this role, if it was passed and parsed."""
        name = roles.get(role)
        return funcs.get(name) if name else None

    def call(fn, values):
        """Apply a student function, binding arguments to the names *they* wrote."""
        return run_function(fn, dict(zip(fn.params, values)), budget)

    # The mapper the student passed replaces the default word count.
    mapper_fn = None
    fn = bound("map")
    if fn is not None:
        def mapper_fn(name, text, _fn=fn):
            # The pairs a mapper makes are what it answers with, so there is
            # nothing to collect: the list it returns is the list of pairs.
            return call(_fn, [name, text]) or []
    elif combiner:
        mapper_fn = lambda name, text: list(Counter(text.split()).items())

    # A combiner aggregates locally before the shuffle.
    cfn = bound("combine")
    if cfn is not None and mapper_fn is not None:
        base = mapper_fn
        def mapper_fn(name, text, _base=base, _cfn=cfn):
            pairs = _base(name, text)
            grouped: dict = {}
            for k, v in pairs:
                grouped.setdefault(k, []).append(v)
            return [(k, call(_cfn, [k, vs])) for k, vs in grouped.items()]

    # The reducer replaces the default sum.
    reducer_fn = None
    rfn = bound("reduce")
    if rfn is not None:
        def reducer_fn(key, values, _rfn=rfn):
            return call(_rfn, [key, list(values)])

    # The partitioner replaces the default hash.
    partition_fn = None
    pfn = bound("partition")
    if pfn is not None:
        def partition_fn(key, n, _pfn=pfn):
            got = call(_pfn, [key, n])
            if not isinstance(got, (int, float)):
                raise NotationError([Diagnostic(
                    _pfn.line, 1, "error",
                    f"{_pfn.name} must return an int, but it returned "
                    f"{type(got).__name__}",
                    hint="a partitioner chooses a reducer, so it answers with "
                         "a number")])
            return int(got) % n

    cluster = map_reduce(
        splits, partitions=n_red or 2, mappers_count=n_map,
        machine_names=machines or None,
        traits=traits, capacity=capacity, mapper=mapper_fn,
        reduce=reducer_fn, partition=partition_fn, crash=crash, seed=seed)
    return cluster, expects


def judge_mr(source: str) -> Submission:
    """
    Judge a MapReduce program.

    Correctness only. Whether a working submission is a *good* design is a
    question of money, answered by `pricing` against a scenario, and it never
    fails anybody.
    """
    sub = Submission()
    try:
        cluster, expects = build_mr(source)
    except NotationError as e:
        d = e.diagnostics[0]
        sub.results.append(CaseResult(
            "compile", Verdict.CE, f"line {d.line}: {d.message}"))
        return sub

    counts = {e.detail["key"]: e.detail["value"]
              for e in cluster.trace.of_kind("output")}

    for key, want, line in expects:
        got = counts.get(key)
        ok = got == want
        sub.results.append(CaseResult(
            f"expect {key}={want}",
            Verdict.AC if ok else Verdict.WA,
            "" if ok else f"line {line}: counted {got if got is not None else 0}",
            score=1.0 if ok else 0.0))

    return sub
