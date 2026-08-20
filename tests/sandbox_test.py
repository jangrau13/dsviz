"""
The expression sub-language must not be an escape hatch.

`_eval_expr` used to call `eval(py, {"__builtins__": {}}, safe)`. An empty
`__builtins__` blocks name lookup but not attribute access, so a student could
reach arbitrary objects with `(1).__class__.__base__.__subclasses__()`. The
grammar gate (which forbids `.` in a NAME) was the only thing in the way — and
the CI grader reaches `_eval_expr` via `judge_assignment` -> `build_mr` ->
`run_function`, never passing through that gate. This test guards both paths.
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from dsviz.expr import _eval_expr, Budget
from dsviz.notation import NotationError
from dsviz.assignment import judge_assignment
import json


def ev(expr, env=None):
    return _eval_expr(expr, env or {}, 1, Budget())


print("=== legitimate expressions still evaluate ===")
assert ev("split(lower(value))", {"value": "The Cat SAT"}) == ["the", "cat", "sat"]
assert ev("hash(key) mod n", {"key": "cat", "n": 4}) in range(4)
assert ev("a + b * 2", {"a": 1, "b": 3}) == 7
assert ev("n > 0 and n < 10", {"n": 5}) is True
assert ev("word in split(text)", {"word": "cat", "text": "a cat"}) is True
assert ev("[1, 2, 3]") == [1, 2, 3]
assert ev("-x", {"x": 5}) == -5
assert ev("sum(values)", {"values": [1, 2, 3]}) == 6
print("ok — arithmetic, calls, comparisons, lists all work")

print("\n=== escapes are rejected, not executed ===")
ESCAPES = [
    "(1).__class__",
    "(1).__class__.__base__.__subclasses__()",
    "split.__globals__",
    "[x for x in [1, 2]]",
    "value[0]",
    "(lambda: 1)()",
    '__import__("os")',
    "{}.__class__",
    "value.upper()",          # attribute call — must go through the builtin, not the method
]
for bad in ESCAPES:
    try:
        ev(bad, {"value": "hi"})
    except NotationError:
        print(f"blocked: {bad}")
    else:
        raise AssertionError(f"NOT BLOCKED: {bad!r} evaluated instead of raising")

print("\n=== a written type is what separates a loop from an escape ===")
# The walker has no comprehension node and must never grow one. The typed form
# is run by hand in `_eval_expr` before `ast.parse` sees it, which is why
# `[x for x in [1, 2]]` above is still refused while this evaluates.
assert ev("[(c, 1) for c: string in stops]", {"stops": ["bern", "chur"]}) == [
    ("bern", 1), ("chur", 1)]
assert ev("[n * 2 for n: int in [1, 2, 3]]") == [2, 4, 6]
try:
    ev("[(1).__class__ for n: int in [1]]")
except NotationError:
    print("blocked: an escape inside a typed comprehension")
else:
    raise AssertionError("NOT BLOCKED: escape inside a comprehension")
print("ok — typed comprehensions run, untyped ones do not")

print("\n=== the CI grading path (judge_assignment -> build_mr) is also safe ===")
# A map whose pairs try to walk the object graph, written as a program a
# student could hand in: the grading path assembles the task's starter around
# it and checks the whole thing.
#
# Two independent things have to refuse this, and either alone would do: the
# grammar forbids `.` in a name, and the walker above has no Attribute node.
# The assertion is only that it never scores — which of the two catches it is
# not this test's business, and the direct escapes above are what pin the
# walker itself.
malicious = (
    "map(key, value):\n"
    "    return [((1).__class__.__base__.__subclasses__(), 1)]\n"
    "\n"
    "reduce(key, values):\n"
    "    sum(values)\n"
)
result = json.loads(judge_assignment("a1-wordcount", malicious, True))
# It must NOT crash the grader with an executed escape; it must fail as a normal
# wrong/errored submission with a verdict, never AC.
assert result["verdict"] != "AC", "malicious submission must not pass grading"
print(f"verdict for malicious submission: {result['verdict']} (not AC — good)")

print("\nALL SANDBOX TESTS PASSED")
