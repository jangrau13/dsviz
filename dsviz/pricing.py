"""
What a design costs, and what it earns — in money.

Every other measure in this package is technical: messages, seconds, imbalance.
Technical measures cannot be traded against each other, because they are in
different units. You cannot say whether a second of latency is worth a replica.
Money is the only common denominator, so this module converts a `Trace` into a
profit-and-loss account and lets designs be compared as businesses.

The rule that keeps this honest: **money is the aggregator, never the
replacement**. Every line carries the technical quantity it was computed from,
so a bill reads `23,400 messages x CHF 0.09`, not `CHF 2,106`. The measure is
the multiplicand; `metrics.EXPLANATIONS` still says why it matters. A student
who only ever sees francs learns to optimise a cost function, which is the
opposite of the point.

Nothing here names a currency amount. Prices belong to a scenario, a scenario
belongs to an assignment, and an assignment is not the language's business —
the same rule that keeps tasks out of `dsviz`. This module supplies the shape
of a price list and the arithmetic over a trace; the francs arrive from
outside.

    Trace + Scenario  ->  PnL  ->  browser page / Manim / leaderboard

`PnL` is plain data, like `Trace` and `shapes`, which is what lets a lecture
video and a student's editor draw the same account.

Five buckets, because they are five different kinds of decision:

    build         what you paid to construct it        once, felt monthly
    capacity      the fleet you reserved               whether used or not
    consumption   what the work actually burned        with load
    incidents     what went wrong                      only when it does
    revenue       what the service earned              only when it works

A design moves money *between* these. Replication triples `capacity` and adds
to `build` in order to empty `incidents`. That see-saw is the whole lesson, and
it is why the buckets are reported separately rather than summed into one
number.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .core import Trace


# What each bucket means, why it exists, and what moves it. Shown on hover, so
# a franc is never displayed without an account of where it came from.
EXPLANATIONS = {
    "build": (
        "Engineering time to construct this design, spread over its life.",
        "A complicated architecture costs money before it processes anything. "
        "This is the line that stops 'just add another replica' from being "
        "free: every mechanism you add is days of somebody's work, and you "
        "carry it every month whether or not the thing it protects against "
        "ever happens.",
        "Fewer moving parts. Ask whether the mechanism earns its build cost "
        "in the incidents it actually prevents."),
    "capacity": (
        "The fleet you reserved, priced for the whole period.",
        "You pay for a machine that sits idle exactly as much as for one that "
        "is busy. Provisioning for the worst hour means paying for it in "
        "every other hour — which is fine if the worst hour is where the "
        "money is, and waste if it is not.",
        "Right-size to the load you actually expect, and let the incident "
        "lines tell you what under-provisioning costs."),
    "consumption": (
        "What the work burned: overtime machine-hours, storage, egress.",
        "This scales with load, so it is the line a better algorithm moves. "
        "Note how small egress usually is at real prices — a combiner does "
        "not pay for itself in bytes, it pays by shortening the job.",
        "Cut the work, not the price. Less shuffle, fewer passes, less spill."),
    "incidents": (
        "What failure cost: reruns, lost work, callouts, being late.",
        "Zero until something breaks, then large. This is the bucket every "
        "fault-tolerance mechanism is bought to empty, and comparing it "
        "against `build` and `capacity` is how you tell whether the "
        "mechanism was worth buying.",
        "Replicate, checkpoint, retry idempotently — then check the other "
        "buckets to see what it cost you."),
    "revenue": (
        "What the service earned by being available, fast and correct.",
        "Downtime does not cost you money; it stops you earning it. That is a "
        "different thing, and keeping it on the revenue side rather than as a "
        "penalty is what makes capacity an investment rather than pure waste.",
        "Serve more requests, inside the deadline, off a fresh result."),
    "not_earned": (
        "Revenue you did not get: refunds for stale answers, and the meter "
        "stopped while you were down.",
        "Availability stops being a percentage here and becomes the meter "
        "running. A stale answer is worse than no answer, because you refund "
        "it and pay a penalty on top.",
        "Shorten outages and tighten freshness. An answer nobody can trust "
        "is not worth serving."),
}

BUCKETS = ("revenue", "not_earned", "build", "capacity", "consumption", "incidents")

# Buckets that add to what you keep. Everything else is spent.
EARNING = ("revenue",)


# --- prices ------------------------------------------------------------

@dataclass(frozen=True)
class PriceVector:
    """
    What one business pays and earns. Every amount is in `currency`.

    This is a schema with no values: the numbers arrive from the assignment.
    Two scenarios over the identical workload and the identical failure script
    can differ *only* here, and a different design will win each — which is
    the cheapest way to show that the right architecture depends on what the
    business values rather than on what is technically elegant.
    """

    currency: str = "CHF"

    # -- how long the account covers, and how machines are billed
    period_hours: float = 730.0          # a month
    #: What each machine type costs per hour, by catalogue name. The names,
    #: the processor and the room are `machine_types`' — the language's. Only
    #: the price is here, because the price is what makes the choice
    #: interesting and a different exercise may want it to land differently.
    #:
    #: Price these superlinearly or there is no choice to make: if four times
    #: the processor costs four times the money, the large machine is never
    #: worse and nobody has to think. Real providers charge six or seven, and
    #: that is what makes spreading work over ordinary machines worth doing —
    #: and buying the large one worth doing anyway when the work will not
    #: split.
    machine_prices: dict[str, float] = field(default_factory=dict)
    machine_hour: float = 0.0            # a machine of no stated type, per hour
    #: How the price rises with speed. A machine at twice the rate costs
    #: `2 ** speed_exponent` times as much, so above 1.0 the fast box is worse
    #: value per unit of work than two ordinary ones — which is what the cloud
    #: actually charges, and what makes scaling up and scaling out a real
    #: choice rather than an obvious one. The fast box still wins whenever the
    #: work refuses to be split.
    speed_exponent: float = 1.0
    #: Per item of headroom, per hour. Memory is bought, not free: a machine
    #: big enough to hold the whole job costs more every hour than one that is
    #: only big enough when the data is spread across several.
    memory_item_hour: float = 0.0
    overtime_machine_hour: float = 0.0   # burst beyond the reservation

    # -- data
    storage_gb_month: float = 0.0
    egress_gb: float = 0.0
    bytes_per_message: float = 0.0       # what one message weighs on the wire

    # -- construction
    engineer_day: float = 0.0
    amortise_months: float = 24.0
    build_days: dict[str, float] = field(default_factory=dict)

    # -- when it breaks
    rerun: float = 0.0                   # one run redone from the start
    #: A machine given more than it can hold. The run does not finish, so
    #: nothing downstream is served and the work is redone somewhere it fits.
    #: This is what stops a design being scaled down until it is free: past a
    #: certain size the data does not fit on one machine, and the cheapest
    #: fleet that works is not the smallest fleet you can buy.
    out_of_memory: float = 0.0
    lost_item: float = 0.0               # work that died with a machine
    dropped_message: float = 0.0
    callout: float = 0.0                 # somebody woken up
    hour_late: float = 0.0               # downstream stalled

    # -- what it earns
    per_request: float = 0.0
    sla_seconds: float = 0.0             # answer inside this to earn full price
    slow_request: float = 0.0            # answered, but late — usually 0
    requests_per_run: float = 0.0        # batch: what one fresh result unlocks
    window_seconds: float = 0.0          # one run must finish inside this
    freshness_seconds: float = 0.0       # result older than this is stale
    stale_refund: float = 0.0            # paid back per stale answer served
    stale_penalty: float = 0.0           # and the fine on top

    def check(self) -> None:
        """
        Refuse a price list that pays for being wrong.

        If serving a stale answer nets more than serving a good one, the
        profitable strategy is to answer quickly and incorrectly, and the
        competition teaches the opposite of the course. That is a mistake in
        the price list rather than in a submission, so it fails loudly here
        instead of quietly rewarding the wrong thing.
        """
        wrong = self.stale_refund + self.stale_penalty
        if self.per_request > 0 and wrong <= self.per_request:
            raise ValueError(
                f"price list pays for being wrong: a stale answer costs "
                f"{wrong:g} but a good one earns {self.per_request:g}. "
                f"Refund plus penalty must exceed the revenue.")
        if self.period_hours <= 0:
            raise ValueError("period_hours must be positive")
        from .machine_types import names as machine_type_names
        known = machine_type_names()
        for name, rate in self.machine_prices.items():
            if name not in known:
                raise ValueError(
                    f"no machine type called {name!r}. The catalogue is "
                    f"{', '.join(known)} — a price for a machine nobody can "
                    f"buy is a typo, not an option.")
            if rate <= 0:
                raise ValueError(f"{name} must cost something")


@dataclass(frozen=True)
class Scenario:
    """
    A month in the life of a business: a workload, a failure script, a price
    list. The unit a design competes in.

    `workload` is opaque here. dsviz does not know what a news archive is; the
    assignment that declares the scenario reads its own keys back out when it
    builds the run.

    `hidden` scenarios follow the path held-out input already takes: the
    development copy lives beside the task and CI replaces it at hand-in. The
    *prices* are published either way — you cannot design for a business whose
    priorities are secret, and reading a price list to infer an architecture
    is the skill being examined.
    """

    name: str
    title: str
    business: str                        # the one-line story, for the page
    prices: PriceVector
    workload: dict = field(default_factory=dict)
    runs_per_period: float = 1.0         # how often the job runs in the period
    seeds: int = 200                     # runs behind the risk premium
    hidden: bool = False
    why: str = ""                        # what this scenario is here to teach

    def __post_init__(self):
        self.prices.check()


# --- the account -------------------------------------------------------

@dataclass(frozen=True)
class LineItem:
    """
    One line of the account, kept as quantity x rate rather than a total.

    `events` holds indices into the trace, which is what lets the page work
    backwards: click the line, and the sends or crashes that produced it light
    up in the diagram. A number you cannot trace to the thing that caused it
    is not an explanation.
    """

    bucket: str
    label: str
    quantity: float
    unit: str
    rate: float
    why: str = ""
    events: tuple[int, ...] = ()

    @property
    def amount(self) -> float:
        return self.quantity * self.rate

    def to_json(self) -> dict:
        return {"bucket": self.bucket, "label": self.label,
                "quantity": round(self.quantity, 3), "unit": self.unit,
                "rate": self.rate, "amount": round(self.amount, 2),
                "why": self.why, "events": list(self.events)}

    def __repr__(self):
        return (f"{self.label:<34} {self.quantity:>12,.1f} {self.unit:<14} "
                f"x {self.rate:>9,.4f} = {self.amount:>12,.2f}")


@dataclass(frozen=True)
class PnL:
    """One design, run against one scenario, as a profit-and-loss account."""

    scenario: str
    currency: str
    items: tuple[LineItem, ...] = ()

    def bucket(self, name: str) -> list[LineItem]:
        return [i for i in self.items if i.bucket == name]

    def subtotal(self, name: str) -> float:
        return sum(i.amount for i in self.bucket(name))

    @property
    def earned(self) -> float:
        return sum(self.subtotal(b) for b in EARNING)

    @property
    def spent(self) -> float:
        return sum(self.subtotal(b) for b in BUCKETS if b not in EARNING)

    @property
    def profit(self) -> float:
        return self.earned - self.spent

    def to_json(self) -> dict:
        return {"scenario": self.scenario, "currency": self.currency,
                "items": [i.to_json() for i in self.items],
                "subtotals": {b: round(self.subtotal(b), 2) for b in BUCKETS},
                "earned": round(self.earned, 2),
                "spent": round(self.spent, 2),
                "profit": round(self.profit, 2)}

    def __repr__(self):
        lines = []
        for b in BUCKETS:
            rows = self.bucket(b)
            if not rows:
                continue
            lines.append(f"\n{b.upper()}")
            lines.extend(f"  {r}" for r in rows)
            lines.append(f"  {'':<34} {'':<12} {'subtotal':<14}   "
                         f"{'':>9} = {self.subtotal(b):>12,.2f}")
        lines.append(f"\n  OPERATING PROFIT ({self.currency}) "
                     f"{self.profit:>12,.2f}")
        return "\n".join(lines)


# --- what the trace proves ---------------------------------------------

def _duration(e) -> float:
    """A work event's length, however the emitter recorded it."""
    return e.detail.get("duration", e.detail.get("until", e.t) - e.t)


