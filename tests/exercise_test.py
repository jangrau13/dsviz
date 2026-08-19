"""
An exercise gets its own tasks, under its own numbering.

One installed package ships every task the course has. Three exercises each
show a few of them, numbered from one, and none of them should present a task
belonging to another. That scoping is the only thing standing between "three
independent exercises" and "one dropdown with everything in it", so it is
worth a test rather than a convention.
"""

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsviz import exercise                                      # noqa: E402
from dsviz.assignment import ASSIGNMENTS                        # noqa: E402

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def make(tmp: pathlib.Path, body: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp(dir=tmp))
    (root / "pyproject.toml").write_text(body)
    return root


with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)

    # No manifest at all: this repository itself, where every task is wanted.
    bare = pathlib.Path(tempfile.mkdtemp(dir=tmp))
    ok("no manifest offers every task",
       exercise.task_names(bare) == list(ASSIGNMENTS))

    spark = make(tmp, '''
[project]
name = "assignment-2"
version = "0"

[tool.dsviz]
title = "Assignment 2"
tasks = ["a2-wordcount", "a2-telemetry", "a2-kmeans"]

[tool.dsviz.titles]
"a2-wordcount" = "Task 1: word count in Spark"
''')
    ok("an exercise offers only its own tasks",
       exercise.task_names(spark) == ["a2-wordcount", "a2-telemetry", "a2-kmeans"],
       str(exercise.task_names(spark)))
    ok("another exercise's task is not offered",
       "a1-wordcount" not in exercise.task_names(spark))
    ok("declared order is kept",
       exercise.task_names(spark)[0] == "a2-wordcount",
       "the dropdown reads in the order the exercise wrote")
    ok("an exercise can renumber a task",
       exercise.title_for(spark, "a2-wordcount", "x") == "Task 1: word count in Spark")
    ok("a task it did not rename keeps the package's title",
       exercise.title_for(spark, "a2-telemetry", ASSIGNMENTS["a2-telemetry"].title)
       == ASSIGNMENTS["a2-telemetry"].title)

    # A name the package no longer has must not take the editor down with it;
    # it is dropped, and `dsviz tasks` is where it surfaces.
    stale = make(tmp, '''
[project]
name = "assignment-x"
version = "0"

[tool.dsviz]
tasks = ["a2-wordcount", "t99-does-not-exist"]
''')
    ok("a task that no longer exists is dropped, not raised",
       exercise.task_names(stale) == ["a2-wordcount"])
    ok("and is reported so it can be fixed",
       exercise.unknown_tasks(stale) == ["t99-does-not-exist"])

    # A broken manifest must not stop a student working.
    broken = make(tmp, "[tool.dsviz\ntasks = [")
    ok("an unparseable manifest falls back to every task",
       exercise.task_names(broken) == list(ASSIGNMENTS))

    # The titles reach the browser as JSON in a meta tag, so they have to
    # survive being serialised — an em dash or a quote in a heading included.
    quoted = make(tmp, '''
[project]
name = "a"
version = "0"

[tool.dsviz]
tasks = ["a3-vector"]

[tool.dsviz.titles]
"a3-vector" = 'Task 2: "happened before" — and what it cannot tell you'
''')
    round_trip = json.loads(json.dumps(exercise.titles(quoted)))
    ok("a heading with quotes and dashes survives the trip to the page",
       round_trip["a3-vector"].startswith('Task 2: "happened before"'),
       round_trip["a3-vector"])

    # The tabs the editor opens on must be scoped the same way the dropdown
    # is, or the scoping is cosmetic.
    from dsviz import cli

    seeded = cli.seed(spark)
    starters = sorted(n for n in seeded if n.endswith(".ds"))
    ok("the workspace opens on this exercise's tasks only",
       starters == sorted(["a2-wordcount.ds", "a2-telemetry.ds", "a2-kmeans.ds"]),
       ", ".join(starters))
    ok("a data file ships when one of this exercise's tasks names it",
       "climate.csv" in seeded and "chunk002.txt" not in seeded,
       ", ".join(sorted(n for n in seeded if not n.endswith(".ds"))))

    # A workspace saved before a rename keeps its tabs, and those tabs are
    # files a student is invited to work in. Untouched starters go; anything
    # they might have written stays, whatever it is called.
    stale = dict(seeded)
    stale["t3-spark.ds"] = seeded["a2-wordcount.ds"]     # renamed, untouched
    stale["scratch.ds"] = "def mine(x: int) -> int:\n    return x\n"
    del stale["a2-kmeans.ds"]                            # a tab they closed

    (spark / ".dsviz").mkdir(exist_ok=True)
    (spark / ".dsviz" / "workspace.json").write_text(
        json.dumps({"version": 1, "files": stale}))
    fresh = cli.load_workspace(spark)

    ok("a renamed task's untouched starter is not kept as a second tab",
       "t3-spark.ds" not in fresh, ", ".join(sorted(fresh)))
    ok("a file the student wrote is kept, whatever it is called",
       fresh.get("scratch.ds") == stale["scratch.ds"])
    ok("a task this exercise has is opened even if it was not saved",
       "a2-kmeans.ds" in fresh)

print("ALL EXERCISE TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
