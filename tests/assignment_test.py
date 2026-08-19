"""Tasks, hold-out grading, and the type checker students face."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dsviz.assignment import ASSIGNMENTS, judge_assignment
from dsviz.langserver import analyse

# Reference solutions live here, in the test suite — never in a module that is
# served to the browser.
# Deliberately named nothing like `map`/`reduce`/`partition`: what makes a
# function a mapper is being passed as one, and these solutions only pass if
# that is genuinely true of the implementation.
SOLUTIONS = {
 "t1-wordcount": '''def tokenize(key: string, value: string) -> void:
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
''',
 "t2-combiner": '''def tokenize(key: string, value: string) -> void:
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

job = MapReduce(map=tokenize, reduce=total, combine=total, partition=byKey)
world.run(job)
''',
}

for name, spec in ASSIGNMENTS.items():
    starter = json.loads(judge_assignment(name, spec.starter))

    # An exploration task has no criteria — its starter is meant to run. A
    # graded task's starter is a scaffold and must not already pass.
    if not spec.expects and not spec.budgets:
        assert starter["verdict"] == "AC", f"{name}: exploration starter must run"
        print(f"ok {name}: exploration task runs as given")
        continue

    assert starter["verdict"] != "AC", f"{name}: the starter must not already pass"
    sol = SOLUTIONS[name]
    vis = json.loads(judge_assignment(name, sol))
    hid = json.loads(judge_assignment(name, sol, True))
    assert vis["verdict"] == "AC", f"{name} visible: {vis}"
    assert hid["verdict"] == "AC", f"{name} held-out: {hid}"
    # Correctness cases must not leak their expected values on the hold-out.
    # Budgets stay visible on purpose: they are the design target, not the
    # answer, and a student needs to know what they are aiming at.
    visible_kinds = {r.name for r in spec.requires}          # design checks
    for c in hid["cases"]:
        is_budget = any(c["name"].startswith(b) for b in
                        ("network", "makespan", "imbalance", "tail",
                         "memory", "faults"))
        # Budgets and requirements stay visible on purpose: they describe the
        # design a student is aiming at, not the answer they must produce.
        assert (is_budget or c["name"] in visible_kinds
                or c["name"] == "held-out test"), c
    print(f"ok {name}: starter {starter['label']}, solution passes visible + held-out")

# Fitting to the visible data must fail the hold-out.
cheat = SOLUTIONS["t1-wordcount"].replace("sum(values)", "4")
assert json.loads(judge_assignment("t1-wordcount", cheat, True))["verdict"] != "AC"
print("ok hardcoding fails the held-out input")

# A skipped step must be caught even when the output happens to be right.
skipped = SOLUTIONS["t1-wordcount"].replace("return hash(key) mod n", "return 0")
assert skipped != SOLUTIONS["t1-wordcount"], "the mutation must actually change the code"
res = json.loads(judge_assignment("t1-wordcount", skipped))
assert res["verdict"] != "AC", "a partitioner that ignores the key must fail"
assert any("received nothing" in c["message"] for c in res["cases"]), res["cases"]
print("ok an unwritten partitioner is caught, not silently passed")

# A wrong answer must point at the function that caused it.
for mutation, replacement, expect in [
    ("split(lower(value))", "split(value)", "not normalising"),
    ("return sum(values)", "return 0", "regardless of the values"),
]:
    broken = SOLUTIONS["t1-wordcount"].replace(mutation, replacement)
    assert broken != SOLUTIONS["t1-wordcount"], mutation
    msgs = " ".join(c["message"] for c in
                    json.loads(judge_assignment("t1-wordcount", broken))["cases"])
    assert expect in msgs, (mutation, msgs)
print("ok a failing case names the likely cause")

# Types are written, never inferred.
bad = SOLUTIONS["t1-wordcount"].replace("for word: string in", "for word in")
errs = [d for d in json.loads(analyse(bad, "t1-wordcount"))["diagnostics"]
        if d["severity"] == "error"]
assert errs, "an untyped loop variable must be rejected"
print("ok untyped bindings are rejected")

# Every metric explains itself.
r = json.loads(analyse(SOLUTIONS["t1-wordcount"], "t1-wordcount"))
for m in r["metrics"]:
    assert m["explain"]["what"] and m["explain"]["why"], m["name"]
print(f"ok all {len(r['metrics'])} metrics carry an explanation")

print("\nALL ASSIGNMENT TESTS PASSED")