def _replicates(trace: Trace) -> bool:
    """The same payload went to more than one target — a copy exists."""
    seen: dict[str, set] = {}
    for e in trace.of_kind("send"):
        seen.setdefault(repr(e.detail.get("payload")), set()).add(e.detail.get("to"))
    return any(len(targets) > 1 for targets in seen.values())


# Mechanisms whose use leaves a mark, and how to look for it.
#
# Build cost is charged on what a design *declares*, because counting machines
# cannot tell replication from partitioning. Declaring is not free: anything
# in here is checked against the trace, and a run whose evidence contradicts
# its claim is refused rather than billed. You tell the business what you
# built, and you are invoiced for it.
#
# Mechanisms absent from this table are taken on trust and still charged — a
# deadline that never fires leaves nothing behind, and pricing only what is
# observable would make caution free.
EVIDENCE: dict[str, Callable[[Trace], bool]] = {
    "retry": lambda t: bool(t.of_kind("retry")),
    "failover": lambda t: bool(t.of_kind("restart")),
    "replication": _replicates,
    "checkpoint": lambda t: any(
        "checkpoint" in str(e.detail.get("label", "")).lower()
        for e in t.of_kind("work")),
}


def detected(trace: Trace, evidence: dict | None = None) -> list[str]:
    """
    Mechanisms this run demonstrably used.

    What the editor charges for while a student types, so adding a retry makes
    a build line appear rather than waiting for a declaration. It sees only
    what leaves a mark, which is why a competition entry declares instead:
    detection alone would make an unfired safeguard free, and the whole reason
    build cost exists is to stop caution from being free.
    """
    table = {**EVIDENCE, **(evidence or {})}
    return [name for name, seen in table.items() if seen(trace)]


