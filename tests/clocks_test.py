"""
Logical clocks: what each one can say, and what it cannot.

Three things are checked, because they are three different claims the course
makes and a student can be shown a diagram that quietly contradicts any of
them.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsviz.assignment import ASSIGNMENTS, build_cluster                 # noqa: E402

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def stamps(src, machine=None):
    c = build_cluster("rpc", src)
    return [(e.machine, e.detail["clock"], e.detail["label"])
            for e in c.trace
            if e.kind == "clock" and (machine is None or e.machine == machine)]


def notes(src):
    return [e.detail.get("text", "") for e in build_cluster("rpc", src).trace
            if e.kind == "note"]


CHAIN = '''@process
class Node:
    pass

p1 = Node(speed=1.0)
p2 = Node(speed=1.0)
p3 = Node(speed=1.0)

world = World(machines=[p1, p2, p3])

def story() -> void:
    p1.send(p2, "hello")
    p2.send(p3, "onwards")
    p3.send(p1, "reply")
    p2.send(p1, "and again")

job = Calls(run=story%s)

world.run(job)
'''

# --- vector clocks: concurrency is visible ------------------------------
vec = stamps(CHAIN % "")
ok("a vector clock stamps every event", len(vec) == 8, str(len(vec)))


def concurrent(a, b):
    return (not all(x <= y for x, y in zip(a, b))
            and not all(x >= y for x, y in zip(a, b)))


pairs = [(a, b) for i, (_, a, _) in enumerate(vec)
         for (_, b, _) in vec[i + 1:] if concurrent(a, b)]
ok("vector clocks show concurrent events as incomparable", pairs,
   f"{len(pairs)} incomparable pair(s)")

# --- Lamport: totally ordered, and that is the problem ------------------
lam = stamps(CHAIN % ', clock="lamport"')
ok("a Lamport stamp is a single number",
   all(isinstance(n, int) for _, n, _ in lam), str(lam[:2]))

# The guarantee: along a chain, the numbers strictly increase.
chain = [n for who, n, label in lam if label in ("hello", "recv hello",
                                                 "onwards", "recv onwards")]
ok("if a happened before b then L(a) < L(b)",
   all(x < y for x, y in zip(chain, chain[1:])), str(chain))

# The missing converse: an event concurrent with another gets a number that
# orders them anyway. This is the point of the whole task, so it is asserted
# rather than left to the diagram.
by_label = {label: n for _, n, label in lam}
ok("but a smaller number does not mean it happened first",
   by_label.get("and again", 0) < by_label.get("reply", 0),
   f"'and again' is {by_label.get('and again')} and 'reply' is "
   f"{by_label.get('reply')}, and neither happened before the other")

# --- causal delivery: held, then released -------------------------------
CHAT = '''@process
class Node:
    pass

p1 = Node(speed=1.0)
p2 = Node(speed=1.0)
p3 = Node(speed=1.0)

world = World(machines=[p1, p2, p3])

def story() -> void:
    p1.broadcast("A", late=p3)
    p2.broadcast("B")

job = Calls(run=story%s)

world.run(job)
'''

loose = [label for who, _, label in stamps(CHAT % "", machine="p3")]
ok("without a delivery rule the reply is shown before the message",
   loose == ["recv B", "recv A (late)"], str(loose))

held = [label for who, _, label in stamps(CHAT % ', delivery="causal"',
                                          machine="p3")]
ok("with one, the order is repaired", held == ["deliver A", "deliver B"],
   str(held))
ok("and the student is told what was held",
   any("holding 'B'" in n for n in notes(CHAT % ', delivery="causal"')),
   "; ".join(notes(CHAT % ', delivery="causal"')) or "(no note)")
ok("nothing is dropped to achieve it", len(held) == 2, str(held))

# A broadcast is one send, not one per recipient: the sender's own entry must
# advance by one however many processes are listening.
first = stamps(CHAT % "")[0]
ok("a broadcast advances the sender's counter once", first[1] == [1, 0, 0],
   str(first[1]))

# Point-to-point under a broadcast rule is a mistake worth naming.
POINT = CHAT.replace('p1.broadcast("A", late=p3)', 'p1.send(p2, "A")')
ok("a point-to-point message under causal delivery is explained, not ignored",
   any("causal delivery" in n for n in notes(POINT % ', delivery="causal"')),
   "; ".join(notes(POINT % ', delivery="causal"'))[:100] or "(no note)")

# --- the shipped task does what its comments promise --------------------
task = ASSIGNMENTS["t9-buffering"].starter
shipped = [label for who, _, label in stamps(task, machine="p3")]
ok("the task ships showing the problem", shipped == ["recv B", "recv A (late)"],
   str(shipped))
fixed = [label for who, _, label in stamps(
    task.replace("job = Calls(run=story)",
                 'job = Calls(run=story, delivery="causal")'), machine="p3")]
ok("and its step 2 fixes it", fixed == ["deliver A", "deliver B"], str(fixed))

print("ALL CLOCK TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
