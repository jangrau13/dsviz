"""
The simulation core: machines, messages, time, failure.

Built on SimPy — it owns the event queue, the clock, and message stores, so we
don't hand-roll a discrete-event simulator. What this module adds is a fluent,
non-generator API: SimPy's native style needs `yield` inside generator
functions, which is the right engine but an awkward surface for authoring a
lecture scene.

Nothing here knows about rendering. The output is a `Trace` of plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import random

import simpy


# --- events ------------------------------------------------------------
# One record per thing that happened. Renderers match on `kind`, so adding an
# event type never requires touching the simulator.

@dataclass
class Event:
    t: float          # simulated seconds
    kind: str
    machine: str | None = None
    detail: dict = field(default_factory=dict)

    def __repr__(self):  # keeps trace dumps readable in a REPL
        bits = " ".join(f"{k}={v!r}" for k, v in self.detail.items())
        return f"[{self.t:6.2f}] {self.kind:<9} {self.machine or '':<12} {bits}"


class Trace(list):
    """An ordered list of Events, with conveniences for renderers."""

    @property
    def duration(self) -> float:
        return max((e.t for e in self), default=0.0)

    def of_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self if e.kind in kinds]

    def machines(self) -> list[str]:
        """Machine names in first-appearance order."""
        seen = {}
        for e in self:
            if e.machine is not None:
                seen.setdefault(e.machine, None)
        return list(seen)

    def to_dicts(self) -> list[dict]:
        """JSON-friendly form, so a trace can cross a language boundary."""
        return [{"t": e.t, "kind": e.kind, "machine": e.machine, **e.detail}
                for e in self]


# --- RPC ---------------------------------------------------------------

@dataclass
class Rpc:
    """
    The outcome of one RPC.

    `status` uses gRPC's vocabulary so the names carry over to the assignment:
    ok, unavailable (server down), unimplemented (no such method),
    deadline_exceeded (caller gave up).
    """
    method: str
    target: str
    status: str
    started: float
    done_at: float
    attempts: int = 1
    # What the handler returned, when the service has one. None both when the
    # call failed and when the service was declared for its timing alone, so
    # read `status` to tell those apart.
    reply: Any = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def elapsed(self) -> float:
        return self.done_at - self.started

    def __repr__(self):
        return (f"<rpc {self.method}->{self.target} {self.status} "
                f"{self.elapsed:.2f}s attempts={self.attempts}>")


# --- machines ----------------------------------------------------------

class Machine:
    """
    One simulated node.

    Properties drive both the simulation and the picture:

      speed     1.0 is nominal; 0.5 takes twice as long (stragglers)
      capacity  items it can hold before it is visibly swamped (skew)
      role      free-form label, used to group machines when rendering

    Two more say how it fails. `error_rate` is how likely it is to break; but
    a system is not described by how often it breaks alone — what happens next
    matters at least as much, and it is a property of the machine, not of the
    accident. A machine that stays down and one that comes back in two seconds
    fail at the same rate and behave nothing alike, so each machine says which
    it is: `on_crash` is "stay_dead" or "restart", and a restarting machine is
    back `restart_after` seconds later, without what it was holding.

    Each machine owns a SimPy `Store` as its inbox, so message delivery is
    SimPy's job rather than ours.
    """

    def __init__(self, cluster: Cluster, name: str, *, speed: float = 1.0,
                 capacity: int | None = None, role: str | None = None,
                 error_rate: float = 0.0, on_crash: str = "stay_dead",
                 restart_after: float = 1.0, **props):
        self.cluster = cluster
        self.name = name
        self.speed = speed
        # How likely this machine is to break doing one piece of work.
        # Applied at random (see `breaks_now`), so two runs of the same
        # program differ — which is the only honest way to show an unreliable
        # system.
        self.error_rate = error_rate
        self.on_crash = on_crash
        self.restart_after = restart_after
        # When a restarting machine is due back. None while it is up, and it
        # stays None for a machine that declared it stays dead.
        self.down_until: float | None = None
        self.capacity = capacity
        self.role = role
        self.props = props

        self.clock = 0.0        # this machine's local time
        self.items: list = []   # what it currently holds
        self.alive = True
        self.inbox = simpy.Store(cluster.env)
        self.services: dict[str, float] = {}   # method -> service time
        # method -> callable(payload) producing the reply. Optional: a service
        # may be declared for its timing alone, which is all the earliest
        # exercises need.
        self.handlers: dict[str, Any] = {}

        cluster._emit(0.0, "spawn", name,
                      speed=speed, capacity=capacity, role=role,
                      error_rate=error_rate, on_crash=on_crash, **props)

    # -- local work ----------------------------------------------------
    def work(self, label: str, duration: float = 1.0) -> Machine:
        """
        Do something that takes time. Slow machines take proportionally longer.

        This is also where an unreliable machine breaks. Doing work is the
        opportunity to fail — a mapper grinding through its split is exposed
        exactly as a service answering a request is — so the caller must look
        at `alive` afterwards rather than assume the work happened.
        """
        self._require_alive()
        if self.breaks_now():
            # It breaks setting about the job, so the work does not happen and
            # the clock does not advance: there is nothing to show for it.
            self.crash(at=self.clock)
            return self
        start = self.clock
        self.clock += duration / self.speed
        self.cluster._emit(start, "work", self.name,
                           label=label, until=self.clock, duration=self.clock - start)
        return self

    def hold(self, *items: Any) -> Machine:
        """Take custody of items (map output, a partition, a key).

        Anything already in flight to this machine lands first, so `total` and
        the capacity check reflect everything it actually holds.
        """
        self._require_alive()
        self.cluster.env.run()
        self.items.extend(items)
        self.cluster._emit(self.clock, "hold", self.name,
                           items=list(items), total=len(self.items),
                           over_capacity=self.is_overloaded)
        return self

    @property
    def is_overloaded(self) -> bool:
        return self.capacity is not None and len(self.items) > self.capacity

    # -- communication -------------------------------------------------
    def send(self, target: Machine, payload: Any, *, latency: float = 0.5) -> Machine:
        """Ship a payload to another machine, arriving `latency` later."""
        self._require_alive()
        if not target.up_at(self.clock):
            self.cluster._emit(self.clock, "drop", self.name,
                               to=target.name, payload=payload, reason="target down")
            return self

        depart, arrive = self.clock, self.clock + latency
        self.cluster._emit(depart, "send", self.name,
                           to=target.name, payload=payload, arrive=arrive)
        # Delivery is a SimPy process so arrivals sit on the shared event
        # queue. The delay is measured from `arrive` relative to the env's
        # current time — machines run at their own local clocks, so the env
        # may already be past this sender's `depart`.
        self.cluster.env.process(self._deliver(target, payload, arrive))
        return self

    def _deliver(self, target: Machine, payload: Any, arrive: float):
        yield self.cluster.env.timeout(max(0.0, arrive - self.cluster.env.now))
        if not target.up_at(arrive):               # crashed while in flight
            self.cluster._emit(arrive, "drop", self.name,
                               to=target.name, payload=payload, reason="in flight")
            return
        yield target.inbox.put(payload)
        target.clock = max(target.clock, arrive)
        target.items.append(payload)
        self.cluster._emit(arrive, "recv", target.name,
                           frm=self.name, payload=payload)

    # -- RPC -----------------------------------------------------------
    def serve(self, method: str, *, duration: float = 0.5,
              handler: Any = None) -> Machine:
        """
        Declare that this machine handles `method`. Mirrors a gRPC service.

        `duration` is how long the work takes, which is what the timeline
        draws. `handler` is optional and, when given, is called to produce the
        reply — that is what lets a student write the body of a service rather
        than only declaring how slow it is. Timing and result are deliberately
        separate: a handler that returns instantly still costs `duration` in
        the simulation, so the picture does not change when the body does.
        """
        self._require_alive()
        self.services[method] = duration
        if handler is not None:
            self.handlers[method] = handler
        self.cluster._emit(self.clock, "serve", self.name,
                           method=method, duration=duration,
                           implemented=handler is not None)
        return self

    def call(self, target: Machine, method: str, payload: Any = None, *,
             latency: float = 0.3, deadline: float | None = None,
             retries: int = 0) -> "Rpc":
        """
        Make an RPC and wait for the reply.

        Unlike `send`, a call is synchronous from the caller's point of view:
        this machine's clock advances past the round trip. That is what makes
        a slow or dead server visible as caller idle time rather than as
        something that quietly vanished.

        Returns an `Rpc` recording what happened — ok, timeout or unavailable.
        """
        self._require_alive()
        attempts = 0
        while True:
            attempts += 1
            started = self.clock
            rpc = self._attempt(target, method, payload, latency, deadline, started)
            if rpc.ok or attempts > retries:
                rpc.attempts = attempts
                # The reply travels with the event so a diagram can show what
                # the call actually answered, not merely that it answered.
                self.cluster._emit(rpc.done_at, "rpc", self.name,
                                   to=target.name, method=method,
                                   status=rpc.status, attempts=attempts,
                                   started=started, payload=payload,
                                   reply=rpc.reply)
                self.clock = rpc.done_at
                return rpc
            # Failed but retries remain: back off and try again.
            self.clock = rpc.done_at + latency
            self.cluster._emit(self.clock, "retry", self.name,
                               to=target.name, method=method,
                               attempt=attempts, reason=rpc.status)

    def breaks_now(self) -> bool:
        """
        Whether this piece of work is the one that breaks the machine.

        Genuinely random. A failure rate that produced the same run every time
        would not be a failure rate — it would be a script, and a student could
        learn the one run instead of the behaviour. Unreliability is only worth
        modelling if you have to run it again to see what it does.

        `error_rate` is how likely this machine is to break, so it does not
        fail one piece of work and carry on: it goes down and loses what it
        held. That is the distinction the exercise is about — a slow machine
        still answers, a broken one does not, and only one of them is fixed by
        waiting.

        It asks the machine doing the work, not the caller. A mapper chewing
        through its split is running the same risk as a service answering a
        request, and the earlier version — which only drew inside the RPC round
        trip — meant a MapReduce or Spark program could not fail at all however
        it was written.
        """
        if self.error_rate <= 0 or not self.alive:
            return False
        # The cluster's own generator, not the module-level one: a seeded
        # cluster has to replay exactly, or "run it a hundred times" would
        # produce a spread nobody could ever get back.
        return self.cluster.rng.random() < self.error_rate

    def _attempt(self, target, method, payload, latency, deadline, started):
        if not target.up_at(started):
            # No round trip happens; the caller learns immediately.
            return Rpc(method, target.name, "unavailable", started, started + latency)

        if method not in target.services:
            return Rpc(method, target.name, "unimplemented", started, started + latency)

        if target.breaks_now():
            # It does not merely refuse this request: it breaks. Retrying will
            # keep finding it down until it restarts — by itself if it said it
            # would, and otherwise not at all.
            target.crash(at=started)
            return Rpc(method, target.name, "unavailable",
                       started, started + latency)

        service_time = target.services[method] / target.speed
        done = started + latency + service_time + latency      # there, work, back

        if deadline is not None and (done - started) > deadline:
            # The server may still be working, but the caller has given up.
            return Rpc(method, target.name, "deadline_exceeded",
                       started, started + deadline)

        self.cluster._emit(started, "send", self.name,
                           to=target.name, payload=payload if payload is not None
                           else f"{method}()",
                           arrive=started + latency)
        target.clock = max(target.clock, started + latency)
        target.work(f"{method}()", duration=target.services[method])

        # The body runs only once the call has survived every failure check
        # above, so a student's handler never executes for a call the caller
        # has already given up on. It runs before the reply is drawn so that
        # the wire carries the value the student's own code produced — change
        # the function, and the diagram changes with it.
        reply = None
        handler = target.handlers.get(method)
        if handler is not None:
            reply = handler(payload)

        self.cluster._emit(done - latency, "send", target.name,
                           to=self.name,
                           payload=reply if reply is not None else f"{method}→reply",
                           arrive=done, reply=True)
        return Rpc(method, target.name, "ok", started, done, reply=reply)

    # -- failure -------------------------------------------------------
    def crash(self, *, at: float | None = None, lose_state: bool = True) -> Machine:
        """Take the machine down. In-memory state is lost unless told otherwise."""
        t = self.clock if at is None else at
        self.cluster._run_to(t)
        self.clock = max(self.clock, t)
        self.alive = False
        # What it said it would do about this. A machine that restarts is not
        # back yet — it is back later, and the gap is the part worth seeing.
        self.down_until = t + self.restart_after if self.on_crash == "restart" else None
        lost = list(self.items) if lose_state else []
        if lose_state:
            self.items.clear()
        self.cluster._emit(t, "crash", self.name, lost=lost,
                           on_crash=self.on_crash, back_at=self.down_until)
        return self

    def restart(self, *, at: float | None = None) -> Machine:
        t = self.clock if at is None else at
        self.cluster._run_to(t)
        self.clock = max(self.clock, t)
        self.alive = True
        self.down_until = None
        self.cluster._emit(t, "restart", self.name)
        return self

    def up_at(self, t: float) -> bool:
        """
        Whether this machine is answering at time `t`, restarting it if due.

        A machine that declared `on_crash="restart"` is down for a while, not
        forever, and nothing observes the moment it comes back — only the next
        thing that tries it. So the comeback is recorded lazily here, stamped
        at the time it was actually due rather than the time somebody noticed.
        """
        if self.alive:
            return True
        if self.down_until is None or t < self.down_until:
            return False
        self.restart(at=self.down_until)
        return True

    def _require_alive(self):
        """Make sure this machine can work now, waiting out a restart if it declared one."""
        if self.alive:
            return
        if self.down_until is not None:
            # It comes back on its own; it just cannot do anything until then.
            # Its own clock jumps to the comeback, so the downtime reads as a
            # gap in its timeline rather than as work it never stopped doing.
            self.restart(at=max(self.clock, self.down_until))
            return
        raise RuntimeError(f"{self.name} is down — restart() it before more work")

    def __repr__(self):
        return f"<Machine {self.name} t={self.clock:.2f} items={len(self.items)}>"


# --- cluster -----------------------------------------------------------

class Cluster:
    """A set of machines and the trace of what they did."""

    def __init__(self, name: str = "cluster", *, seed: int | None = None):
        self.name = name
        # Failure is genuinely random. A system that breaks one time in four
        # does not break on every fourth request — pretending otherwise would
        # teach a pattern that does not exist, and would make one run look like
        # the answer when it is only one sample. A program is reproducible
        # exactly when nothing in it can fail; give a seed to reproduce a
        # particular run, which is what running a hundred of them relies on.
        self.seed = seed
        self.rng = random.Random(seed)
        self.env = simpy.Environment()
        self.machines: dict[str, Machine] = {}
        self.trace = Trace()

    def machine(self, name: str, **props) -> Machine:
        if name in self.machines:
            raise ValueError(f"machine {name!r} already exists")
        m = Machine(self, name, **props)
        self.machines[name] = m
        return m

    def machines_of(self, role: str) -> list[Machine]:
        return [m for m in self.machines.values() if m.role == role]

    def barrier(self, label: str = "") -> float:
        """Bring every machine to the latest clock — a phase boundary.

        Everything still in flight lands first: a barrier means the previous
        phase is complete, so pending deliveries must be applied before any
        machine reads its own state.
        """
        self.env.run()                      # flush every pending delivery
        t = max(self.now(), self.env.now)
        for m in self.machines.values():
            m.clock = t
        self._emit(t, "barrier", None, label=label)
        return t

    def note(self, text: str, *, at: float | None = None):
        """A caption to show at this point. Purely for the rendering."""
        self._emit(self.now() if at is None else at, "note", None, text=text)

    def now(self) -> float:
        return max((m.clock for m in self.machines.values()), default=0.0)

    def _run_to(self, t: float):
        """Advance SimPy's queue so everything scheduled up to and including
        `t` has fired.

        `env.run(until=t)` stops *before* processing events timed exactly at
        `t`, which would leave a delivery pending and let the next call read
        stale machine state. Running fractionally past `t` settles them.
        """
        if t >= self.env.now:
            self.env.run(until=t + 1e-9)

    def _emit(self, t: float, kind: str, machine: str | None, **detail):
        self.trace.append(Event(t=t, kind=kind, machine=machine, detail=detail))

    def sorted_trace(self) -> Trace:
        """The trace in time order, with any pending events flushed first."""
        self.env.run()
        return Trace(sorted(self.trace, key=lambda e: e.t))
