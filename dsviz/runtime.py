"""
The runtime: one program, one interpreter.

There is no MapReduce language, no RPC language and no Spark language. There is
one language — decorated classes holding typed functions — and a small table
saying what each decorator *means* to the simulator. `@machine` is one that
answers and makes calls; `@mapper` and `@reducer` are the halves of a job. Nothing about the
grammar, the checker or this interpreter changes when a new exercise is added:
only the table does.

That is what makes it one course rather than four notations sharing a toolchain.
A student who has written an RPC call has already written most of a Spark stage,
because they are the same construct pointed at a different engine.

Everything here ends in a `Trace`, which `shapes` renders and Manim draws — so a
new decorator gets a diagram for free.
"""

from __future__ import annotations

from .core import Cluster, Event
from .notation import Diagnostic, NotationError
from .syntax import LIFECYCLE, MACHINE_SETTINGS, lint


# What a decorator means. `role` is what the machine is drawn and scheduled as;
# `serves` says whether its methods answer calls from elsewhere.
MACHINE_KINDS = {
    # A machine is a machine. Whether it answers calls or makes them is decided
    # by what it does — a class with methods can be called, one that calls
    # others does that — not by which word was written above it. There is no
    # separate "client": something that only makes calls is still a machine,
    # and having both words only invited the question of which to use.
    "machine": {"role": "machine", "serves": True},
    # The MapReduce halves keep their own names, because a job has to know
    # which machines are mappers and which are reducers.
    "mapper":  {"role": "mapper", "serves": True},
    "reducer": {"role": "reducer", "serves": True},
    "process": {"role": "process", "serves": True},
}

# How long a method takes when it does not say. Half a second reads as work on
# a timeline without dominating it.
DEFAULT_DURATION = 0.5


def machines_of(mod) -> list:
    """
    Every machine the program actually makes, as (instance, class) pairs.

    A decorated class is a *kind* of machine; an instance is one that exists.
    Nothing runs because a class was declared — `server = Adder()` is what puts
    a machine on the timeline, under the name `server`.
    """
    # Only what the world contains, narrowed to the subsystems the runs
    # actually ask for. A machine that exists but was left out of the world is
    # a description of a machine, not a running one.
    #
    # Every run is consulted, not just the first. Narrowing to `mod.runs[0].on`
    # meant a program that ran one job on some machines and a second job on
    # others never created the second set at all — so the second `world.run`
    # found no executors and did nothing, silently. Which machines a job is
    # given is decided per run, in the runner; this only decides which ones are
    # built, and that is the union of everything asked for.
    chosen: list = []
    for world in mod.worlds.values():
        chosen = list(world.machines)
        break
    asked: set = set()
    for run in mod.runs:
        if not run.on:
            asked = set(chosen)       # a run with no `on` wants the whole world
            break
        asked |= set(run.on)
    if asked:
        chosen = [m for m in chosen if m in asked]

    out = []
    for name in chosen:
        inst = mod.instances.get(name)
        cls = mod.classes.get(inst.cls) if inst else None
        if inst is not None and cls is not None and cls.kind in MACHINE_KINDS:
            out.append((inst, cls))
    return out


def _duration(method) -> float:
    """`@duration(0.4)` or `@duration(seconds=0.4)`; otherwise the default."""
    takes = method.decorator("duration")
    if takes is None:
        return DEFAULT_DURATION
    positional = takes.args.get("_args") or []
    return float(takes.args.get(
        "seconds", positional[0] if positional else DEFAULT_DURATION))


