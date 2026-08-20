"""
Exercise vocabulary.

One language for the course: these are thin helpers over `core`, not separate
DSLs. Each returns the same `Cluster`/`Trace` pair, so every exercise renders
through the same pipeline.

Covers MapReduce, Spark (lineage + recompute) and vector clocks.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

import networkx as nx

from .core import Cluster, Machine
from .values import Pair, Split

# How many times a restarting mapper is given its splits again before the job
# gives up on it. A machine with a high enough error_rate would otherwise die
# on every attempt for ever; a real master gives up too, and says so.
MAX_MAP_ATTEMPTS = 4


# --- shared -------------------------------------------------------------

def hash_partition(key, partitions: int) -> int:
    """The 31-hash the Java/JS assignments use, so pictures match student code."""
    h = 0
    for ch in str(key):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h) % partitions


# --- MapReduce ----------------------------------------------------------

def normalize_inputs(inputs) -> dict[str, str]:
    """
    Accept whatever the caller has to hand and return {split name: contents}.

    Takes a dict, a list of strings, a list of (name, text) pairs, a single
    string, or a path to a file or directory. Being liberal here means the
    exercises and the browser can share one entry point.
    """
    import os

    if isinstance(inputs, dict):
        return {str(k): str(v) for k, v in inputs.items()}

    if isinstance(inputs, (str, os.PathLike)):
        p = str(inputs)
        if os.path.isdir(p):
            names = sorted(os.listdir(p))
            return {n: open(os.path.join(p, n)).read() for n in names}
        if os.path.isfile(p):
            # One split per line keeps a plain text file usable as input.
            lines = [l.strip() for l in open(p) if l.strip()]
            return {f"split-{i+1}": l for i, l in enumerate(lines)}
        return {"split-1": p}          # a bare string is one split

    if isinstance(inputs, (list, tuple)):
        out = {}
        for i, item in enumerate(inputs):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out[str(item[0])] = str(item[1])
            else:
                out[f"split-{i+1}"] = str(item)
        return out

    raise TypeError(
        f"cannot read inputs of type {type(inputs).__name__} — pass a dict, "
        "list, string, or file path")


def map_reduce(inputs, *, partitions: int = 2,
               mapper: Callable[[str, str], Iterable[tuple]] | None = None,
               speeds: dict[str, float] | None = None,
               capacity: int | None = None,
               crash: tuple[str, float] | None = None,
               mappers_count: int | None = None,
               mapper_names: list | None = None,
               reducer_names: list | None = None,
               reduce: Callable[[object, list], object] | None = None,
               partition: Callable[[object, int], int] | None = None,
               traits: dict | None = None,
               seed: int | None = None) -> Cluster:
    """
    Run a MapReduce job and return the cluster holding its trace.

        inputs   {split name: contents}
        mapper   (name, contents) -> pairs; defaults to word count
        speeds   {machine: speed} — set one below 1.0 to make a straggler
        crash    (machine, time) — kill a machine mid-job
        mappers_count  how many mappers to use; defaults to one per split,
                 and splits are shared round-robin when there are fewer
        reduce   (key, values) -> value; defaults to sum
        partition (key, n) -> int; defaults to the 31-hash
        traits   {machine: settings} — error_rate and what it does about it
        seed     fixes the failure draws, so a run can be replayed

    `inputs` is liberal: a dict, list, string or file path (see
    `normalize_inputs`).

    The shuffle uses `hash_partition`, so the partition assignments on screen
    are the ones a student's own implementation produces.
    """
    inputs = normalize_inputs(inputs)
    mapper = mapper or (lambda name, text: [(w, 1) for w in text.split()])
    reduce = reduce or (lambda key, values: sum(values))
    partition = partition or hash_partition
    speeds = speeds or {}
    traits = traits or {}
    if partitions < 1:
        raise ValueError("need at least one partition")
    c = Cluster("mapreduce", seed=seed)

    def settings(name: str) -> dict:
        """How this machine was declared: its speed and how it fails."""
        t = dict(traits.get(name, {}))
        t.setdefault("speed", speeds.get(name, 1.0))
        return t

    # One mapper per split, any number of them, unless `mappers` caps it.
    # When the caller names its machines, those names are used as written — a
    # student who declares a slow mapper wants to see *that* name on the
    # timeline, not `mapper-2`.
    map_names = list(mapper_names or [])
    n_map = len(map_names) or mappers_count or len(inputs)
    if not map_names:
        map_names = [f"mapper-{i + 1}" for i in range(n_map)]
    red_names = list(reducer_names or []) or [f"reducer-{p}" for p in range(partitions)]

    names = list(inputs)
    assignment: dict[str, list[str]] = {m: [] for m in map_names}
    for i, name in enumerate(names):          # round-robin when splits > mappers
        assignment[map_names[i % n_map]].append(name)

    mappers = [
        c.machine(mname, role="mapper", splits=assignment[mname],
                  **settings(mname))
        for mname in assignment
    ]
    reducers = [
        c.machine(red_names[p], role="reducer", partition=p,
                  capacity=capacity, **settings(red_names[p]))
        for p in range(partitions)
    ]

    # map phase — each mapper emits pairs from its own split
    #
    # A mapper can break part-way through. Map output lives in the mapper's
    # memory, so a crash loses every pair it had produced, not just the split
    # it was on. What happens next is the machine's own business: one that
    # said it restarts comes back and has to do the whole thing again — which
    # is what a real master does when a worker dies, and it costs time; one
    # that stays dead takes its share of the answer with it, and the totals
    # come out wrong. Running it a hundred times is how you tell how often
    # each of those happens.
    emitted: dict[str, list[tuple]] = {}
    for m in mappers:
        for attempt in range(MAX_MAP_ATTEMPTS):
            pairs: list[tuple] = []
            for name in assignment[m.name]:
                # Record the split this mapper was handed, so the picture shows
                # the data the student's map function is actually looking at.
                c._emit(m.clock, "input", m.name, split=name,
                        value=Split(name, inputs[name]))
                got = [Pair(k, v) for k, v in mapper(name, inputs[name])]
                m.work(f"map({name})", duration=max(len(got), 1) * 0.4)
                if not m.alive:
                    break                       # it died on this split
                pairs.extend(got)
            if m.alive:
                break                           # got through the whole share
            if m.down_until is None:
                pairs = []                      # stays dead: its output is gone
                break
            m.restart(at=m.down_until)
            c.note(f"{m.name} came back — its map output was lost, so its "
                   f"splits are run again", at=m.clock)
        emitted[m.name] = pairs
        if pairs:
            m.hold(*pairs)

    if crash:
        who, when = crash
        c.machines[who].crash(at=when)
        emitted[who] = []
        c.note(f"{who} died — its map output is gone and must be recomputed",
               at=when)

    c.barrier("end of map")

    # shuffle — every pair crosses to the reducer owning its key
    for m in mappers:
        if not m.alive:
            continue
        for key, val in emitted[m.name]:
            target = c.machines[red_names[partition(key, partitions) % partitions]]
            m.send(target, (key, val))

    c.barrier("end of shuffle")

    # reduce phase — group by key, then collapse
    for r in reducers:
        grouped = defaultdict(list)
        for key, val in r.items:
            grouped[key].append(val)
        r.work(f"reduce({len(grouped)} keys)", duration=len(grouped) * 0.5)
        if not r.alive:
            # A reducer that dies holding a partition takes those keys with it.
            # It can come back, but the pairs it was sent are not re-sent, so
            # restarting a reducer does not recover the answer the way
            # restarting a mapper does — the input to a reduce is not on disk.
            c.note(f"{r.name} died holding its partition — those keys have "
                   f"no answer", at=r.clock)
            continue
        for key, vals in grouped.items():
            c._emit(r.clock, "output", r.name, key=key, value=reduce(key, vals))

    return c


# --- Spark --------------------------------------------------------------

class Lineage:
    """
    An RDD lineage graph, backed by networkx.

    The point Spark makes and MapReduce does not: when a partition is lost you
    do not restore it from disk, you *recompute* it from its ancestors. Holding
    the graph in networkx means the recompute set is just `nx.ancestors`.
    """

    def __init__(self):
        self.g = nx.DiGraph()

    def rdd(self, name: str, *, parents: Iterable[str] = (), op: str = "") -> str:
        self.g.add_node(name, op=op)
        for p in parents:
            self.g.add_edge(p, name)
        return name

    def recompute_set(self, lost: str) -> list[str]:
        """Everything that must be recomputed to rebuild `lost`, in order.

        The ancestors, because an RDD that is not held anywhere is made again
        from what made it. This is the right answer for a step that was never
        kept — an uncached RDD read by two branches really does replay its
        whole ancestry for the second reader.

        It is the wrong answer for a partition that was *lost*: see
        `rebuild_set`.
        """
        needed = nx.ancestors(self.g, lost) | {lost}
        return [n for n in nx.topological_sort(self.g) if n in needed]

    def rebuild_set(self, lost: str) -> list[str]:
        """Everything that must be recomputed because `lost` was lost.

        The step itself and everything derived from it — not its ancestors.
        Those did not go anywhere: one partition was lost, and its parents are
        still sitting on live executors, so the rebuild starts from them.
        What cannot survive is the lost step and every step already computed
        from it.

        Using the ancestors here inverted the lesson Assignment 2 is built on.
        Measured on the telemetry pipeline before this existed, losing each
        step of readings → parsed → byMonth → together → deltas → swing:

            lose=swing    (last)   410.40
            lose=together          406.80
            lose=parsed            298.80
            lose=readings (first)  234.00
            lose nothing at all    234.00

        Losing the first step of a six-step pipeline cost exactly what losing
        nothing cost, and the task asks the student to observe the opposite.
        The earlier a step sits the more descendants it has, which is why this
        set — and not the other one — makes the cost of a loss the size of
        what depended on it.
        """
        needed = nx.descendants(self.g, lost) | {lost}
        return [n for n in nx.topological_sort(self.g) if n in needed]

    def stages(self) -> list[list[str]]:
        """Nodes grouped into dependency levels — the shape of a stage DAG."""
        return [sorted(level) for level in nx.topological_generations(self.g)]


def spark_job(lineage: Lineage, *, executors: int = 2,
              lose: str | None = None) -> Cluster:
    """
    Run a lineage graph across executors, optionally losing one RDD partition
    and recomputing it from its ancestors.
    """
    c = Cluster("spark")
    execs = [c.machine(f"executor-{i+1}", role="executor") for i in range(executors)]

    for stage_no, stage in enumerate(lineage.stages()):
        for i, node in enumerate(stage):
            m = execs[i % len(execs)]
            m.work(f"{node} [{lineage.g.nodes[node].get('op','')}]", duration=1.0)
            m.hold(node)
        c.barrier(f"stage {stage_no}")

    if lose:
        holder = next((m for m in execs if lose in m.items), execs[0])
        holder.crash(at=holder.clock, lose_state=True)
        needed = lineage.recompute_set(lose)
        c.note(f"lost {lose} — recomputing {' → '.join(needed)} from lineage")
        holder.restart(at=holder.clock + 0.5)
        for node in needed:
            holder.work(f"recompute {node}", duration=1.0)
            holder.hold(node)

    return c


# --- Vector clocks ------------------------------------------------------

class VectorClockRun:
    """
    A message-passing run that maintains vector clocks.

    Rules: local event and send increment the sender's own entry; receive takes
    the pointwise max with the incoming stamp, then increments its own.
    """

    def __init__(self, *names: str):
        self.names = list(names)
        self.cluster = Cluster("vector-clocks")
        for n in names:
            self.cluster.machine(n, role="process")
        self.clocks = {n: [0] * len(names) for n in names}

    def _idx(self, name: str) -> int:
        return self.names.index(name)

    def event(self, who: str, label: str = "") -> VectorClockRun:
        self.clocks[who][self._idx(who)] += 1
        m = self.cluster.machines[who]
        m.work(label or "event", duration=1.0)
        self.cluster._emit(m.clock, "clock", who,
                           clock=list(self.clocks[who]), label=label)
        return self

    def send(self, frm: str, to: str, label: str = "", *,
             latency: float = 1.0) -> VectorClockRun:
        self.clocks[frm][self._idx(frm)] += 1
        stamp = list(self.clocks[frm])
        sender, receiver = self.cluster.machines[frm], self.cluster.machines[to]
        self.cluster._emit(sender.clock, "clock", frm, clock=stamp, label=label)
        sender.send(receiver, {"label": label, "clock": stamp}, latency=latency)

        # receive rule: pointwise max, then bump own entry
        merged = [max(a, b) for a, b in zip(self.clocks[to], stamp)]
        merged[self._idx(to)] += 1
        self.clocks[to] = merged
        self.cluster._emit(receiver.clock, "clock", to,
                           clock=list(merged), label=f"recv {label}")
        return self

    # -- the checkable part -------------------------------------------
    def assert_clock(self, who: str, expected: list[int]) -> VectorClockRun:
        """Check a claimed clock, reporting the causal reason when it differs."""
        actual = self.clocks[who]
        if actual != expected:
            raise AssertionError(
                f"{who}: you claim {expected}, but the run gives {actual}.\n"
                f"  {self._explain(who, expected, actual)}"
            )
        return self

    def _explain(self, who: str, expected: list[int], actual: list[int]) -> str:
        notes = []
        for i, (e, a) in enumerate(zip(expected, actual)):
            other = self.names[i]
            if e > a:
                notes.append(
                    f"{who} has not heard from {other} often enough for entry "
                    f"{i} to reach {e} (it is {a})")
            elif e < a:
                notes.append(
                    f"{who} already knows more about {other} than {e} "
                    f"(entry {i} is {a})")
        return "; ".join(notes) or "entries differ"

    def concurrent(self, a: str, b: str) -> bool:
        """True when neither clock dominates — the events are concurrent."""
        ca, cb = self.clocks[a], self.clocks[b]
        return (any(x < y for x, y in zip(ca, cb))
                and any(x > y for x, y in zip(ca, cb)))

    def happens_before(self, a: str, b: str) -> bool:
        ca, cb = self.clocks[a], self.clocks[b]
        return all(x <= y for x, y in zip(ca, cb)) and ca != cb