def unsupported(trace: Trace, declared: Iterable[str],
                evidence: dict | None = None) -> list[str]:
    """
    Declared mechanisms the trace does not back up.

    Empty means the account can be trusted. Anything else is a claim to a
    build cost for something that never happened — which, since build cost is
    the counterweight that makes a simple design defensible, is worth catching
    rather than quietly paying for.
    """
    table = {**EVIDENCE, **(evidence or {})}
    return [m for m in declared if m in table and not table[m](trace)]


# --- the arithmetic ----------------------------------------------------

def price(trace: Trace, scenario: Scenario, *,
          declared: Sequence[str] = (), evidence: dict | None = None) -> PnL:
    """
    Turn one run into a period's account.

    The simulation covers a single run; a scenario says how often that run
    happens over the period. Scaling the *scenario* rather than the prices is
    what lets the rates stay plausible — a word count over four documents
    costs a fraction of a rappen at real cloud prices, and rounding the price
    up to make the arithmetic interesting would teach something false.
    """
    missing = unsupported(trace, declared, evidence)
    if missing:
        raise ValueError(
            "the trace does not support what this design declares: "
            + ", ".join(missing)
            + ". Build cost is charged on the declaration, so a claim that "
              "never runs cannot be billed.")

    p, runs, items = scenario.prices, scenario.runs_per_period, []

    # -- build: paid once, felt every period
    for mech in declared:
        days = p.build_days.get(mech)
        if not days:
            continue
        items.append(LineItem(
            "build", f"{mech} — {days:g} engineer-days", days / p.amortise_months,
            "days/month", p.engineer_day,
            why="engineering time, spread over the life of the system"))

    # -- capacity: the fleet, whether it was busy or not, priced for what each
    # machine actually is. A fleet is rarely uniform, and three identical boxes
    # should read as one line rather than three, so machines are grouped by
    # what they cost: how fast they run and how much they can hold.
    spawns = [(i, e) for i, e in enumerate(trace) if e.kind == "spawn"]
    fleet = len(spawns) or len(trace.machines())
    # How much a machine can hold is not always known when it is created — a
    # job may hand a machine its partition, and the room it needs with it — so
    # the largest figure the machine is ever seen to carry stands as what was
    # bought for it.
    room: dict[str, int] = {}
    for e in trace.of_kind("hold", "spawn"):
        cap = e.detail.get("capacity")
        if cap and e.machine:
            room[e.machine] = max(room.get(e.machine, 0), int(cap))
    # A machine bought off the catalogue is charged what its type costs. One
    # that stated no type is charged for what it turned out to be, so a
    # program with no procurement in it still has a capacity line.
    kinds: dict[tuple, list[int]] = {}
    for i, e in spawns:
        kinds.setdefault((e.detail.get("type"),
                          float(e.detail.get("speed") or 1.0),
                          room.get(e.machine) or e.detail.get("capacity")),
                         []).append(i)
    for (kind, speed, cap), idx in sorted(kinds.items(), key=lambda k: str(k[0])):
        rate = p.machine_prices.get(kind) if kind else None
        if rate:
            spec = kind
        else:
            rate = (p.machine_hour * (speed ** p.speed_exponent)
                    + p.memory_item_hour * (cap or 0))
            spec = f"speed {speed:g}" + (f", holds {cap:,}" if cap else "")
        if not rate:
            continue
        how = f"{len(idx)} machine{'s' if len(idx) > 1 else ''}"
        items.append(LineItem(
            "capacity", f"{how} reserved — {spec}",
            len(idx) * p.period_hours, "machine-hours", rate,
            why=_why_machine(kind) if kind and rate else
                "reserved for the period, so idle costs the same as busy; "
                "a faster or roomier machine costs more every hour of it",
            events=tuple(idx)))

    # -- consumption: what the work actually burned
    late_s = max(0.0, trace.duration - p.window_seconds) if p.window_seconds else 0.0
    if late_s and p.overtime_machine_hour:
        items.append(LineItem(
            "consumption", "overtime beyond the window",
            fleet * (late_s / 3600.0) * runs, "machine-hours",
            p.overtime_machine_hour,
            why="a run that overruns its slot holds the fleet into the next one"))

    # A machine that sends to itself puts nothing on the wire. `metrics`
    # counts those, deliberately — a local shuffle is still shuffle, and the
    # skew it causes is real. Money is stricter: co-locating the map and the
    # fold that reads it removes the network hop, so it removes the charge.
    wire = [(i, e) for i, e in enumerate(trace)
            if e.kind == "send" and e.detail.get("to") != e.machine]
    if wire and p.egress_gb and p.bytes_per_message:
        gb = len(wire) * p.bytes_per_message / 1e9 * runs
        items.append(LineItem(
            "consumption", f"{len(wire):,} messages across the network", gb, "GB",
            p.egress_gb,
            why="at real prices this is usually small — a combiner pays by "
                "shortening the job, not by saving bytes",
            events=tuple(i for i, _ in wire)))

    peak = max((e.detail.get("total", 0) for e in trace.of_kind("hold")), default=0)
    if peak and p.storage_gb_month and p.bytes_per_message:
        items.append(LineItem(
            "consumption", f"intermediates held (peak {peak:,} items)",
            peak * p.bytes_per_message / 1e9, "GB-month", p.storage_gb_month,
            why="what the busiest machine had to keep at once"))

    # -- incidents: nothing until something breaks
    # A machine holding more than its capacity is out of memory. The run is
    # over: whatever it was building is gone, so the revenue below is not
    # earned either, and neither is any of it recoverable by waiting.
    oom = [e for e in trace.of_kind("hold") if e.detail.get("over_capacity")]
    if oom and p.out_of_memory:
        who = sorted({e.machine for e in oom})
        biggest = max(e.detail.get("total", 0) for e in oom)
        items.append(LineItem(
            "incidents", f"out of memory on {', '.join(who)}",
            len(who) * runs, "machines", p.out_of_memory,
            why=f"held {biggest:,} items at once, past what the machine has; "
                f"the run does not finish and the work goes somewhere it fits",
            events=tuple(i for i, e in enumerate(trace)
                         if e.kind == "hold" and e.detail.get("over_capacity"))))

    crashes = trace.of_kind("crash")
    drops = trace.of_kind("drop")
    lost = sum(len(e.detail.get("lost", [])) for e in crashes)
    crash_idx = tuple(i for i, e in enumerate(trace) if e.kind == "crash")

    if crashes and p.rerun:
        items.append(LineItem(
            "incidents", f"{len(crashes)} crash(es) — work redone",
            len(crashes) * runs, "reruns", p.rerun,
            why="a run that dies part-way is a run you pay for twice",
            events=crash_idx))
    if crashes and p.callout:
        items.append(LineItem(
            "incidents", "engineer called out", len(crashes) * runs,
            "incidents", p.callout,
            why="somebody woke up; this is what peace of mind is worth per night",
            events=crash_idx))
    if lost and p.lost_item:
        items.append(LineItem(
            "incidents", "work lost with a machine", lost * runs, "items",
            p.lost_item,
            why="a machine that forgets takes unfinished work with it",
            events=crash_idx))
    if drops and p.dropped_message:
        items.append(LineItem(
            "incidents", "messages dropped", len(drops) * runs, "messages",
            p.dropped_message,
            why="sent to a machine that was not there to receive them",
            events=tuple(i for i, e in enumerate(trace) if e.kind == "drop")))
    if late_s and p.hour_late:
        items.append(LineItem(
            "incidents", "downstream stalled", late_s / 3600.0 * runs, "hours late",
            p.hour_late,
            why="everything waiting on this result waited"))

    items.extend(_revenue(trace, scenario, late_s, failed=bool(oom)))
    return PnL(scenario=scenario.name, currency=p.currency, items=tuple(items))


