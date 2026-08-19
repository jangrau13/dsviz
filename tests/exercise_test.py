"""
An exercise is a checkout with a `tasks.py` in it.

dsviz ships no tasks. What this checks is the loader: that an exercise's own
manifest decides which tasks exist, in what order, under what headings — and
that a workspace saved before that list changed is brought up to date without
taking a student's work with it.
"""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fixture                                              # noqa: E402
from dsviz import cli, exercise                             # noqa: E402

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


FIXTURE = fixture.EXERCISE

# --- the manifest is the list -------------------------------------------
tasks = exercise.load(FIXTURE)
ok("an exercise's tasks are its own tasks.py",
   list(tasks) == ["fx-takings", "fx-busiest", "fx-calls", "fx-ticks",
                   "fx-stages"], ", ".join(tasks))
ok("in the order that file lists them",
   list(tasks)[0] == "fx-takings" and list(tasks)[-1] == "fx-stages")
ok("under the headings it writes",
   exercise.titles(FIXTURE)["fx-calls"] == "Fixture: one machine asking another",
   exercise.titles(FIXTURE)["fx-calls"])
ok("and the exercise has a name of its own",
   exercise.title(FIXTURE) == "Fixture — one task per dialect",
   exercise.title(FIXTURE))

# A checkout with no manifest has no tasks, and says so by being empty rather
# than by raising: the editor reports an empty dropdown, not a crash.
with tempfile.TemporaryDirectory() as tmp:
    bare = pathlib.Path(tmp)
    ok("a checkout with no tasks.py has no tasks",
       exercise.task_names(bare) == [] and exercise.title(bare) == "")

exercise.load(FIXTURE)      # the empty checkout above cleared the registry

# --- the workspace opens on this exercise's tasks ------------------------
seeded = cli.seed(FIXTURE)
starters = sorted(n for n in seeded if n.endswith(".ds"))
ok("the workspace opens on this exercise's tasks only",
   starters == sorted(f"{n}.ds" for n in tasks), ", ".join(starters))
ok("a data file ships when one of this exercise's tasks names it",
   "readings.txt" in seeded,
   ", ".join(sorted(n for n in seeded if not n.endswith(".ds"))))

# --- a saved workspace is brought up to date -----------------------------
# Tasks get renamed and exercises get rescoped. A workspace saved before
# either keeps its tabs, and those tabs are files a student is invited to work
# in. Untouched starters go; anything they might have written stays.
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "tasks").mkdir()
    for name, text in seeded.items():
        (root / "tasks" / name).write_text(text)
    (FIXTURE / "tasks.py").read_text()
    (root / "tasks.py").write_text((FIXTURE / "tasks.py").read_text())

    stale = dict(seeded)
    stale["fx-old-name.ds"] = seeded["fx-calls.ds"]      # renamed, untouched
    stale["scratch.ds"] = "def mine(x: int) -> int:\n    return x\n"
    del stale["fx-ticks.ds"]                            # a tab they closed

    (root / ".dsviz").mkdir()
    (root / ".dsviz" / "workspace.json").write_text(
        json.dumps({"version": 1, "files": stale}))
    fresh = cli.load_workspace(root)

    ok("a renamed task's untouched starter is not kept as a second tab",
       "fx-old-name.ds" not in fresh, ", ".join(sorted(fresh)))
    ok("a file the student wrote is kept, whatever it is called",
       fresh.get("scratch.ds") == stale["scratch.ds"])
    ok("a task this exercise has is opened even if it was not saved",
       "fx-ticks.ds" in fresh)

print("ALL EXERCISE TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
