"""
What a machine remembers.

A machine that only answers is a function with a network in front of it: ask
it twice and it says the same thing twice. State is what makes the second call
able to see what the first one did — and what a crash destroys, which is the
only reason failure costs anything in this course.

Four things have to hold, and each of them has been wrong in some version of
this:

  * a field is per machine, not per kind. Two ledgers of one class that share
    a counter would make every instance of a class one machine.
  * an update inside a method changes the machine, not a local that is thrown
    away when the call returns.
  * a crash puts every field back where it started, and says so on the trace,
    because that loss is the thing being taught.
  * the diagram shows it: one line per field, inside the machine's own box,
    below its name and above nothing that would overlap it.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.notation import NotationError
from dsviz.runtime import build
from dsviz.shapes import (CHIP_H, LABEL_STRIP, box_height, dataflow,
                          held_positions, state_positions)
from dsviz.syntax import lint

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


PROGRAM = """
@machine
class Turnstile:
    passages: int = 0

    @duration(0.3)
    def admit(who: string) -> int:
        passages: int = passages + 1
        return passages

gate = Turnstile(type="m1.small")
foyer = Turnstile(passages=10)

world = World(machines=[gate, foyer])

def story() -> void:
    first: int = gate.admit("ada")
    second: int = gate.admit("bo")
    elsewhere: int = foyer.admit("cy")

job = Calls(run=story)
world.run(job)
"""

cluster = build(PROGRAM, seed=1)

# --- it is remembered between calls -------------------------------------
ok("a field survives from one call to the next",
   cluster.machines["gate"].state["passages"] == 2,
   str(cluster.machines["gate"].state))

# --- and it belongs to the machine, not the kind -------------------------
ok("each machine remembers its own",
   cluster.machines["foyer"].state["passages"] == 11,
   str(cluster.machines["foyer"].state))

# --- the reply is what the body computed --------------------------------
replies = [e.detail["reply"] for e in cluster.trace.of_kind("rpc")]
ok("the answer moves with the state", replies == [1, 2, 11], str(replies))

# --- every change is on the trace, starting value included ---------------
changes = [(e.machine, e.detail["value"], e.detail["reason"])
           for e in cluster.sorted_trace().of_kind("state")]
ok("what it starts at is stated before anything happens",
   changes[:2] == [("gate", 0, "start"), ("foyer", 10, "start")], str(changes[:2]))
ok("and every change after it is an event of its own",
   [c for c in changes if c[2] == "admit"]
   == [("gate", 1, "admit"), ("gate", 2, "admit"), ("foyer", 11, "admit")],
   str(changes))

# --- a crash loses it ----------------------------------------------------
CRASHING = PROGRAM.replace(
    '    elsewhere: int = foyer.admit("cy")',
    '    gate.crash()\n    gate.restart()\n'
    '    third: int = gate.admit("cy")')
crashed = build(CRASHING, seed=1)
ok("a crash puts what it held back where it started",
   crashed.machines["gate"].state["passages"] == 1,
   "counted from zero again, so the machine came back empty")
lost = [e.detail.get("forgot") for e in crashed.trace.of_kind("crash")]
ok("and the crash says what was lost", lost == [["passages"]], str(lost))
after = [e for e in crashed.sorted_trace().of_kind("state")
         if e.detail["reason"] == "crash"]
ok("the loss is drawable, not merely true", len(after) == 1 and
   after[0].detail["value"] == 0, str(after))

# --- the diagram shows it ------------------------------------------------
frame = dataflow(cluster.sorted_trace(), title="")
badges = [s for s in frame if s.kind == "state"]
ok("one shape per reading, the starting value included",
   len(badges) == len(changes), f"{len(badges)} shapes, {len(changes)} readings")
ok("a reading holds until the next one replaces it",
   all(b.t_out is None or b.t_out > b.t_in for b in badges))
ok("every reading of a field sits in the same place",
   len({round(b.y, 6) for b in badges if b.meta["machine"] == "gate"}) == 1)

box = next(s for s in frame if s.kind == "box" and s.text == "gate")
inside = [b for b in badges if b.meta["machine"] == "gate"]
ok("and inside the machine's own box",
   all(box.y - box.h / 2 <= b.y - b.h / 2 and b.y + b.h / 2 <= box.y + box.h / 2
       for b in inside),
   f"box {box.y - box.h / 2:.2f}..{box.y + box.h / 2:.2f}")

# Held items must not come down onto the memory strip. Checked on the
# geometry rather than on a run, because a machine that both holds and
# remembers is exactly the case a layout gets wrong once and never again.
h = box_height(4, 2)
lowest = held_positions(0.0, 4, box_h=h, state_rows=2)[-1]
highest_state = state_positions(0.0, 2, box_h=h)[0]
ok("what a machine holds is stacked clear of what it remembers",
   lowest - CHIP_H / 2 >= highest_state + CHIP_H / 2 - 1e-9,
   f"items reach {lowest:.2f}, memory starts at {highest_state:.2f}")
ok("the box grew for the strip rather than the strip growing into the label",
   h >= box_height(4) and state_positions(0.0, 2, box_h=h)[0]
   < h / 2 - LABEL_STRIP)


# --- the checker catches what would otherwise be silent -------------------
def errors(source):
    return [d.message for d in lint(source)[1] if d.severity == "error"]


BAD_START = """
@machine
class Meter:
    reading: int = "nothing"