def _why_machine(kind: str) -> str:
    """What this type of machine is, in the catalogue's own words."""
    from .machine_types import CATALOGUE
    entry = CATALOGUE.get(kind)
    return (f"{kind}: {entry.why}" if entry else
            "reserved for the period, so idle costs the same as busy")


def _revenue(trace: Trace, scenario: Scenario, late_s: float, *,
             failed: bool = False) -> list[LineItem]:
    """
    What the service earned, on three conditions — and each one is a property
    this course is about:

        available   it answered at all           otherwise the meter stops
        fast        inside the deadline          otherwise reduced, or nothing
        fresh       off a new enough result      otherwise refunded, plus a fine

    That is CAP as a revenue statement. Availability is not a percentage here;
    it is whether the meter was running.

    Two shapes of service, told apart by whether anyone called anything. A
    service answers requests; a batch job produces a result that a service
    then answers *from*, so a run that finishes on time unlocks a period's
    worth of requests and one that overruns serves stale ones until it lands.
    """
    p, runs, out = scenario.prices, scenario.runs_per_period, []
    calls = trace.of_kind("rpc")

    # A run that ran out of memory produced no result, so there is nothing to
    # serve from and nothing to charge for serving. Being down is not a fine —
    # it is the meter stopping, and that distinction is most of why capacity
    # is an investment rather than pure waste.
    if failed:
        if p.requests_per_run and p.per_request:
            out.append(LineItem(
                "not_earned", "nothing to serve — the run did not finish",
                p.requests_per_run * runs, "requests", p.per_request,
                why="the meter stops; a design that cannot hold the data "
                    "earns nothing from it"))
        return out

    if calls:
        ok = [(i, e) for i, e in enumerate(trace) if e.kind == "rpc"
              and e.detail.get("status") == "ok"]
        served = [(i, e) for i, e in ok
                  if not p.sla_seconds
                  or e.t - e.detail.get("started", e.t) <= p.sla_seconds]
        slow = [(i, e) for i, e in ok if (i, e) not in served]
        failed = [(i, e) for i, e in enumerate(trace) if e.kind == "rpc"
                  and e.detail.get("status") != "ok"]

        if served and p.per_request:
            out.append(LineItem(
                "revenue", "requests served inside the deadline",
                len(served) * runs, "requests", p.per_request,
                why="available, fast and correct — the only kind that pays",
                events=tuple(i for i, _ in served)))
        if slow:
            out.append(LineItem(
                "revenue", "answered, but late", len(slow) * runs, "requests",
                p.slow_request,
                why="a late answer is worth less; often it is worth nothing",
                events=tuple(i for i, _ in slow)))
        if failed and p.per_request:
            out.append(LineItem(
                "not_earned", "unavailable — meter stopped", len(failed) * runs,
                "requests", p.per_request,
                why="downtime does not cost you money, it stops you earning it",
                events=tuple(i for i, _ in failed)))
        return out

    # Batch: one result, serving a period's worth of requests behind it.
    if not p.requests_per_run or not p.per_request:
        return out
    window = p.window_seconds or p.freshness_seconds
    stale_frac = min(1.0, late_s / window) if window and late_s else 0.0
    fresh = p.requests_per_run * (1.0 - stale_frac)
    stale = p.requests_per_run * stale_frac

    if fresh:
        out.append(LineItem(
            "revenue", "answers served off a fresh result", fresh * runs,
            "requests", p.per_request,
            why="what one on-time run of this job is worth downstream"))
    if stale:
        out.append(LineItem(
            "not_earned", "answers served stale — refunded, plus penalty",
            stale * runs, "requests", p.stale_refund + p.stale_penalty,
            why="worse than no answer: you refund it and pay a fine on top"))
    return out


