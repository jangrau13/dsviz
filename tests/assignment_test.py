"""Tasks, hold-out grading, and the type checker students face."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixture  # noqa: E402  dsviz ships no tasks; this brings some

from dsviz.assignment import ASSIGNMENTS, judge_assignment
from dsviz.langserver import analyse

# Reference solutions live here, in the test suite — never in a module that is
# served to the browser. Deliberately named nothing like
# `map`/`reduce`/`partition`: what makes a function a mapper is being passed
# as one, and this solution only passes if that is genuinely true.
SOLUTIONS = {"fx-takings": fixture.SOLUTION}

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
cheat = SOLUTIONS["fx-takings"].replace("sum(values)", "4")
assert json.loads(judge_assignment("fx-takings", cheat, True))["verdict"] != "AC"
print("ok hardcoding fails the held-out input")

# A skipped step must be caught even when the output happens to be right.
skipped = SOLUTIONS["fx-takings"].replace("return hash(key) mod n", "return 0")
assert skipped != SOLUTIONS["fx-takings"], "the mutation must actually change the code"
res = json.loads(judge_assignment("fx-takings", skipped))
assert res["verdict"] != "AC", "a partitioner that ignores the key must fail"
assert any("received nothing" in c["message"] for c in res["cases"]), res["cases"]
print("ok an unwritten partitioner is caught, not silently passed")

# A wrong answer must point at the function that caused it.
for mutation, replacement, expect in [
    ("split(lower(value))", "split(value)", "not normalising"),
    ("return sum(values)", "return 0", "regardless of the values"),
]:
    broken = SOLUTIONS["fx-takings"].replace(mutation, replacement)
    assert broken != SOLUTIONS["fx-takings"], mutation
    msgs = " ".join(c["message"] for c in
                    json.loads(judge_assignment("fx-takings", broken))["cases"])
    assert expect in msgs, (mutation, msgs)
print("ok a failing case names the likely cause")

# Types are written, never inferred.
bad = SOLUTIONS["fx-takings"].replace("for branch: string in", "for word in")
errs = [d for d in json.loads(analyse(bad, "fx-takings"))["diagnostics"]
        if d["severity"] == "error"]
assert errs, "an untyped loop variable must be rejected"
print("ok untyped bindings are rejected")

# A local given a value and no type is a syntax error, and the grammar's own
# answer is "syntax error here, expected 'if'" — which is true, names a
# keyword nobody was reaching for, and leaves the student to work out that the
# missing thing is a type. The variable gets named instead.
untyped = analyse(
    "@machine\nclass N:\n    @duration(0.1)\n    def go(x: string) -> int:\n"
    "        return 1\n\na = N(speed=1.0)\n\nworld = World(machines=[a])\n\n"
    "def story() -> void:\n    got = a.go(\"hi\")\n    a.go(\"again\")\n\n"
    "job = Calls(run=story)\n\nworld.run(job)\n", "fx-calls")
named = [d for d in json.loads(untyped)["diagnostics"]
         if "got" in d["message"] and "type" in d["message"]]
assert named, json.loads(untyped)["diagnostics"]
assert named[0]["line"] == 12, named[0]
print("ok a local with no type is named, and so is the fix")

# Whether a particular task can be passed the wrong way is that task's test,
# and lives beside it — see BCS-DS-Assignment-1/tests/tasks_test.py, which
# checks that the search index cannot be passed by deduplicating after the
# shuffle. dsviz tests that a budget is enforced; an exercise tests that its
# own budget means what it says.

# Every metric explains itself.
r = json.loads(analyse(SOLUTIONS["fx-takings"], "fx-takings"))
for m in r["metrics"]:
    assert m["explain"]["what"] and m["explain"]["why"], m["name"]
print(f"ok all {len(r['metrics'])} metrics carry an explanation")

# --- the file's markers and the panel's steps are one list ---------------
# A starter marks the lines a step applies to — `# step 3: add , retries=2` —
# and the panel next to it lists what the steps are. The file used to carry its
# own copy of that list, which hid the fact that they disagreed: a1-rpc marked
# six steps while the task offered four, so "step 5" named nothing a student
# could look up. With the duplicate gone the panel is the only list, and it has
# to cover every marker.
import re  # noqa: E402

drift = []
for name, spec in ASSIGNMENTS.items():
    marked = {int(n) for n in re.findall(r"step (\d+)", spec.starter)}
    if marked and max(marked) > len(spec.steps):
        drift.append(f"{name}: file marks step {max(marked)}, "
                     f"panel lists {len(spec.steps)}")
assert not drift, "; ".join(drift)
print(f"ok every step a starter marks is listed in its task")

# The starters carry the code, and the panel carries the prose. A starter that
# is mostly prose is one the student has to scroll past to reach their work.
bulky = []
for name, spec in ASSIGNMENTS.items():
    lines = spec.starter.split("\n")
    comment = sum(1 for line in lines if line.strip().startswith("#"))
    code = sum(1 for line in lines
               if line.strip() and not line.strip().startswith("#"))
    if comment > code * 3:
        bulky.append(f"{name}: {comment} comment lines to {code} of code")
assert not bulky, "; ".join(bulky)
print("ok no starter is more than three-quarters commentary")

print("\nALL ASSIGNMENT TESTS PASSED")