def evaluate(source: str, *, runs: int = 100) -> dict:
    """
    Run a program many times and report the spread, not one story.

    A single run answers "what happened"; a hundred answer "what tends to
    happen, and how bad does it get". That is the honest question about an
    unreliable system: a design is not good because one run went well, and a
    tail that only shows up in one run out of fifty is exactly the tail worth
    knowing about.

    Every run is seeded by its index, so the *set* is reproducible even though
    the runs differ from one another. Nothing is drawn — this is the answer to
    "is this design any good", not "what does it look like".
    """
    from .metrics import measure
    from .assignment import build_cluster
    from .langserver import detect_dialect

    dialect = detect_dialect(source)
    # A program varies when something in it can break. Nothing else in here is
    # random, so a program with no error_rate anywhere replays identically, and
    # running it a hundred times would be a hundred copies of one answer
    # dressed up as evidence.
    varies = _can_fail(source)
    wanted = max(1, runs)

    samples: dict[str, list] = {}
    failures = 0
    for i in range(wanted if varies else 1):
        try:
            c = build_cluster(dialect, source, seed=i)
        except NotationError:
            raise
        except Exception:
            failures += 1
            continue
        for name, metric in measure(c.trace).items():
            samples.setdefault(name, []).append(float(metric.value))

    return {
        "runs": wanted if varies else 1,
        "asked": wanted,
        "failed": failures,
        # Said out loud, so a flat result reads as "nothing here can fail"
        # rather than as a hundred runs that happened to agree.
        "deterministic": not varies,
        "metrics": {name: _spread(values) for name, values in samples.items()},
    }


def _can_fail(source: str) -> bool:
    """
    Whether anything in this program is capable of behaving differently.

    Read off the world rather than the dialect, because the answer is the same
    question in all of them: was any machine given an error_rate. A scripted
    `crashes at 2.0` does not count — it happens on every run, so it makes no
    spread.
    """
    from .syntax import declared_machines

    return any(traits.get("error_rate", 0.0) > 0
               for _, _, traits in declared_machines(source))


def _spread(values: list) -> dict:
    """The shape of a set of results: where it sits, and how bad the tail is."""
    ordered = sorted(values)
    n = len(ordered)

    def at(q: float):
        if not n:
            return 0.0
        return ordered[min(n - 1, max(0, int(round(q * (n - 1)))))]

    mean = sum(ordered) / n if n else 0.0
    # Population spread; with a hundred runs the difference from the sample
    # form is not worth the explanation it would cost a student.
    var = sum((v - mean) ** 2 for v in ordered) / n if n else 0.0
    return {
        "n": n, "min": at(0.0), "p50": at(0.5), "p95": at(0.95), "max": at(1.0),
        "mean": mean, "stdev": var ** 0.5,
        # A coarse histogram, so the shape is visible without a plotting
        # library: ten buckets between the smallest and largest result.
        "histogram": _histogram(ordered),
    }


def _histogram(ordered: list, buckets: int = 10) -> list:
    if not ordered:
        return []
    low, high = ordered[0], ordered[-1]
    if high == low:
        return [len(ordered)] + [0] * (buckets - 1)
    counts = [0] * buckets
    width = (high - low) / buckets
    for v in ordered:
        counts[min(buckets - 1, int((v - low) / width))] += 1
    return counts