# --- peace of mind -----------------------------------------------------

@dataclass(frozen=True)
class RiskProfile:
    """
    The same design and the same scenario, run many times with different
    failure draws — and the gap between the month you expect and the month you
    fear.

    This is how peace of mind gets a price without anyone inventing one. You
    are billed on the **bad month, not the average month**, exactly as an
    insurer would quote you, and the difference between the two is the premium
    your architecture attracts. A student who adds replication watches
    `capacity` rise, `incidents` fall, and this number collapse — which is the
    argument for redundancy, stated in the only unit that can carry it.

    `worst` is a real run, not a statistic, so the page can link the premium
    to the crash that caused it.
    """

    scenario: str
    currency: str
    accounts: tuple[PnL, ...] = ()

    @property
    def profits(self) -> list[float]:
        return sorted(a.profit for a in self.accounts)

    @property
    def expected(self) -> float:
        """The month you would budget for."""
        return statistics.fmean(self.profits) if self.accounts else 0.0

    #: How much of the tail the bad month averages over. A tenth is the
    #: convention an insurer uses and is stable at a few hundred runs.
    TAIL = 0.10

    @property
    def bad(self) -> float:
        """
        The month you have to be able to survive: the mean of the worst tenth.

        Averaging the tail rather than reading a percentile off it is what
        makes this work for the failures this course is about, which are rare
        and expensive rather than common and mild. A design that is fine
        299 months in 300 and catastrophic in the last one has a perfectly
        healthy tenth percentile — the disaster is below it, and a percentile
        cannot see anything below itself. The mean of the worst tenth carries
        the whole tail's weight, so one ruinous month in three hundred moves
        it by a thirtieth of the ruin instead of by nothing at all.

        This is expected shortfall, which is what an insurer quotes on.
        """
        p = self.profits
        if not p:
            return 0.0
        k = max(1, int(len(p) * self.TAIL))
        return statistics.fmean(p[:k])

    @property
    def premium(self) -> float:
        """What the spread costs you. Peace of mind, in money."""
        return self.expected - self.bad

    @property
    def worst(self) -> PnL | None:
        """The actual run that stands for the bad month."""
        if not self.accounts:
            return None
        # The run that stands for the tail is the worst one in it, not the one
        # closest to its mean: a student clicking the premium is asking what
        # the bad month looks like, and the answer is the crash.
        return min(self.accounts, key=lambda a: a.profit)

    def to_json(self) -> dict:
        w = self.worst
        return {"scenario": self.scenario, "currency": self.currency,
                "runs": len(self.accounts),
                "expected": round(self.expected, 2),
                "bad": round(self.bad, 2),
                "premium": round(self.premium, 2),
                "worst_run": w.to_json() if w else None}

    def __repr__(self):
        return (f"expected {self.expected:>12,.2f}   "
                f"bad month {self.bad:>12,.2f}   "
                f"risk premium {self.premium:>12,.2f} {self.currency}")


