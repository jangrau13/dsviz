"""
Non-functional properties, measured from a trace.

Correctness is table stakes: for these exercises every working submission gets
the same answer. What separates a good design from a merely working one is how
it behaves — how much crosses the network, how badly load is skewed, how long
the tail is, what a failure costs.

Every metric here is derived from the `Trace`, so it applies unchanged to
MapReduce, Spark or any protocol expressed in the DSL.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .core import Trace


# What each measurement means, why it matters, and what to do about it.
# Surfaced on hover, so a number is never shown without an explanation.
EXPLANATIONS = {
    "makespan": (
        "Wall-clock time from the first task starting to the last one finishing.",
        "This is what a user waits for. It is set by the slowest path through "
        "the job, not by how much total work was done. Adding machines does "
        "not help if one of them is the bottleneck.",
        "Lower it by removing stragglers and by not serialising phases."),
    "network_msgs": (
        "How many intermediate pairs crossed the network during the shuffle.",
        "The shuffle is usually the most expensive part of a distributed job: "
        "disk and network are orders of magnitude slower than memory. Every "
        "pair a mapper emits has to be moved to the reducer that owns its key.",
        "A combiner aggregates on the mapper first and cuts this sharply, "
        "without changing the answer."),
    "load_imbalance": (
        "The busiest machine's work divided by the average machine's work.",
        "1.0 means perfectly even. Higher means some machines idle while one "
        "finishes, so you paid for parallelism you did not get. Usually caused "
        "by a partitioner that sends too many keys to one reducer, or by "
        "genuinely skewed data such as one very common word.",
        "Spread keys more evenly, or split the hot key."),
    "tail_ratio": (
        "The slowest task divided by the median task.",
        "Job time is the maximum, not the average. One slow worker holds up "
        "the barrier and everyone else waits, which is why adding machines "
        "sometimes does not speed a job up at all.",
        "Real systems answer this with speculative execution: run a second "
        "copy of the straggler elsewhere and take whichever finishes first."),
    "fault_cost": (
        "Work lost and redone because a machine failed.",
        "Counts items lost with a crashed machine, messages dropped in "
        "flight, and time spent recomputing. It distinguishes a design that "
        "rebuilds one partition from one that redoes an entire phase.",
        "MapReduce writes map output to disk so a lost reducer does not force "
        "re-running the maps. Spark instead recomputes from lineage."),
    "peak_items": (
        "The most items any single machine held at one time.",
        "The memory-pressure and skew measure. A reducer that receives most "
        "of the keys may not fit them in memory, which is where real jobs "
        "spill to disk or fail outright.",
        "Better partitioning, or a combiner, brings the peak down."),
    "parallelism": (
        "The average number of machines doing work at the same time.",
        "Higher is better: it says how much of your cluster was actually "
        "busy. A job with one reducer can look balanced, because every "
        "machine did similar work, while it serialises the whole reduce "
        "phase.",
        "More partitions gives more reducers that can run at once."),
}


@dataclass(frozen=True)
class Metric:
    """One measurement, with the direction that counts as better."""
    name: str
    value: float
    unit: str = ""
    lower_is_better: bool = True
    detail: str = ""

    @property
    def explanation(self) -> dict:
        """What it measures, why it matters, and what to do about it."""
        what, why, how = EXPLANATIONS.get(
            self.name, (self.detail, "", ""))
        return {"what": what, "why": why, "how": how,
                "better": "lower" if self.lower_is_better else "higher"}

    def __repr__(self):
        arrow = "↓" if self.lower_is_better else "↑"
        return f"{self.name:<18} {self.value:>10.3f} {self.unit:<8} {arrow} {self.detail}"


# --- individual measures ------------------------------------------------

def makespan(trace: Trace) -> Metric:
    """Wall-clock time to finish. The headline number."""
    return Metric("makespan", trace.duration, "s",
                  detail="job completion time")


def network_bytes(trace: Trace) -> Metric:
    """
    How many pairs crossed the network.

    The metric a combiner improves: aggregating locally before the shuffle cuts
    this sharply without changing the answer.
    """
    sends = trace.of_kind("send")
    return Metric("network_msgs", float(len(sends)), "msgs",
                  detail="pairs crossing the network; a combiner reduces this")


def load_imbalance(trace: Trace) -> Metric:
    """
    Ratio of the busiest machine's work to the mean.

    1.0 is perfect balance. High values mean a bad partitioner: one reducer
    doing most of the work while the rest idle.
    """
    per_machine: dict[str, float] = {}
    for e in trace.of_kind("work"):
        dur = e.detail.get("duration", e.detail.get("until", e.t) - e.t)
        per_machine[e.machine] = per_machine.get(e.machine, 0.0) + dur
    if not per_machine:
        return Metric("load_imbalance", 1.0, "×", detail="no work recorded")
    loads = list(per_machine.values())
    mean = statistics.fmean(loads)
    worst = max(loads)
    hot = max(per_machine, key=per_machine.get)
    return Metric("load_imbalance", worst / mean if mean else 1.0, "×",
                  detail=f"busiest is {hot}; 1.0 would be perfectly balanced")


def tail_latency(trace: Trace) -> Metric:
    """
    Slowest task divided by median task. The straggler measure.

    Why job time is not average task time: one slow worker holds up the
    barrier, and every other machine waits.
    """
    durs = [e.detail.get("duration", e.detail.get("until", e.t) - e.t)
            for e in trace.of_kind("work")]
    durs = [d for d in durs if d > 0]
    if len(durs) < 2:
        return Metric("tail_ratio", 1.0, "×", detail="too few tasks to compare")
    med = statistics.median(durs)
    return Metric("tail_ratio", max(durs) / med if med else 1.0, "×",
                  detail="slowest task vs median; high means stragglers")


def fault_cost(trace: Trace) -> Metric:
    """
    Work lost and redone because something failed.

    Distinguishes a design that recomputes one partition from lineage (cheap)
    from one that redoes an entire phase (expensive).
    """
    crashes = trace.of_kind("crash")
    lost = sum(len(e.detail.get("lost", [])) for e in crashes)
    redone = sum(
        e.detail.get("duration", 0.0) for e in trace.of_kind("work")
        if "recompute" in str(e.detail.get("label", "")).lower())
    dropped = len(trace.of_kind("drop"))
    return Metric("fault_cost", float(lost + dropped) + redone, "units",
                  detail=f"{len(crashes)} crash(es), {lost} items lost, "
                         f"{dropped} msg(s) dropped, {redone:.1f}s recomputed")


def peak_memory(trace: Trace) -> Metric:
    """Most items any single machine held at once — the skew/OOM measure."""
    peak, who = 0, "-"
    for e in trace.of_kind("hold"):
        if e.detail.get("total", 0) > peak:
            peak, who = e.detail["total"], e.machine
    over = [e.machine for e in trace.of_kind("hold")
            if e.detail.get("over_capacity")]
    return Metric("peak_items", float(peak), "items",
                  detail=f"held by {who}" + (
                      f"; over capacity on {len(set(over))} machine(s)" if over else ""))


def parallelism(trace: Trace) -> Metric:
    """
    Average number of machines busy at once, over the busy period.

    Catches the bottleneck that `load_imbalance` hides: a job with a single
    reducer can look well balanced — every machine does similar work — while
    still serialising the whole reduce phase. Higher is better.
    """
    work = [(e.t, e.t + e.detail.get("duration",
             e.detail.get("until", e.t) - e.t)) for e in trace.of_kind("work")]
    work = [(a, b) for a, b in work if b > a]
    if not work:
        return Metric("parallelism", 1.0, "×", lower_is_better=False,
                      detail="no work recorded")

    # Sweep the interval endpoints: between consecutive boundaries the number
    # of concurrently busy machines is constant.
    bounds = sorted({t for iv in work for t in iv})
    busy_area = 0.0
    span = 0.0
    for lo, hi in zip(bounds, bounds[1:]):
        width = hi - lo
        if width <= 0:
            continue
        n = sum(1 for a, b in work if a < hi and b > lo)
        if n:
            busy_area += n * width
            span += width
    avg = busy_area / span if span else 1.0
    return Metric("parallelism", avg, "×", lower_is_better=False,
                  detail="machines busy at once; higher is better overlap")


ALL_METRICS = [makespan, network_bytes, load_imbalance, tail_latency,
               fault_cost, peak_memory, parallelism]


# --- reporting ----------------------------------------------------------

def measure(trace: Trace) -> dict[str, Metric]:
    """Every metric, keyed by name."""
    return {m.name: m for m in (fn(trace) for fn in ALL_METRICS)}


def report(trace: Trace, *, title: str = "") -> str:
    """A human-readable scorecard."""
    lines = [f"== {title} ==" if title else "== metrics =="]
    lines += [repr(m) for m in measure(trace).values()]
    return "\n".join(lines)


def compare(submissions: dict[str, Trace], *, weights: dict[str, float] | None = None
            ) -> list[tuple[str, float, dict[str, Metric]]]:
    """
    Rank submissions on non-functional properties.

    Each metric is normalised against the best submission for that metric, so
    the score says "how far off the best anyone managed", not "how big is this
    number". Lower total is better.

        weights   {metric name: weight} — emphasise what the exercise is about
    """
    weights = weights or {}
    measured = {name: measure(tr) for name, tr in submissions.items()}
    if not measured:
        return []

    metric_names = list(next(iter(measured.values())))
    best: dict[str, float] = {}
    for m in metric_names:
        vals = [measured[s][m].value for s in measured]
        sample = next(iter(measured.values()))[m]
        best[m] = min(vals) if sample.lower_is_better else max(vals)

    ranked = []
    for name, metrics in measured.items():
        total = 0.0
        for m in metric_names:
            w = weights.get(m, 1.0)
            b, v = best[m], metrics[m].value
            if metrics[m].lower_is_better:
                ratio = v / b if b else (1.0 if v == 0 else float("inf"))
            else:
                ratio = b / v if v else float("inf")
            total += w * ratio
        ranked.append((name, total, metrics))

    return sorted(ranked, key=lambda r: r[1])