def build(source: str, *, name: str = "cluster", seed: int | None = None) -> Cluster:
    """
    Run a program and return the cluster holding its trace.

    Three passes, because a program is not required to be written in dependency
    order: declare every machine, teach each one what it answers, then perform
    the calls in the order they appear.
    """
    mod, diags = lint(source)
    errors = [d for d in diags if d.severity == "error"]
    if errors:
        raise NotationError(errors)

    c = Cluster(name, seed=seed)
    built = machines_of(mod)

    # `times` belongs to the job — it is how many rounds to run. How often a
    # machine fails does not: that is a property of the machine, declared where
    # the machine is.

    for inst, cls in built:
        kind = MACHINE_KINDS[cls.kind]
        # The instance settings win: the class may give a default speed, but
        # `slow = Worker(speed=0.3)` is the machine that actually runs. How it
        # fails travels with it the same way — how likely, and what it does
        # afterwards, are both properties of this machine.
        declared = cls.decorator(cls.kind).args
        settings = {}
        for key, (default, cast) in MACHINE_SETTINGS.items():
            settings[key] = cast(inst.settings.get(key, declared.get(key, default)))
        c.machine(inst.var, role=kind["role"], **settings)

    for inst, cls in built:
        if not MACHINE_KINDS[cls.kind]["serves"]:
            continue
        for method in cls.methods.values():
            c.machines[inst.var].serve(method.name, duration=_duration(method),
                                       handler=_handler(method))

    # Calls run in source order across the whole program, so a machine that both
    # answers and calls behaves the way it reads.
    #
    # Which clock the processes keep is the job's to say. The same chat runs
    # under either, and what separates them is exactly what the exercise is
    # about: a Lamport stamp orders everything, including events that are not
    # related, so a smaller number never proves a message came first.
    processes = [i.var for i, k in built if k.kind == "process"]
    wanted = next((str(j.settings["clock"]) for j in mod.jobs
                   if j.settings.get("clock")), "vector")
    # A job asking for causal delivery is asking for delivery to be a decision
    # rather than something that just happens on arrival.
    causal = any(str(j.settings.get("delivery", "")) == "causal"
                 for j in mod.jobs)
    if causal:
        clocks = CausalClocks(processes)
    elif wanted == "lamport":
        clocks = LamportClocks(processes)
    else:
        clocks = Clocks(processes)

    for inst, cls in built:
        for method in sorted(cls.methods.values(), key=lambda m: m.line):
            for call in method.calls:
                _perform(c, inst.var, call, clocks)

    # A job whose work is a sequence of calls. MapReduce hands the runtime three
    # functions to orchestrate; this hands it one function that is the work. The
    # caller is the job itself, so a program of plain calls needs no machine
    # invented to hold them — the shape is the same as every other job:
    # describe a world, build a job, run it.
    # A program that never runs anything is not a program that works — it is
    # one that has had its last two lines deleted. Judging it on "did it raise"
    # passed exactly that, so a submission could keep the machines, drop the
    # job and the `world.run`, and be marked as running without errors.
    if not mod.runs:
        from .notation_mr import last_line
        raise NotationError([Diagnostic(
            last_line(source), 1, "error",
            "this program never runs anything",
            hint="build a job and run it in the world, e.g. "
                 "job = Calls(run=story) and then world.run(job)")])

    for run in mod.runs:
        job = next((j for j in mod.jobs if j.var == run.job), None)
        if job is None:
            raise NotationError([Diagnostic(
                run.line, 1, "error",
                f"world.run({run.job}) was asked for, but there is no job "
                f"called {run.job!r}",
                hint=f"jobs defined here: "
                     f"{', '.join(j.var for j in mod.jobs) or 'none'}")])
        if job.kind == "Spark" and run_pipeline(mod, c, job, run):
            continue
        if "run" not in job.roles:
            continue
        fn = mod.functions.get(job.roles["run"])
        if fn is None:
            continue
        if job.var not in c.machines:
            c.machine(job.var, role="client", speed=1.0)
        rounds = max(1, int(job.settings.get("times", run.times)))
        for round_no in range(rounds):
            # Each round replaces the last on the diagram rather than being
            # added to it: the picture is of one run of the job, not of every
            # run laid end to end. What carries over is the state of the
            # world — a machine that broke in round two is still broken in
            # round three, which is the only reason running it repeatedly
            # tells you anything.
            if round_no:
                c.trace.clear()
                # What a machine is *holding* is drawn too, so it has to go
                # with the trace. Leaving it made every round pile more chips
                # into the same boxes, which is both wrong and what pushed the
                # diagram out of shape.
                for machine in c.machines.values():
                    machine.items.clear()
            env: dict = {}
            for call in fn.calls:
                _perform(c, job.var, call, clocks, env)
    if hasattr(clocks, "flush"):
        clocks.flush(c)
    return c


