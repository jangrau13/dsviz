"""
The Spark operators, pinned to what real Spark answers.

Every value asserted here was measured against PySpark 4.1.3 in
`apache/spark:4.1.3-python3` by the differential harness in `spark-vs-sim/`,
not derived from reading the documentation. That harness needs Docker and a
JVM, so it cannot run in CI — which is exactly why its findings belong here as
plain assertions. Twice now a bug has been caught by running real Spark and
would not have been caught by anything in this repository:

  1. Three operators were declared in NARROW/WIDE with no branch in `_apply`,
     so the linter offered them by name and applying one said "cannot be
     applied here" — the message for an operator used in the wrong position,
     given for an operator nobody had written.
  2. `foldByKey` had Spark's signature inverted. `foldByKey(zeroValue, func)`
     was rejected and `foldByKey(func)` was accepted, so the form the Spark
     documentation gives failed and the form that runs nowhere but here
     worked. That is worse than an unimplemented operator: it refuses nothing
     and teaches an API that does not exist.
  3. `mapPartitions` split contiguously but put the remainder in the front
     partitions, where Spark puts it in the last ones. Same total, different
     partitions — and partition boundaries are the whole subject of that
     operator, so a student calling it to see what a partition holds was shown
     a partition Spark would never give them.

If a case here fails, prefer believing this file over the engine: these are
measurements, and the engine is the thing that has been wrong each time.
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.notation import NotationError
import random

from dsviz.pyspark import (COMBINES, NARROW, WIDE, Budget, _apply,
                            _partition)

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


NODE = ast.parse("x").body[0]


def apply(op, args, data, partitions=1):
    return _apply(op, args, data, NODE, 1, Budget(), None, None, partitions)


# --- every declared operator can actually be applied ---------------------
#
# The type checker reads NARROW and WIDE, so anything named there is offered
# to students. An operator that is offered and then refuses is a worse failure
# than one that was never mentioned.
DATA = {"pairs": [("k1", 1), ("k1", 2), ("k2", 3)], "flat": [1, 2, 3]}
ARGS = {
    "reduceByKey": [lambda a, b: a + b],
    "foldByKey": [0, lambda a, b: a + b],
    "aggregateByKey": [0, lambda acc, v: acc + v, lambda a, b: a + b],
    "combineByKey": [lambda v: v, lambda acc, v: acc + v, lambda a, b: a + b],
    "mapPartitions": [lambda it: [sum(it)]],
}
for op in sorted(NARROW | WIDE):
    if op not in ARGS:
        continue
    data = DATA["flat"] if op == "mapPartitions" else DATA["pairs"]
    try:
        apply(op, ARGS[op], data)
        ran = True
        why = ""
    except NotationError as exc:
        ran, why = False, str(exc).replace("\n", " ")
    ok(f"{op} has an implementation, not only a name", ran, why)


# --- mapPartitions splits where Spark splits -----------------------------
#
# Measured: sc.parallelize([3,1,4,1,5], N).mapPartitions(lambda it: [sum(it)])
# The remainder falls to the LAST partitions, because Spark's boundaries are
# (i * n) // slices rather than an even split with the extra at the front.
SPLITS = {
    1: [[3, 1, 4, 1, 5]],
    2: [[3, 1], [4, 1, 5]],
    3: [[3], [1, 4], [1, 5]],
    4: [[3], [1], [4], [1, 5]],
}
for parts, want in SPLITS.items():
    got = apply("mapPartitions", [lambda it: [list(it)]], [3, 1, 4, 1, 5], parts)
    ok(f"mapPartitions splits [3,1,4,1,5] as Spark does at {parts} partition(s)",
       got == want, "" if got == want else f"{got} != {want}")

SUMS = {1: [14], 2: [4, 10], 3: [3, 5, 6], 4: [3, 1, 4, 6]}
for parts, want in SUMS.items():
    got = apply("mapPartitions", [lambda it: [sum(it)]], [3, 1, 4, 1, 5], parts)
    ok(f"mapPartitions sums per partition as Spark does at {parts}",
       got == want, "" if got == want else f"{got} != {want}")


# --- the combining operators take Spark's signatures ---------------------
#
# Each of these is what the Spark documentation gives. A student copying from
# it must not be told they got it wrong.
def answer(op, args, parts=1):
    return dict(apply(op, args, [("k1", 1), ("k1", 2)], parts))


ok("reduceByKey(func)", answer("reduceByKey", [lambda a, b: a + b]) == {"k1": 3})
ok("foldByKey(zeroValue, func)", answer("foldByKey", [0, lambda a, b: a + b]) == {"k1": 3})
ok("aggregateByKey(zeroValue, seqFunc, combFunc)",
   answer("aggregateByKey", [0, lambda acc, v: acc + v, lambda a, b: a + b]) == {"k1": 3})
ok("combineByKey(createCombiner, mergeValue, mergeCombiners)",
   answer("combineByKey", [lambda v: v, lambda acc, v: acc + v,
                           lambda a, b: a + b]) == {"k1": 3})

# And refuse the form that runs nowhere else. Real Spark raises TypeError on
# foldByKey(func); accepting it here would teach an API that does not exist.
try:
    answer("foldByKey", [lambda a, b: a + b])
    refused, msg = False, "accepted"
except NotationError as exc:
    refused, msg = True, str(exc).replace("\n", " ")
ok("foldByKey(func) is refused, as Spark refuses it", refused,
   "" if refused else msg)
ok("and the refusal names the real signature", "zeroValue, func" in msg,
   "" if "zeroValue, func" in msg else msg)


# --- a zero value is not shared between partitions -----------------------
# Spark gives each partition its own accumulator. A mutable zero that every
# partition wrote into would collect the same values several times.
got = dict(apply("aggregateByKey", [[], lambda acc, v: acc + [v],
                                    lambda a, b: a + b],
                 [("k", 1), ("k", 2), ("k", 3)], 3))
ok("a mutable zeroValue is per-partition, not shared",
   sorted(got["k"]) == [1, 2, 3], "" if sorted(got["k"]) == [1, 2, 3] else str(got))


# --- what the cost model prices, it can run ------------------------------
# COMBINES decides whether a wide operation ships one record per key instead
# of every record. Pricing a map-side combine for an operator that cannot run
# is how the last round of this went wrong.
unrunnable = sorted(op for op in COMBINES if op not in ARGS)
ok("every operator the cost model treats as combining is implemented",
   not unrunnable, ", ".join(unrunnable))


# --- the one disagreement with Spark that is meant to be there -----------
#
# Spark assigns a key's values to partitions by where the source records
# already were, so for a fixed input and partition count it answers the same
# way every time — including from a reducer that has no right to one answer.
# Measured: (a + b) / 2 over [1, 2, 9, 12] gives real Spark 6.0 at two
# partitions, 6.0 at three, and 8.625 at four. Three answers to one question.
#
# The simulator draws the grouping fresh each run instead, so the spread is
# visible from a single program rather than only to someone who thought to
# vary the partition count. That is the whole reason the construct exists, so
# it is asserted here: a differential run finding THIS disagreeing is correct,
# and making it agree would delete the lesson.
fixed = dict(apply("reduceByKey", [lambda a, b: (a + b) / 2],
                   [("k", 1.0), ("k", 2.0), ("k", 9.0), ("k", 12.0)], 2))
ok("a non-associative reducer is reproducible when nothing supplies chance",
   fixed["k"] == 8.625, "" if fixed["k"] == 8.625 else str(fixed))

spread = set()
for seed in range(24):
    acc = None
    for bucket in _partition([1.0, 2.0, 9.0, 12.0], 2, random.Random(seed)):
        value = bucket[0]
        for nxt in bucket[1:]:
            value = (value + nxt) / 2
        acc = value if acc is None else (acc + value) / 2
    spread.add(acc)
ok("but with the cluster's own chance it spreads, as a broken reducer should",
   len(spread) > 1, "" if len(spread) > 1 else f"one answer: {sorted(spread)}")
# Stronger than "6.0 is reachable": every answer real Spark gave at ANY
# partition count is in the spread at two — 6.0 at 2 and 3 partitions, 8.625
# at 4. The simulator is not showing a different world, it is showing several
# of Spark's at once, from one cluster size.
#
# Two things about this that look alike and are not, because one of them is a
# flaky test waiting to be written. Measured over 3000 runs per count:
#
#   what is asserted here — Spark's two answers are CONTAINED in the spread.
#   Both are common: p(6.0) is .33-.39 and p(8.625) is .10-.24 at every count,
#   so 60 samples miss one with probability .0000 at two partitions and .0020
#   at four. Safe anywhere. It is at two because that is the smallest cluster
#   that makes the point, not because the others would break.
#
#   what must NOT be asserted — that the spread has exactly N values. The full
#   set saturates slowly: 5 values at two partitions, but 12 at three and 12
#   at four, whose rarest members land 2-3% of the time. 60 runs misses one
#   about a quarter of the time at three partitions. Pinning the count would
#   be a test that fails for no reason one run in four.
#
# So "assert more of the spread, it is a stronger claim" is the tempting and
# wrong change here — the same shape as "make the engines agree" above.
SPARK_ANSWERS = {6.0, 8.625}
ok("every answer Spark gives at any partition count is in the spread at two",
   SPARK_ANSWERS <= spread,
   "" if SPARK_ANSWERS <= spread else f"missing {sorted(SPARK_ANSWERS - spread)}")

print()
if failures:
    print(f"{len(failures)} SPARK OPERATOR CHECK(S) FAILED")
    sys.exit(1)
print("ALL SPARK OPERATOR TESTS PASSED")
