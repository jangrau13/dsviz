"""
Two things happening at once, and the one place they cannot.

`with parallel():` is the only construct in the language where the ordinary
reading of a function — this line, then the next — does not hold. What makes
it worth having is not that it is faster: it is that it is faster only when
the calls go to different machines, because a machine answers one request at
a time. Both halves are asserted here, because a block that sped up three
calls to one machine would be teaching something false about what a machine
is.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.core import Cluster
from dsviz.runtime import build
from dsviz.syntax import lint

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


# --- the engine ---------------------------------------------------------

def world(names, duration=0.4, speed=1.0):
    c = Cluster("t")
    client = c.machine("client", role="client")
    for n in names:
        c.machine(n, speed=speed).serve("ping", duration=duration)
    return c, client


c, client = world(["a", "b", "d"])
for n in ("a", "b", "d"):
    client.call(c.machines[n], "ping", "hi")
ok("calls written one under the other are one after the other",
   round(client.clock, 2) == 3.0, f"client clock {client.clock}")

c, client = world(["a", "b", "d"])
with client.parallel():
    times = [client.call(c.machines[n], "ping", "hi") for n in ("a", "b", "d")]
ok("everything in a block leaves at the moment the block began",
   {round(r.started, 2) for r in times} == {0.0},
   str([r.started for r in times]))
ok("the block ends at the last reply, not the first",
   round(client.clock, 2) == 1.0, f"client clock {client.clock}")

c, client = world(["a"])
with client.parallel():
    queued = [client.call(c.machines["a"], "ping", "hi") for _ in range(3)]
ok("one machine answers one request at a time, so three of them queue",
   [round(r.done_at, 2) for r in queued] == [1.0, 1.4, 1.8],
   str([r.done_at for r in queued]))

# The saving on one machine is the wire and nothing else: three requests
# travel at once, and the work still happens one after the other.
c, alone = world(["a"])
for _ in range(3):
    alone.call(c.machines["a"], "ping", "hi")
ok("asking one machine three times at once saves the wire, not the work",
   round(alone.clock, 2) == 3.0 and round(client.clock, 2) == 1.8,
   f"one after another {alone.clock}, together {client.clock}")

# A caller that queued behind somebody else is told how long it waited, so a
# diagram can show a wait that is nobody's slowness.
c = Cluster("q")
server = c.machine("server").serve("ping", duration=0.4)
first, second = c.machine("first"), c.machine("second")
first.call(server, "ping", "1")
second.call(server, "ping", "2")
queues = [e.detail.get("queued", 0) for e in c.trace if e.kind == "send"
          and e.machine == "second"]
ok("a caller that waited behind another is told it queued",
   any(q > 0 for q in queues), str(queues))


# --- the language -------------------------------------------------------

HEAD = """
@machine
class Node:
    @duration(0.4)
    def ping(word: string) -> string:
        return word

alice = Node(speed=1.0)
bob = Node(speed=1.0)
carol = Node(speed=1.0)
world = World(machines=[alice, bob, carol])
"""
TAIL = "\njob = Calls(run=story)\nworld.run(job)\n"


def duration(body):
    return build(HEAD + body + TAIL, seed=1).sorted_trace().duration


def messages(src):
    return [(d.severity, d.message) for d in lint(HEAD + src + TAIL)[1]]


one_at_a_time = duration("""
def story() -> void:
    alice.ping("hello")
    bob.ping("and you")
    carol.ping("me too")
""")
together = duration("""
def story() -> void:
    with parallel():
        alice.ping("hello")
        bob.ping("and you")
        carol.ping("me too")
""")
ok("three machines asked together cost one round trip, not three",
   round(one_at_a_time, 2) == 3.0 and round(together, 2) == 1.0,
   f"{one_at_a_time} against {together}")

after = duration("""
def story() -> void:
    with parallel():
        alice.ping("hello")
        bob.ping("and you")
    carol.ping("afterwards")
""")
ok("the line after the block starts when the block is over",
   round(after, 2) == 2.0, str(after))

ok("a program with no block is unchanged", round(one_at_a_time, 2) == 3.0)


# --- what the checker refuses -------------------------------------------

errors = messages("""
def story() -> void:
    with parallel():
        one: string = alice.ping("hello")
        bob.ping(one)
""")
ok("a call in the block cannot be given another call's answer",
   any(sev == "error" and "answered yet" in m for sev, m in errors), str(errors))

errors = messages("""
def story() -> void:
    with parallel():
        alice.ping("hello")
        bob.ping("chf")
""")
ok("a string that merely looks like a bound name is not that mistake",
   not [m for sev, m in errors if sev == "error"], str(errors))

errors = messages("""
def story() -> void:
    with together():
        alice.ping("hello")
        bob.ping("hi")
""")
ok("there is no other block to write",
   any(sev == "error" and "together()" in m for sev, m in errors), str(errors))

errors = messages("""
def story() -> void:
    with parallel():
        alice.ping("hello")
        with parallel():
            bob.ping("hi")
            carol.ping("hi")
""")
ok("a block inside a block is refused",
   any(sev == "error" for sev, _ in errors), str(errors))

errors = messages("""
def story() -> void:
    with parallel():
        alice.ping("hello")
""")
ok("a block with one call in it is a warning, not an error",
   any(sev == "warning" for sev, _ in errors)
   and not any(sev == "error" for sev, _ in errors), str(errors))

print("\nALL PARALLEL TESTS PASSED" if not failures
      else f"\n{len(failures)} FAILED: {', '.join(failures)}")
raise SystemExit(1 if failures else 0)
