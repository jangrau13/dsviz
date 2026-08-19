"""
A submission has to be something that ran.

`solutions/` is what CI grades, and the shortcut around the whole editor is to
put a file there by hand. This is the check that makes that fail: handing in
stamps the file with a digest of what the code *did*, and grading recomputes
it. None of this is unforgeable — the engine ships in the student's checkout —
but it is no longer free, and the accidental version of it does not work.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz import attest                                          # noqa: E402

# The reference solution, kept here rather than in a shipped module.
SOLUTION = '''def tokenize(key: string, value: string) -> void:
    for word: string in split(lower(value)):
        emit(word, 1)

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n


@mapper
class Worker:
    pass

@reducer
class Collector:
    pass

m1 = Worker(speed=1.0)
m2 = Worker(speed=1.0)
r1 = Collector(speed=1.0)
r2 = Collector(speed=1.0)

world = World(machines=[m1, m2, r1, r2])

job = MapReduce(map=tokenize, reduce=total, partition=byKey)
world.run(job)
'''
TASK = "a1-wordcount"

print("=== a stamped hand-in verifies ===")
stamped = attest.stamp(TASK, SOLUTION, at="2026-08-19T09:00:00+00:00", runs=100)
assert attest.verify(TASK, stamped) == [], attest.verify(TASK, stamped)
print("ok — the file the server writes is accepted")

print("\n=== the digest is of the run, not of the text ===")
# Same code twice must agree, or CI would reject honest submissions at random.
assert attest.trace_sha(SOLUTION, TASK) == attest.trace_sha(SOLUTION, TASK)
# Different code must disagree, or the digest would attest to nothing.
other = SOLUTION.replace("Worker(speed=1.0)\nm2", "Worker(speed=0.2)\nm2")
assert other != SOLUTION
assert attest.trace_sha(other, TASK) != attest.trace_sha(SOLUTION, TASK)
print("ok — reproducible for the same code, different for different code")

print("\n=== the shortcuts fail ===")
CASES = {
    "a file copied straight from the workspace": SOLUTION,
    "a hand-written file with no record": "# my solution\n" + SOLUTION,
    "a record that is not readable": SOLUTION + "\n# dsviz-run: {not json",
    "code edited after handing in":
        stamped.replace("sum(values)", "sum(values) + 0"),
    "a record lifted from another task":
        attest.stamp("extra-combiner", SOLUTION, at="", runs=1),
}
for label, text in CASES.items():
    reasons = attest.verify(TASK, text)
    assert reasons, f"NOT CAUGHT: {label}"
    print(f"caught: {label}\n        {reasons[0][:72]}…")

print("\n=== a forged record is caught by running the code ===")
# The source hash is easy to compute; the trace hash is not, because producing
# it means running the simulation. Getting the first right and the second wrong
# is exactly what a hand-written record looks like.
body, record = attest.split(stamped)
forged = attest.MARKER + '{"task":"%s","source":"%s","trace":"%s","at":"","runs":1}' % (
    TASK, attest.source_sha(body), "0" * 64)
reasons = attest.verify(TASK, attest.canonical(body) + "\n" + forged + "\n")
assert reasons and "does not match what this code does" in reasons[0], reasons
print(f"caught: a record with the right source hash and an invented trace\n"
      f"        {reasons[0][:72]}…")

print("\n=== whitespace is not tampering ===")
# A CRLF checkout, or an editor that trims trailing spaces, must not read as a
# forged submission — the student did nothing.
crlf = stamped.replace("\n", "\r\n")
assert attest.verify(TASK, crlf) == [], attest.verify(TASK, crlf)
trailing = "\n".join(line + "   " for line in stamped.split("\n"))
assert attest.verify(TASK, trailing) == [], attest.verify(TASK, trailing)
print("ok — line endings and trailing spaces still verify")

print("\n=== the code still parses with the record on it ===")
from dsviz.assignment import judge_assignment                     # noqa: E402
import json                                                        # noqa: E402

graded, _ = attest.split(stamped)
result = json.loads(judge_assignment(TASK, graded, True))
assert result["verdict"] == "AC", result
print(f"ok — the stamped solution grades as {result['verdict']} once split")

print("\nALL ATTESTATION TESTS PASSED")
