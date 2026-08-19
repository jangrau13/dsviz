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

print("\n=== the CI grading path (judge_assignment -> build_mr) is also safe ===")
# A map function whose emit tries to walk the object graph. This bypasses the
# Lark gate entirely, exactly like gate/grade.py does.
malicious = (
    "map(key, value):\n"
    "    emit((1).__class__.__base__.__subclasses__(), 1)\n"
    "\n"
    "reduce(key, values):\n"
    "    sum(values)\n"
)
result = json.loads(judge_assignment("t1-wordcount", malicious, True))
# It must NOT crash the grader with an executed escape; it must fail as a normal
# wrong/errored submission with a verdict, never AC.
assert result["verdict"] != "AC", "malicious submission must not pass grading"
print(f"verdict for malicious submission: {result['verdict']} (not AC — good)")

print("\nALL SANDBOX TESTS PASSED")