class Clocks:
    """
    Vector clocks kept over the machines that carry them.

    Only `@process` machines have one. The rules are the standard ones: a send
    increments the sender's own entry and carries the stamp; a receive takes
    the pointwise maximum and then increments its own. That is what makes the
    diagram readable — two events are concurrent exactly when neither stamp
    dominates the other, which is a fact about the messages, not about which
    machine happened to be faster.
    """

    def __init__(self, names: list[str]):
        self.names = names
        self.at = {n: [0] * len(names) for n in names}
        self.late: list[tuple] = []        # copies still on the wire

    def __bool__(self) -> bool:
        return bool(self.names)

    def flush(self, c: Cluster) -> None:
        """
        Deliver the copies that were held up, in the order they turn up.

        Nothing decides anything here — that is the point. A late message is
        shown when it arrives, however out of order that makes the
        conversation look, and seeing that is what motivates a delivery rule.
        """
        for to, frm, label, stamp in self.late:
            merged = [max(a, b) for a, b in zip(self.at[to], stamp)]
            merged[self.names.index(to)] += 1
            self.at[to] = merged
            c._emit(c.machines[to].clock, "clock", to,
                    clock=list(merged), label=f"recv {label} (late)")
        self.late.clear()

    def broadcast(self, c: Cluster, frm: str, label: str,
                  late: str = "") -> None:
        """
        One message to everyone else: one send, one stamp, many receipts.

        Sending to each recipient in turn would advance the sender's own entry
        once per recipient, which says three messages were sent where one was.

        `late` still delays the copy, and with no rule to hold it back the
        conversation is simply shown out of order. That is the picture the
        delivery rule exists to fix, so it has to be possible to see it.
        """
        if frm not in self.at:
            return
        self.at[frm][self.names.index(frm)] += 1
        stamp = list(self.at[frm])
        c._emit(c.machines[frm].clock, "clock", frm, clock=stamp, label=label)
        for to in self.names:
            if to == frm:
                continue
            if to == late:
                self.late.append((to, frm, label, stamp))
                continue
            merged = [max(a, b) for a, b in zip(self.at[to], stamp)]
            merged[self.names.index(to)] += 1
            self.at[to] = merged
            c._emit(c.machines[to].clock, "clock", to,
                    clock=list(merged), label=f"recv {label}")

    def send(self, c: Cluster, frm: str, to: str, label: str,
             delay: float = 0) -> None:
        # `delay` reorders arrivals, which only matters once delivery is a
        # decision. Here every message is delivered on arrival, so it is
        # accepted and ignored rather than being an error to write.
        if frm not in self.at or to not in self.at:
            return
        self.at[frm][self.names.index(frm)] += 1
        stamp = list(self.at[frm])
        c._emit(c.machines[frm].clock, "clock", frm, clock=stamp, label=label)

        merged = [max(a, b) for a, b in zip(self.at[to], stamp)]
        merged[self.names.index(to)] += 1
        self.at[to] = merged
        c._emit(c.machines[to].clock, "clock", to,
                clock=list(merged), label=f"recv {label}")