def profile(traces: Iterable[Trace], scenario: Scenario, *,
            declared: Sequence[str] = (), evidence: dict | None = None
            ) -> RiskProfile:
    """
    Price the same design over many runs.

    The runs come from the caller, because varying the failure draw is already
    `build_cluster(..., seed=...)`'s job — the seed fixes the draws in every
    dialect, which is exactly what makes a hundred runs of one program mean
    something.
    """
    accounts = tuple(price(t, scenario, declared=declared, evidence=evidence)
                     for t in traces)
    return RiskProfile(scenario=scenario.name, currency=scenario.prices.currency,
                       accounts=accounts)


# --- the lesson --------------------------------------------------------

def crosstab(entries: dict[str, dict[str, float]]) -> dict:
    """
    Every design run against every scenario, not only the one it was built for.

    This is the page that goes up after a competition closes. A student who
    tuned four specialists sees their election-night design losing money on a
    quiet Tuesday, and the trade-off lands harder than a forced compromise
    would: they built both, so they know which decision flipped.

    `entries[design][scenario] = profit`. The diagonal is what each design was
    built for; everything off it is what that choice cost elsewhere.
    """
    designs = list(entries)
    scenarios = sorted({s for row in entries.values() for s in row})
    best = {s: max((entries[d].get(s, float("-inf")), d) for d in designs)[1]
            for s in scenarios} if designs else {}
    return {"designs": designs, "scenarios": scenarios,
            "profit": {d: {s: round(entries[d].get(s, 0.0), 2)
                           for s in scenarios} for d in designs},
            "winner": best}


def share(account: "PnL | RiskProfile") -> dict[str, float]:
    """
    Each bucket as a fraction of everything that moved.

    Authoring aid, and a blunt one on purpose. A scenario in which `capacity`
    is 0.4% of turnover contains no capacity decision: whatever the student
    provisions, the answer is the same, and the scenario teaches nothing while
    looking as though it does. Run this while setting prices, and check that
    every bucket a scenario claims to be about is actually large enough to
    change the ranking.

    Realistic prices make this easy to get wrong in one direction: against the
    revenue of a real business, infrastructure genuinely is a rounding error.
    Decisions there bite through the revenue they protect — a design is not
    punished for its machines but for the answers it failed to serve. A
    scenario meant to be about cost needs thin margins to say so.

    Given a `RiskProfile` this reads the bad month rather than a single run.
    A failure scenario sampled on one clean draw looks exactly like the quiet
    one, and reading that as "no failure decision here" is backwards.
    """
    if isinstance(account, RiskProfile):
        account = account.worst or PnL(account.scenario, account.currency)
    gross = sum(abs(i.amount) for i in account.items) or 1.0
    return {b: round(sum(abs(i.amount) for i in account.bucket(b)) / gross, 4)
            for b in BUCKETS}
