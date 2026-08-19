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

from .core import Cluster
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
    # Only what the world contains, narrowed to whatever subsystem a run was
    # given. A machine that exists but was left out of the world is a
    # description of a machine, not a running one.
    chosen: list = []
    for world in mod.worlds.values():
        chosen = list(world.machines)
        break
    for run in mod.runs:
        if run.on:
            chosen = [m for m in chosen if m in run.on]
        break

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
    clocks = Clocks([i.var for i, k in built if k.kind == "process"])

    for inst, cls in built:
        for method in sorted(cls.methods.values(), key=lambda m: m.line):
            for call in method.calls:
                _perform(c, inst.var, call, clocks)

    # A job whose work is a sequence of calls. MapReduce hands the runtime three
    # functions to orchestrate; this hands it one function that is the work. The
    # caller is the job itself, so a program of plain calls needs no machine
    # invented to hold them — the shape is the same as every other job:
    # describe a world, build a job, run it.
    for run in mod.runs:
        job = next((j for j in mod.jobs if j.var == run.job), None)
        if job is None:
            continue
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

    def __bool__(self) -> bool:
        return bool(self.names)

    def send(self, c: Cluster, frm: str, to: str, label: str) -> None:
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


def run_pipeline(mod, c: Cluster, job, run) -> bool:
    """
    Run an RDD pipeline across the world's machines.

    The assignments in the program are the lineage, so losing a step means
    recomputing it from its ancestors rather than reloading it — which is the
    one thing Spark does that MapReduce cannot, and the reason the graph is
    worth writing down at all.
    """
    from .patterns import Lineage

    if not mod.rdds:
        return False

    lineage = Lineage()
    for rdd in mod.rdds:
        lineage.rdd(rdd.var, parents=rdd.parents, op=rdd.op)

    workers = [name for name in c.machines]
    if not workers:
        return False

    lost = job.settings.get("lose")
    rebuild = set(lineage.recompute_set(str(lost))) if lost else set()

    def live() -> list:
        """The executors still answering, in declaration order."""
        return [c.machines[n] for n in workers if c.machines[n].up_at(c.machines[n].clock)]

    STEP = 0.6

    for level, stage in enumerate(lineage.stages()):
        for node in stage:
            # Each step is split across the executors and the pieces run at
            # the same time, which is what makes a slow executor show up as a
            # straggler. Assigning whole steps put a linear pipeline entirely
            # on the first machine, so a three-executor world drew as one.
            crew = live()
            if not crew:
                c.note(f"every executor is down — {node} cannot be computed")
                break
            share = STEP / len(crew)
            label = f"{node} ({lineage.g.nodes[node].get('op', '')})"
            for machine in crew:
                machine.work(label, duration=share)
                if not machine.alive:
                    # It was holding a piece of this step. The lineage says how
                    # to make that piece again, which is the whole argument for
                    # writing the lineage down — so it joins the rebuild list
                    # instead of ending the job.
                    rebuild |= set(lineage.recompute_set(node))
                    c.note(f"{machine.name} died on {node} — its partition "
                           f"goes back through the lineage")
        c.barrier(f"end of stage {level + 1}")

    for node in sorted(rebuild):
        crew = live()
        if not crew:
            c.note(f"nothing left alive to recompute {node} on")
            break
        # Recomputation does not need the machine that died, only its
        # ancestors — so it goes to whoever is up.
        crew[0].work(f"recompute {node}", duration=STEP)
    if lost:
        c.note(f"{lost} was lost — rebuilt from its lineage, not from disk")
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
            clocks.send(c, call.target, str(call.args[0]), label)
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