class LamportClocks:
    """
    One number per process, which is all Lamport's rule needs.

    A send increments the sender's counter and carries it; a receive takes the
    larger of the two and then increments. That gives the guarantee students
    are asked to state precisely — if a happened before b then L(a) < L(b) —
    and, just as importantly, not its converse. Two unrelated events can come
    out in either order, or equal, and the number cannot tell you which.

    The counter is emitted as a plain integer rather than a one-element list,
    because the diagram should show `3` and not `[3]`: the whole point beside a
    vector clock is that there is nothing here to compare pointwise.
    """

    def __init__(self, names: list[str]):
        self.names = names
        self.at = {n: 0 for n in names}

    def __bool__(self) -> bool:
        return bool(self.names)

    def broadcast(self, c: Cluster, frm: str, label: str,
                  late: str = "") -> None:
        """One send, one counter, and everyone else takes the larger."""
        if frm not in self.at:
            return
        self.at[frm] += 1
        stamp = self.at[frm]
        c._emit(c.machines[frm].clock, "clock", frm, clock=stamp, label=label)
        for to in self.names:
            if to == frm:
                continue
            self.at[to] = max(self.at[to], stamp) + 1
            c._emit(c.machines[to].clock, "clock", to,
                    clock=self.at[to], label=f"recv {label}")

    def send(self, c: Cluster, frm: str, to: str, label: str,
             delay: float = 0) -> None:
        if frm not in self.at or to not in self.at:
            return
        self.at[frm] += 1
        stamp = self.at[frm]
        c._emit(c.machines[frm].clock, "clock", frm, clock=stamp, label=label)

        self.at[to] = max(self.at[to], stamp) + 1
        c._emit(c.machines[to].clock, "clock", to,
                clock=self.at[to], label=f"recv {label}")


class CausalClocks(Clocks):
    """
    Vector clocks, plus the decision of whether a message may be delivered yet.

    The clock alone says what is ordered. It does not say what to *do* about a
    message that arrives before something it depends on — that is a separate
    rule, and here the student writes it:

        job = Calls(run=deliveries, delivery="causal")

    The rule is the standard one. A message from p_j carrying stamp V is
    delivered at p_i only when

        V[j] == V_i[j] + 1        it is the very next one p_j sent to me
        V[k] <= V_i[k]  for k≠j   and I have already seen everything p_j had
                                  seen when it sent

    A message the rule refuses is held, not dropped. Every time something is
    delivered the held ones are offered again, because a delivery is exactly
    the event that can make a buffered message deliverable. That loop is the
    whole mechanism.

    Arrival order is not send order: a message with a `delay` overtakes one
    sent before it. Without that there would be nothing to buffer and a rule
    that always returned true would look correct.
    """

    def __init__(self, names: list[str]):
        super().__init__(names)
        self.waiting: list[dict] = []      # arrived, not yet delivered
        self.clock_at = 0                  # how many sends have happened

    # How far behind a late copy arrives. Large enough that the messages sent
    # after it get there first, which is the whole scenario.
    LATE_BY = 10

    def broadcast(self, c: Cluster, frm: str, label: str,
                  late: str = "") -> None:
        """
        One send, one stamp, and a copy on its way to every other process.

        The stamp is taken once. Every recipient is looking at the same
        message, so a per-recipient stamp would make "the next message p_j
        sent" mean something different at each of them.
        """
        if frm not in self.at:
            return
        self.clock_at += 1
        self.at[frm][self.names.index(frm)] += 1
        stamp = list(self.at[frm])
        c._emit(c.machines[frm].clock, "clock", frm, clock=stamp, label=label)

        for name in self.names:
            if name == frm:
                continue
            self.waiting.append({
                "to": name, "frm": frm, "label": label, "stamp": stamp,
                "arrives": self.clock_at + (self.LATE_BY if name == late else 0),
            })
        self._drain(c)

    def send(self, c: Cluster, frm: str, to: str, label: str,
             delay: float = 0) -> None:
        """
        A message to one process.

        The delivery rule is stated for broadcast, so a point-to-point message
        under it would be held forever waiting for copies that were never sent.
        This says so rather than letting the diagram fill with messages nobody
        can explain.
        """
        c.note(f"{frm} sent {label!r} to {to} alone, but this job asks for "
               f"causal delivery, which is defined over broadcast. Use "
               f"{frm}.broadcast({label!r}) instead.")

    def _deliverable(self, msg) -> bool:
        """Whether this message's dependencies have all been delivered here."""
        mine = self.at[msg["to"]]
        sender = self.names.index(msg["frm"])
        stamp = msg["stamp"]
        if stamp[sender] != mine[sender] + 1:
            return False
        return all(stamp[k] <= mine[k]
                   for k in range(len(mine)) if k != sender)

    def _drain(self, c: Cluster) -> None:
        """Deliver whatever the rule now allows, then ask again."""
        progress = True
        while progress:
            progress = False
            for msg in sorted(self.waiting, key=lambda m: m["arrives"]):
                if msg["arrives"] > self.clock_at:
                    continue                       # has not got there yet
                if not self._deliverable(msg):
                    # Said once, when it first arrives and cannot be taken.
                    # This is the event the exercise is about: the message is
                    # here, it is readable, and it is deliberately not shown
                    # yet because showing it would put it out of order.
                    if not msg.get("held"):
                        msg["held"] = True
                        c.note(f"{msg['to']} is holding {msg['label']!r} from "
                               f"{msg['frm']} — it arrived before something "
                               f"it depends on")
                    continue
                self.waiting.remove(msg)
                to = msg["to"]
                # No increment of the receiver's own entry. Under causal
                # broadcast a counter records how many messages that process
                # has *sent*, and delivering someone else's message is not
                # one of them. Incrementing here made a process's own entry
                # run ahead of what it had sent, and the next broadcast then
                # looked like it had skipped one.
                merged = [max(a, b)
                          for a, b in zip(self.at[to], msg["stamp"])]
                self.at[to] = merged
                c._emit(c.machines[to].clock, "clock", to,
                        clock=list(merged), label=f"deliver {msg['label']}")
                progress = True
                break

    def flush(self, c: Cluster) -> None:
        """
        Let the late copies land, then say what is still being held.

        The run ends when the last line does, but a message that was delayed
        is still on its way. Without letting it arrive, a correctly buffered
        message would be reported as stuck forever — which is the opposite of
        what the exercise is showing.
        """
        if self.waiting:
            self.clock_at = max(m["arrives"] for m in self.waiting)
            self._drain(c)
        for msg in sorted(self.waiting, key=lambda m: m["arrives"]):
            c.note(f"{msg['to']} is still holding {msg['label']!r} from "
                   f"{msg['frm']} — nothing arrived that would release it")


