"""
A submission is graded on what the student wrote, and on nothing else.

Every check here exists because the engine used to fill something in. A
MapReduce with no wiring got a built-in word count, a built-in sum and a
built-in hash; a program with its last two lines deleted was judged on whether
it raised, and it did not. Both produced marks for work nobody did.

The reference solution written before `job =` existed is the exhibit: it scored
5 of 7 on held-out input, and the two it failed were the two needing `lower()`
— because the student's mapper was never called at all. Output that has not
been through the submitted code is the failure mode this file is against.
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixture  # noqa: E402  dsviz ships no tasks; this brings some

from dsviz.assignment import ASSIGNMENTS, judge_assignment          # noqa: E402

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def verdict(task, src, holdout=False):
    return json.loads(judge_assignment(task, src, holdout))


WORLD = ASSIGNMENTS["fx-takings"].starter

FUNCS = fixture.FUNCS

# --- the old syntax is gone, not merely discouraged ----------------------
# Functions named `map`, `reduce` and `partition`, no job line. This is what
# the previous reference solution looked like, and it used to score.
OLD = '''def map(key: string, value: string) -> void:
    for branch: string in split(value):
        emit(branch, 1)

def reduce(key: string, values: [int]) -> int:
    sum(values)

def partition(key: string, n: int) -> int:
    hash(key) mod n
'''
old = verdict("fx-takings", OLD, holdout=True)
ok("binding roles by function name no longer works",
   old["verdict"] == "CE", f"{old['verdict']}: {old['cases'][0]['message'][:60]}")
ok("and it scores nothing rather than most of it",
   old["score"] == 0, f"scored {old['score']:g}")

# --- each position must be filled ---------------------------------------
for omitted in ("map", "reduce", "partition"):
    wiring = ", ".join(f"{r}={n}" for r, n in
                       (("map", "perDay"), ("reduce", "addUp"),
                        ("partition", "spread")) if r != omitted)
    src = f"{FUNCS}\n{WORLD}\njob = MapReduce({wiring})\nworld.run(job)\n"
    got = verdict("fx-takings", src)
    ok(f"a job with no {omitted} is refused", got["verdict"] == "CE",
       got["cases"][0]["message"][:70])

# A name that is not a function must not silently fall back either — in any
# position. The mapper used to be reported differently from the other two:
# "this job's mapper is nosuch, not tokenize", on the emit line, which is true
# and says nothing about the typo the student actually made.
for role in ("map", "reduce", "partition"):
    wiring = ", ".join(
        f"{r}=" + ("nosuch" if r == role else n) for r, n in
        (("map", "perDay"), ("reduce", "addUp"), ("partition", "spread")))
    src = f"{FUNCS}\n{WORLD}\njob = MapReduce({wiring})\nworld.run(job)\n"
    got = verdict("fx-takings", src)
    message = got["cases"][0]["message"]
    ok(f"a job naming no such function as the {role} is refused",
       got["verdict"] == "CE" and "nosuch" in message, message[:70])

# The check that catches a genuinely misplaced emit must still fire: only the
# function passed as the mapper may emit.
STRAY = FUNCS + "\ndef alsoEmits(key: string, value: string) -> void:\n    emit(key, 1)\n"
src = f"{STRAY}\n{WORLD}\njob = MapReduce(map=perDay, reduce=addUp, partition=spread)\nworld.run(job)\n"
got = verdict("fx-takings", src)
ok("a function that is not the mapper still may not emit",
   got["verdict"] == "CE" and "emit" in got["cases"][0]["message"],
   got["cases"][0]["message"][:70])

# --- the complete thing still works -------------------------------------
whole = f"{FUNCS}\n{WORLD}\njob = MapReduce(map=perDay, reduce=addUp, partition=spread)\nworld.run(job)\n"
got = verdict("fx-takings", whole)
ok("a properly wired submission still passes", got["verdict"] == "AC",
   f"{got['score']:g}/{got['max_score']:g}")

# --- nothing passes by doing nothing ------------------------------------
# Delete the job and the run from each shipped starter. This is the laziest
# possible submission, and for exploration tasks it used to be a pass.
gutted_passes = []
for name, spec in ASSIGNMENTS.items():
    gutted = "\n".join(
        line for line in spec.starter.split("\n")
        if not re.match(r"^\s*(job|client)\s*=", line) and "world.run(" not in line)
    if verdict(name, gutted)["verdict"] == "AC":
        gutted_passes.append(name)
ok("a program with its job and run deleted passes nothing",
   not gutted_passes, ", ".join(gutted_passes))

# And the intact starters of exploration tasks still do run, or the check
# above would be passing for the wrong reason.
broken = [n for n, s in ASSIGNMENTS.items()
          if not (s.expects or s.requires or s.budgets)
          and verdict(n, s.starter)["verdict"] != "AC"]
ok("while every exploration task still runs as shipped", not broken,
   ", ".join(broken))

print("ALL 'NOTHING FOR FREE' TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
