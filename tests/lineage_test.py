"""
What the run did, not what it answered.

The differential harness in `spark-vs-sim/` runs a program through both this
engine and real PySpark and compares the results. That is the right way to
check what a pipeline *answers*, and it has found five real bugs. It cannot
check any of what follows, and the reason is structural rather than a gap in
its case list: recomputation, cache reuse, shuffle traffic and stragglers all
leave the answer unchanged. Two engines agreeing on every value tells you
nothing about whether a cached RDD was rebuilt four times on the way there.

So the boundary is: that harness holds what the program answers, this file
holds what the run costs. Neither can see the other's half.

These are also the claims Assignment 2 is *about*. A student is asked to
notice that losing an early step costs more than losing a late one, that
caching pays when two branches read the same RDD, and that filtering before a
shuffle beats filtering after. If the engine did not actually behave that way,
the exercise would be teaching a story the tool contradicts — and every one of
these was, at some point, asserted by a comment rather than by the engine.

Written as properties rather than fixed numbers on purpose. `makespan == 14.10`
breaks whenever a cost constant is tuned and says nothing when it passes;
`losing an earlier step costs more than losing a later one` is the actual
claim, survives tuning, and fails exactly when the lesson stops being true.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz import runtime
from dsviz.exercise import load

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


# The tasks live in the exercises, not here. Without one loaded there is no
# `chunk001.txt`, so these programs declare their own input instead — which
# also keeps this suite honest about not needing a sibling checkout.
INPUT = ('input rows: "the cat sat" | "the mat sat" | "a cat sat" '
         '| "the dog ran"\n')


def program(body: str, machines: int = 2, speeds=None) -> str:
    speeds = speeds or [1.0] * machines
    decls = "\n".join(f"e{i + 1} = Executor(speed={s})"
                      for i, s in enumerate(speeds))
    names = ", ".join(f"e{i + 1}" for i in range(len(speeds)))
    return ("@machine\nclass Executor:\n    pass\n"
            f"{decls}\n"
            f"world = World(machines=[{names}])\n\n"
            f"{INPUT}\n{body}\n")


def run(body: str, **kw):
    """One run, and the things worth asserting about it."""
    cluster = runtime.build(program(body, **kw), seed=0)
    trace = cluster.trace
    return {
        "makespan": trace.duration,
        "work": [e.detail.get("label", "") for e in trace.of_kind("work")],
        "notes": [e.detail.get("text", "") for e in trace.of_kind("note")],
        "sends": [e.detail.get("payload", "") for e in trace.of_kind("send")],
    }


WORDS = ('lines = textFile("rows")\n'
         'words = lines.flatMap(lambda l: l.split(" "))\n'
         'pairs = words.map(lambda w: (w, 1))\n'
         'counts = pairs.reduceByKey(lambda a, b: a + b)\n')


# --- 1. a recomputation replays exactly what rebuilding needs -----------
#
# The whole argument for writing a lineage down is that a lost partition is
# rebuilt from its ancestors rather than reloaded from disk. If the engine
# replayed the wrong set — too few and the rebuild is a lie, too many and the
# cost of a loss is overstated — the diagram would still look right.
#
# `Lineage.recompute_set` is documented as "everything that must be
# recomputed to rebuild `lost`", which is the ancestors and the step itself.
# That is what is asserted here, and see the note under property 3 for the
# claim it does NOT support.
lost = run(WORDS + "job = Spark(pipeline=counts, lose=pairs)\n"
           "world.run(job)")
replay = [n for n in lost["notes"] if "recomputing" in n]
ok("losing a step says what is recomputed", bool(replay),
   "" if replay else str(lost["notes"]))
if replay:
    named = replay[0].split("recomputing")[-1]
    ok("the replay names the lost step and everything it was made from",
       all(n in named for n in ("lines", "words", "pairs")), named.strip())
    ok("and stops there — what the loss did not destroy is not rebuilt",
       "counts" not in named, named.strip())
    ok("in dependency order, so the replay could actually be performed",
       named.index("lines") < named.index("words") < named.index("pairs"),
       named.strip())


# --- 2. a cached RDD is not rebuilt for its second reader ----------------
#
# `cache()` is a line worth writing only if not writing it costs something.
# Two branches read the same RDD; with the cache they share one copy.
TWO_BRANCHES = (WORDS +
                'longest = pairs.filter(lambda p: len(p[0]) > 3)\n'
                'job = Spark(pipeline=counts)\n'
                'world.run(job)\n')
TWO_CACHED = (WORDS.replace(
    'pairs = words.map(lambda w: (w, 1))\n',
    'pairs = words.map(lambda w: (w, 1)).cache()\n') +
    'longest = pairs.filter(lambda p: len(p[0]) > 3)\n'
    'job = Spark(pipeline=counts)\n'
    'world.run(job)\n')

plain, cached = run(TWO_BRANCHES), run(TWO_CACHED)
again = [w for w in plain["work"] if "again" in w]
ok("an RDD read twice and not cached is computed twice", bool(again),
   "" if again else "no recompute work in the timeline")
ok("caching it removes the second computation",
   not [w for w in cached["work"] if "again" in w],
   str([w for w in cached["work"] if "again" in w]))
ok("and caching is cheaper, not merely quieter",
   cached["makespan"] < plain["makespan"],
   f"cached {cached['makespan']:.2f} vs plain {plain['makespan']:.2f}")
ok("the run says why, so the diagram can be read",
   any("cached" in n for n in cached["notes"]), str(cached["notes"]))


# --- 3. a loss costs more the more there is to rebuild ------------------
#
# Measured, losing each step of the same four-step pipeline in turn:
#
#     lose=lines   3.20   replays lines
#     lose=words   3.65   replays lines → words
#     lose=pairs   5.00   replays lines → words → pairs
#     lose=counts  6.35   replays lines → words → pairs → counts
#
# Monotonic in the length of the ancestry, which is the engine being
# self-consistent: `lose=` is applied once the pipeline has been built, so
# what a loss costs is what it takes to rebuild that one RDD.
#
# OPEN QUESTION, deliberately not asserted either way. a2-wordcount and
# a2-telemetry both tell the student the opposite — "losing `pairs` instead of
# `counts` rebuilds more, because everything downstream of it has to be made
# again. The cost of a loss is the size of its descendants, not of the step
# itself." Against this engine that is false: an earlier loss is the cheap one.
#
# Both readings are defensible and they answer different questions. Ancestors
# is what it costs to rebuild a lost RDD after the job is done. Ancestors plus
# descendants is what it costs when the partition is lost *during* the job and
# the final answer is still wanted — which is the Spark situation the exercise
# describes, and the one that makes caching an early step worth doing.
#
# Whichever way it is settled, the engine and the two solution notes must stop
# disagreeing: a student following the note sees the opposite and has to decide
# whether to disbelieve the tool or themselves. Asserted here only in the
# direction that holds today, so this suite records the behaviour without
# blessing it.
costs = {}
for step in ("lines", "words", "pairs", "counts"):
    costs[step] = run(WORDS + f"job = Spark(pipeline=counts, lose={step})\n"
                      "world.run(job)")["makespan"]
ok("a loss costs strictly more the longer the ancestry it must replay",
   costs["lines"] < costs["words"] < costs["pairs"] < costs["counts"],
   ", ".join(f"{k}={v:.2f}" for k, v in costs.items()))
ok("and losing anything costs something",
   min(costs.values()) > 0, str(costs))


# --- 4. filtering before a shuffle moves fewer records ------------------
#
# a2-telemetry's whole lesson. The cheapest record to move is one you dropped.
BEFORE = ('lines = textFile("rows")\n'
          'words = lines.flatMap(lambda l: l.split(" "))\n'
          'kept = words.filter(lambda w: w != "the")\n'
          'pairs = kept.map(lambda w: (w, 1))\n'
          'counts = pairs.groupByKey()\n'
          'job = Spark(pipeline=counts)\nworld.run(job)\n')
AFTER = ('lines = textFile("rows")\n'
         'words = lines.flatMap(lambda l: l.split(" "))\n'
         'pairs = words.map(lambda w: (w, 1))\n'
         'grouped = pairs.groupByKey()\n'
         'counts = grouped.filter(lambda p: p[0] != "the")\n'
         'job = Spark(pipeline=counts)\nworld.run(job)\n')


def crossing(result) -> int:
    """How many records the shuffle carried, from what the send says."""
    for payload in result["sends"]:
        if ":" in payload and "record" in payload:
            return int(payload.split(":")[1].strip().split()[0])
    return -1


ok("filtering before a wide step moves fewer records than filtering after",
   0 < crossing(run(BEFORE)) < crossing(run(AFTER)),
   f"before {crossing(run(BEFORE))} vs after {crossing(run(AFTER))}")


# --- 5. a combining reducer ships less than groupByKey ------------------
#
# The PDF's own question, and the difference between reduceByKey and
# groupByKey: one partial per key crosses instead of one record per value.
GROUPED = (WORDS.replace("pairs.reduceByKey(lambda a, b: a + b)",
                         "pairs.groupByKey()") +
           "job = Spark(pipeline=counts)\nworld.run(job)\n")
COMBINED = WORDS + "job = Spark(pipeline=counts)\nworld.run(job)\n"
ok("reduceByKey ships less than groupByKey",
   0 < crossing(run(COMBINED)) <= crossing(run(GROUPED)),
   f"reduceByKey {crossing(run(COMBINED))} vs "
   f"groupByKey {crossing(run(GROUPED))}")


# --- 6. one slow executor holds up the stage ----------------------------
#
# The straggler. A stage is not finished until its slowest member is, so a
# slow machine must lengthen the run rather than being averaged away.
even = run(WORDS + "job = Spark(pipeline=counts)\nworld.run(job)",
           speeds=[1.0, 1.0, 1.0])
straggler = run(WORDS + "job = Spark(pipeline=counts)\nworld.run(job)",
                speeds=[1.0, 1.0, 0.2])
ok("a slow executor lengthens the run rather than being averaged away",
   straggler["makespan"] > even["makespan"],
   f"straggler {straggler['makespan']:.2f} vs even {even['makespan']:.2f}")


# --- 7. more executors finish sooner ------------------------------------
#
# The claim behind declaring a world at all. Not linear — a shuffle does not
# get cheaper with more machines — but it must not be flat, or the size of a
# cluster would be a decoration.
small = run(WORDS + "job = Spark(pipeline=counts)\nworld.run(job)", speeds=[1.0])
big = run(WORDS + "job = Spark(pipeline=counts)\nworld.run(job)",
          speeds=[1.0] * 4)
ok("a bigger cluster finishes sooner", big["makespan"] < small["makespan"],
   f"4 executors {big['makespan']:.2f} vs 1 {small['makespan']:.2f}")

print()
if failures:
    print(f"{len(failures)} LINEAGE/COST PROPERTY CHECK(S) FAILED")
    sys.exit(1)
print("ALL LINEAGE AND COST PROPERTIES HOLD")