m = Meter()
world = World(machines=[m])
def story() -> void:
    m.crash()
job = Calls(run=story)
world.run(job)
"""
ok("a starting value that is not the declared type is refused",
   any("starts at" in e for e in errors(BAD_START)), str(errors(BAD_START)))

NOTHING_TO_HOLD = BAD_START.replace('reading: int = "nothing"',
                                    'reading: void = 0')
ok("a field that holds nothing is refused",
   any("cannot remember" in e for e in errors(NOTHING_TO_HOLD)),
   str(errors(NOTHING_TO_HOLD)))

SHADOWED = """
@machine
class Meter:
    reading: int = 0

    @duration(0.2)
    def record(reading: int) -> int:
        return reading

m = Meter()
world = World(machines=[m])
def story() -> void:
    seen: int = m.record(4)
job = Calls(run=story)
world.run(job)
"""
ok("a parameter that shadows a field is refused",
   any("same name" in e for e in errors(SHADOWED)), str(errors(SHADOWED)))

RETYPED = SHADOWED.replace("def record(reading: int) -> int:\n        return reading",
                           "def record(seen: int) -> int:\n"
                           "        reading: string = \"3\"\n"
                           "        return 3")
ok("an update that renames the type is refused",
   any("calls it string" in e for e in errors(RETYPED)), str(errors(RETYPED)))

WRONG_OVERRIDE = """
@machine
class Meter:
    reading: int = 0

m = Meter(reading="high")
world = World(machines=[m])
def story() -> void:
    m.crash()
job = Calls(run=story)
world.run(job)
"""
ok("an instance cannot start a field at the wrong type",
   any("is a string" in e for e in errors(WRONG_OVERRIDE)),
   str(errors(WRONG_OVERRIDE)))

INERT = """
@machine
class Meter:
    reading: int = 0

m = Meter()
world = World(machines=[m])
def story() -> void:
    m.crash()
job = Calls(run=story)
world.run(job)
"""
warnings = [d.message for d in lint(INERT)[1] if d.severity == "warning"]
ok("state nothing can change is said to be pointless, not left silent",
   any("no method" in w for w in warnings), str(warnings))
ok("and it is a warning, not a refusal", not errors(INERT), str(errors(INERT)))

# A machine with no state at all must be exactly what it was.
PLAIN = """
@machine
class Ping:
    @duration(0.2)
    def ping(word: string) -> string:
        return word

p = Ping()
world = World(machines=[p])
def story() -> void:
    said: string = p.ping("hello")
job = Calls(run=story)
world.run(job)
"""
plain = build(PLAIN, seed=1)
ok("a machine that remembers nothing has nothing on the diagram",
   not [s for s in dataflow(plain.sorted_trace()) if s.kind == "state"]
   and plain.machines["p"].state == {})

print()
if failures:
    print(f"{len(failures)} STATE CHECK(S) FAILED")
    sys.exit(1)
print("ALL STATE TESTS PASSED")
