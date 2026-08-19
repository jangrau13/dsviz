"""
Spark notation.

Students write an RDD pipeline, using the operations they meet in Assignment 3:
textFile, flatMap, mapToPair, reduceByKey, filter, groupByKey, sortByKey,
cache, count, collect.

    executors 3
    input lines: "the cat sat" | "the dog ran"

    words   = textFile(lines).flatMap(split(value))
    pairs   = words.mapToPair(value, 1)
    counts  = pairs.reduceByKey(a + b)
    counts.cache()
    counts.collect()

Each assignment builds an RDD, and the lineage between them is what Spark
recomputes from when a partition is lost. Narrow operations pipeline inside a
stage; wide ones (reduceByKey, groupByKey, sortByKey) force a shuffle and so
begin a new stage — which is the thing worth seeing.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .core import Cluster
from .expr import Budget, BUILTINS, _eval_expr
from .notation import Diagnostic, NotationError, _strip
from .patterns import Lineage, hash_partition

# Operations, and whether they force a shuffle (wide) or not (narrow).
NARROW = {"flatMap", "map", "mapToPair", "filter", "mapValues", "cache",
          "persist", "distinct", "sample", "union"}
WIDE = {"reduceByKey", "groupByKey", "sortByKey", "join", "repartition",
        "coalesce", "aggregateByKey"}
ACTIONS = {"collect", "count", "foreach", "take", "saveAsTextFile", "reduce",
           "first"}

OPS = NARROW | WIDE | ACTIONS

RULES_SPARK = [
    ("executors", re.compile(r"^executors\s+(?P<n>\d+)$", re.I)),
    ("partitions", re.compile(r"^partitions\s+(?P<n>\d+)$", re.I)),
    ("input",    re.compile(r'^input\s+(?P<name>\w+)\s*:\s*(?P<data>.+)$', re.I)),
    # ARG allows one level of nesting so `flatMap(split(value))` parses.
    ("assign",   re.compile(
        r"^(?P<target>\w+)\s*=\s*(?P<source>\w+(?:\((?:[^()]|\([^()]*\))*\))?)"
        r"(?P<chain>(?:\s*\.\s*\w+\((?:[^()]|\([^()]*\))*\))*)\s*$")),
    ("action",   re.compile(
        r"^(?P<rdd>\w+)\s*\.\s*(?P<op>\w+)\s*"
        r"\((?P<arg>(?:[^()]|\([^()]*\))*)\)\s*$")),
    ("lose",     re.compile(r"^lose\s+(?P<rdd>\w+)(?:\s+on\s+(?P<who>[\w-]+))?$", re.I)),
    ("speed",    re.compile(r"^(?P<who>[\w-]+)\s+speed\s+(?P<v>[\d.]+)$", re.I)),
    ("expect",   re.compile(r"^expect\s+(?P<key>\S+)\s*=\s*(?P<count>\d+)$", re.I)),
    ("budget",   re.compile(r"^budget\s+(?P<metric>\w+)\s*(?P<op><|<=|>|>=)\s*(?P<value>[\d.]+)$", re.I)),
    ("note",     re.compile(r"^note\s+(?P<text>.+)$", re.I)),
]

CALL_RE = re.compile(r"\.\s*(?P<op>\w+)\s*\((?P<arg>(?:[^()]|\([^()]*\))*)\)")


def is_spark_program(source: str) -> bool:
    return bool(re.search(r"^\s*(executors|input\s+\w+\s*:|.*\btextFile\s*\()",
                          source, re.I | re.M))


def parse_spark(source: str) -> tuple[list[tuple], list[Diagnostic]]:
    stmts, diags = [], []
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip(raw).strip()
        if not line:
            continue
        for kind, pat in RULES_SPARK:
            m = pat.match(line)
            if m:
                stmts.append((kind, m.groupdict(), i))
                break
        else:
            diags.append(Diagnostic(
                i, 1, "error", f"cannot parse: {line!r}",
                hint="expected e.g. 'words = textFile(lines).flatMap(split(value))', "
                     "'counts.collect()', 'executors 3'"))
    return stmts, diags


def _calls(chain: str) -> list[tuple[str, str]]:
    return [(m.group("op"), m.group("arg").strip()) for m in CALL_RE.finditer(chain or "")]


def lint_spark(source: str) -> list[Diagnostic]:
    """Check that every RDD exists before use and every operation is known."""
    stmts, diags = parse_spark(source)
    rdds: dict[str, int] = {}
    inputs: dict[str, int] = {}

    for kind, g, line in stmts:
        if kind == "input":
            inputs[g["name"]] = line
        elif kind == "assign":
            src = g["source"]
            m = re.match(r"^(\w+)\((.*)\)$", src)
            if m:
                fn, arg = m.group(1), m.group(2).strip()
                if fn != "textFile":
                    diags.append(Diagnostic(
                        line, 1, "error", f"unknown source {fn!r}",
                        hint="a pipeline starts with textFile(<input>)"))
                elif arg and arg not in inputs:
                    diags.append(Diagnostic(
                        line, 1, "error", f"unknown input {arg!r}",
                        hint=f"declared inputs: {', '.join(inputs) or 'none'}; "
                             'add e.g. \'input lines: "the cat sat"\''))
            elif src not in rdds and src not in inputs:
                diags.append(Diagnostic(
                    line, 1, "error", f"unknown RDD {src!r}",
                    hint=f"defined so far: {', '.join(rdds) or 'none'}"))

            for op, arg in _calls(g["chain"]):
                if op not in OPS:
                    diags.append(Diagnostic(
                        line, 1, "error", f"unknown operation {op!r}",
                        hint=f"try one of: {', '.join(sorted(NARROW | WIDE))}"))
                elif op in ACTIONS:
                    diags.append(Diagnostic(
                        line, 1, "warning",
                        f"{op}() is an action: it returns a result, not an RDD",
                        hint=f"assigning the result of {op}() is usually a mistake"))
            rdds[g["target"]] = line

        elif kind == "action":
            if g["rdd"] not in rdds and g["rdd"] not in inputs:
                diags.append(Diagnostic(
                    line, 1, "error", f"unknown RDD {g['rdd']!r}",
                    hint=f"defined so far: {', '.join(rdds) or 'none'}"))
            if g["op"] not in OPS:
                diags.append(Diagnostic(
                    line, 1, "error", f"unknown operation {g['op']!r}"))

        elif kind == "lose" and g["rdd"] not in rdds:
            diags.append(Diagnostic(
                line, 1, "error", f"cannot lose unknown RDD {g['rdd']!r}",
                hint=f"defined so far: {', '.join(rdds) or 'none'}"))

    if not any(k == "assign" for k, _, _ in stmts):
        diags.append(Diagnostic(
            1, 1, "warning", "no RDDs defined",
            hint="build a pipeline, e.g. words = textFile(lines).flatMap(split(value))"))
    elif not any(k == "action" for k, _, _ in stmts):
        diags.append(Diagnostic(
            1, 1, "warning", "no action — nothing forces the job to run",
            hint="Spark is lazy: add e.g. counts.collect() or counts.count()"))
    return diags


# --- evaluation ---------------------------------------------------------

def _apply(op: str, arg: str, data: list, budget: Budget, line: int) -> list:
    """
    Apply one RDD operation to a list of records.

    Records are either bare values or (key, value) pairs, matching how a
    student thinks about `JavaRDD` versus `JavaPairRDD`.
    """
    if op in ("cache", "persist"):
        return data

    if op == "flatMap":
        out = []
        for v in data:
            budget.spend()
            out.extend(_eval_expr(arg, {"value": v, "line": v}, line, budget))
        return out

    if op == "map":
        return [_eval_expr(arg, {"value": v}, line, budget) for v in data]

    if op == "mapToPair":
        # `mapToPair(value, 1)` — two expressions making a pair.
        parts = [p.strip() for p in arg.split(",")]
        if len(parts) != 2:
            raise NotationError([Diagnostic(
                line, 1, "error", "mapToPair takes a key and a value",
                hint="e.g. mapToPair(value, 1)")])
        return [(_eval_expr(parts[0], {"value": v}, line, budget),
                 _eval_expr(parts[1], {"value": v}, line, budget)) for v in data]

    if op == "filter":
        return [v for v in data
                if _eval_expr(arg, {"value": v[1] if isinstance(v, tuple) else v,
                                    "key": v[0] if isinstance(v, tuple) else v},
                              line, budget)]

    if op == "mapValues":
        return [(k, _eval_expr(arg, {"value": v}, line, budget)) for k, v in data]

    if op == "distinct":
        seen, out = set(), []
        for v in data:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    if op in ("reduceByKey", "aggregateByKey"):
        groups: dict = defaultdict(list)
        for k, v in data:
            groups[k].append(v)
        out = []
        for k, vals in groups.items():
            acc = vals[0]
            for nxt in vals[1:]:
                budget.spend()
                acc = _eval_expr(arg, {"a": acc, "b": nxt}, line, budget)
            out.append((k, acc))
        return out

    if op == "groupByKey":
        groups: dict = defaultdict(list)
        for k, v in data:
            groups[k].append(v)
        return list(groups.items())

    if op == "sortByKey":
        return sorted(data, key=lambda kv: kv[0])

    if op in ("repartition", "coalesce", "union", "sample"):
        return data

    raise NotationError([Diagnostic(
        line, 1, "error", f"cannot apply {op!r} here")])


def build_spark(source: str, *, seed: int | None = None):
    """Run a Spark program. Returns (cluster, lineage, results, expects, budgets)."""
    diags = [d for d in lint_spark(source) if d.severity == "error"]
    if diags:
        raise NotationError(diags)

    stmts, _ = parse_spark(source)
    budget = Budget()
    inputs: dict[str, list] = {}
    rdds: dict[str, list] = {}
    lineage = Lineage()
    n_exec, n_part = 2, 2
    speeds: dict[str, float] = {}
    losses, expects, budgets, notes = [], [], [], []
    stage_of: dict[str, int] = {}
    stage = 0

    for kind, g, line in stmts:
        if kind == "executors":
            n_exec = int(g["n"])
        elif kind == "partitions":
            n_part = int(g["n"])
        elif kind == "speed":
            speeds[g["who"]] = float(g["v"])
        elif kind == "input":
            # `input lines: "a b" | "c d"` — one record per quoted chunk.
            parts = re.findall(r'"([^"]*)"', g["data"]) or [g["data"].strip()]
            inputs[g["name"]] = parts
        elif kind == "expect":
            expects.append((g["key"], int(g["count"]), line))
        elif kind == "budget":
            budgets.append((g["metric"].lower(), g["op"], float(g["value"]), line))
        elif kind == "lose":
            losses.append((g["rdd"], g.get("who"), line))
        elif kind == "note":
            notes.append(g["text"])

        elif kind == "assign":
            src, target = g["source"], g["target"]
            m = re.match(r"^(\w+)\((.*)\)$", src)
            if m:
                data = list(inputs.get(m.group(2).strip(), []))
                parents = []
            else:
                data = list(rdds[src])
                parents = [src]
                stage = stage_of.get(src, 0)

            ops = _calls(g["chain"])
            for op, arg in ops:
                if op in WIDE:
                    stage += 1          # a shuffle begins a new stage
                data = _apply(op, arg, data, budget, line)

            rdds[target] = data
            stage_of[target] = stage
            lineage.rdd(target, parents=parents,
                        op=" → ".join(o for o, _ in ops) or "textFile")

        elif kind == "action":
            rdd, op, arg = g["rdd"], g["op"], g["arg"]
            if op in ACTIONS:
                notes.append(f"{op}() on {rdd}")
            else:
                rdds[rdd] = _apply(op, arg, list(rdds[rdd]), budget, line)

    # --- simulate the pipeline on a cluster ---
    c = Cluster("spark", seed=seed)
    # The executors are the machines the world was given, with the names and
    # settings they were declared with. This used to invent `executor-1..n`
    # from a count and ignore the world entirely, so `e3 = Executor(speed=0.4)`
    # named a straggler that never appeared on the picture.
    from .syntax import declared_machines
    declared = [(name, traits) for name, kind, traits in declared_machines(source)
                if kind in ("machine", "executor", "process")]
    if not declared:
        declared = [(f"executor-{i+1}",
                     {"speed": speeds.get(f"executor-{i+1}", 1.0)})
                    for i in range(n_exec)]
    execs = [c.machine(name, role="executor", **traits) for name, traits in declared]

    by_stage: dict[int, list[str]] = defaultdict(list)
    for name, s in stage_of.items():
        by_stage[s].append(name)

    def assign(i: int):
        """The executor that takes the i-th piece of this stage.

        Round-robin, but stepping over anything that is down. A dead executor
        is not given more work — the scheduler notices, which is the whole
        reason a job survives losing one.
        """
        for step in range(len(execs)):
            m = execs[(i + step) % len(execs)]
            if m.up_at(m.clock):
                return m
        return None

    for s in sorted(by_stage):
        for i, name in enumerate(sorted(by_stage[s])):
            m = assign(i)
            if m is None:
                c.note(f"every executor is down — {name} cannot be computed")
                continue
            m.work(f"{name}", duration=max(len(rdds[name]), 1) * 0.15)
            if not m.alive:
                # An executor that breaks loses the partitions it was holding.
                # This is the case Spark answers rather than suffers: the
                # lineage still says how each one was made, so it goes into the
                # recompute list with the losses the program asked for.
                losses.append((name, m.name, 0))
                continue
            m.hold(name)
        # A wide dependency ships data between executors before the next stage.
        if s < max(by_stage):
            for i, m in enumerate(execs):
                target = execs[(i + 1) % len(execs)]
                if target is not m and m.up_at(m.clock):
                    m.send(target, f"stage{s}-shuffle", latency=0.3)
        c.barrier(f"stage {s}")

    for rdd, who, line in losses:
        holder = next((m for m in execs if rdd in m.items),
                      c.machines.get(who, execs[0]))
        if holder.alive:
            holder.crash(at=holder.clock, lose_state=True)
        needed = lineage.recompute_set(rdd)
        c.note(f"lost {rdd} — recomputing {' → '.join(needed)} from lineage")
        if holder.on_crash == "restart":
            holder.restart(at=holder.clock + holder.restart_after)
        else:
            # It said it stays dead, so the work moves to someone else — which
            # is the honest picture: recomputation does not need *that* machine
            # back, only the lineage.
            holder = next((m for m in execs if m.alive), holder)
            if not holder.alive:
                c.note(f"nothing left alive to recompute {rdd} on")
                continue
        for node in needed:
            holder.work(f"recompute {node}", duration=0.6)
            if not holder.alive:
                # It broke again mid-recompute. Somebody else picks it up;
                # the lineage does not care who runs it.
                holder = next((m for m in execs if m.up_at(m.clock)), holder)
                if not holder.alive:
                    break
                holder.work(f"recompute {node}", duration=0.6)
            holder.hold(node)

    for text in notes:
        c.note(text)

    return c, lineage, rdds, expects, budgets