# How many of a result's records are reported. A pipeline may end with more
# rows than anyone wants listed under a diagram; the count is on the RDD node.
OUTPUT_LIMIT = 40


def run_pipeline(mod, c: Cluster, job, run) -> bool:
    """
    Run an RDD pipeline across the world's machines.

    The pipeline is evaluated on real records before anything is drawn, so the
    picture is of what the program computed rather than of its shape. This used
    to build a lineage graph and time it without ever applying an operation,
    which is why a pipeline of invented names — `frobnicate`, `wibble` — drew a
    clean diagram and scored full marks.

    The executors are the machines the world was given, all of them. Setting up
    a hundred machines runs the job on a hundred.
    """
    from . import pyspark

    if not mod.rdds:
        return False

    workers = list(run.on) if getattr(run, "on", None) else list(c.machines)
    executors = [c.machines[n] for n in workers if n in c.machines]
    if not executors:
        return False

    pipe = pyspark.build(mod.rdds, mod.inputs)
    for warning in pipe.warnings:
        # Said out loud on the diagram too, not only in the editor's margin:
        # a reducer that is not associative is a property of the run, and the
        # run is what the picture is of.
        c.note(warning.message)
    lose = job.settings.get("lose") or job.roles.get("lose") or ""
    pyspark.simulate(pipe, c, executors, lose=str(lose))

    for step in pipe.named_steps():
        c.trace.append(Event(t=0.0, kind="rdd", machine=None, detail={
            "name": step.name, "op": step.op, "stage": step.stage,
            "wide": step.wide, "records": len(step.data),
            "parents": list(step.parents)}))

    # What the job produced, so the editor can show it and the grader can
    # check it. Without this a pipeline ran, drew a diagram and reported
    # nothing at all: a student could not read their own answer off the page.
    final = pipe.by_name(str(job.settings.get("pipeline", ""))) \
        or (pipe.named_steps() or [None])[-1]
    if final is not None:
        for record in final.data[:OUTPUT_LIMIT]:
            if isinstance(record, tuple) and len(record) == 2:
                key, value = record
            else:
                continue
            c.trace.append(Event(t=0.0, kind="output", machine=None,
                                 detail={"key": str(key), "value": value}))
    c.pipeline = pipe
    return True


