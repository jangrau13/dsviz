"""
The exercise the test suite runs against.

dsviz ships no tasks, so its own suite brings some: `tests/exercise/` is a
four-task exercise, one per dialect, in a domain no course uses. Import this
before anything that reads `ASSIGNMENTS` and the registry is filled.

The reference solution lives here rather than in the exercise, for the same
reason a real course keeps its solutions in a private repository: the exercise
is served to a browser and anything in it is visible.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXERCISE = pathlib.Path(__file__).resolve().parent / "exercise"

from dsviz import exercise as _exercise      # noqa: E402


def load() -> dict:
    """Register the fixture's tasks, and return them."""
    return _exercise.load(EXERCISE)


load()

from dsviz.assignment import ASSIGNMENTS      # noqa: E402,F401

# The graded task's three functions, deliberately named nothing like the
# positions they fill: what makes a function a mapper is being passed as one.
FUNCS = '''def perDay(key: string, value: string) -> [pair]:
    return [(branch, 1) for branch: string in split(lower(value))]

def addUp(key: string, values: [int]) -> int:
    return sum(values)

def spread(key: string, n: int) -> int:
    return hash(key) mod n
'''

WIRING = '''
job = MapReduce(map=perDay, reduce=addUp, partition=spread, partitions=2)
world.run(job)
'''

#: A complete, passing submission for `fx-takings`.
SOLUTION = FUNCS + ASSIGNMENTS["fx-takings"].starter + WIRING