def _handler(method):
    """
    Run a service method's own body to produce the reply.

    Declaring how slow a machine is says nothing about what it answers, so a
    diagram built from declarations alone can only label the wire with the
    method's name. Running the body means the value on the wire is the one the
    student's code computed — change `return amount * 2` and the picture
    changes with it.
    """
    from .expr import Budget, run_function

    if not method.body:
        return None

    def handle(payload):
        args = dict(zip(method.params, [payload]))
        try:
            return run_function(method, args, Budget())
        except Exception:
            # A body that cannot run must not take the simulation down with
            # it; the call still happened, it simply has nothing to report.
            return None
    return handle


def _perform(c: Cluster, caller: str, call, clocks=None, env=None) -> None:
    """One call: a lifecycle event on a machine, or a request to another."""
    target = c.machines.get(call.target)
    if target is None:
        raise NotationError([Diagnostic(
            call.line, 1, "error",
            f"there is no machine called {call.target!r}",
            hint="a machine is a class with a decorator above it")])

    # `p1.send(p2, "m1")` — a message from one machine to another. The sender
    # is the machine the call names, not whatever function the line sits in,
    # because a message is between two processes and both have to be said out
    # loud for the ordering to mean anything.
    if call.method == "send" and call.args:
        receiver = c.machines.get(str(call.args[0]))
        if receiver is None:
            raise NotationError([Diagnostic(
                call.line, 1, "error",
                f"there is no machine called {call.args[0]!r} to send to")])
        label = str(call.args[1]) if len(call.args) > 1 else "message"
        target.send(receiver, label)
        if clocks:
            # `delay` is what lets a message overtake one sent before it.
            # Without it every arrival is in send order and there is never
            # anything to buffer, so the rule would never be exercised.
            clocks.send(c, call.target, str(call.args[0]), label,
                        delay=float(call.options.get("delay", 0)))
        return

    # `p1.broadcast("hello")` — one message to everyone else, which is what
    # the chat is: the server relays each line to every client. The causal
    # delivery rule is stated for broadcast and means nothing without it —
    # "the next message p_j sent" is only well defined if p_j sends to all.
    if call.method == "broadcast" and call.args:
        label = str(call.args[0])
        late = call.options.get("late")
        if clocks:
            clocks.broadcast(c, call.target, label,
                             late=str(late) if late is not None else "")
        return

    if call.method in LIFECYCLE:
        # It happens where the story says it happens. Left to itself the
        # machine would stamp this on its own clock, and a crashed machine's
        # clock is frozen — so `bank.restart()` written after two calls landed
        # back at the start of the run, before the calls it was meant to
        # rescue. The caller has been through those calls, so its clock is
        # where "now" is.
        at = call.options.get("at")
        if at is None and caller in c.machines:
            at = c.machines[caller].clock
        getattr(target, call.method)(at=float(at) if at is not None else None)
        return

    env = env if env is not None else {}
    # An argument may be a value the previous call returned, so a bare name is
    # looked up before it is sent. Otherwise the wire would carry the word
    # `chf` rather than the number the student's code actually produced.
    args = [env.get(a, a) if isinstance(a, str) else a for a in call.args]

    rpc = c.machines[caller].call(
        target, call.method,
        args[0] if args else None,
        deadline=(float(call.options["deadline"])
                  if "deadline" in call.options else None),
        retries=int(call.options.get("retries", 0)))

    if call.bind:
        env[call.bind] = rpc.reply

    # A message between processes also advances their clocks.
    if clocks:
        clocks.send(c, caller, call.target, call.method)
