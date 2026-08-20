"""
Nothing a student can read may contain the answer to a task.

This is not the documentation check next door — that one guards the site.
This one guards *everything shipped*: the modules Pyodide loads, the page
scripts, the built docs. All of it is readable in a browser tab, so a helpful
example in a docstring is published teaching material whether or not anyone
meant it that way.

Real leaks this test was written after finding:

  * `expr.py`'s module docstring opened with the whole of Task 1 — map,
    reduce and partition, bodies included.
  * the `sum` builtin's help said "the body of a counting reducer" and gave
    `return sum(values)`; `hash`'s said "this is what makes a partitioner
    work" and gave `return hash(key) mod n`. Both appeared as editor hovers,
    a keystroke away from the empty function they answer.
  * the `def` entry demonstrated typed signatures using `split(lower(value))`
    then the pair it makes — the reference mapper, line for line.

The rule that follows: an example in dsviz is written in a domain no task
uses. Sensor readings and station names, never words in a document.
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


# The bodies of the functions the tasks ask for. Not the builtins themselves —
# `sum` has to be documented — but the shapes that only occur in an answer.
ANSWERS = [
    (r"split\s*\(\s*lower\s*\(", "the mapper's normalise-then-split"),
    # The shape of the pair each mapper makes. A pattern here has to match the
    # answer as it is written, or it passes by matching nothing — which is how
    # a guard like this dies without failing.
    (r"\(\s*word\s*,\s*1\s*\)", "the counting mapper's pair"),
    (r"\(\s*word\s*,\s*key\s*\)", "the index mapper's pair"),
    (r"for\s+word\b.*\bin\s+split", "iterating a document's words"),
    (r"return\s+sum\s*\(\s*values\s*\)", "the counting reducer's body"),
    (r"hash\s*\(\s*key\s*\)\s*mod\s*n", "the partitioner's body"),
    (r"abs\s*\(\s*hash\s*\(\s*key\s*\)\s*\)", "the partitioner's body"),
]

# What a student can read. `tasks/` is excluded on purpose: a task states its
# own requirement, and Task 1 saying what a pair should carry is the
# assignment, not a leak. `src/` in a checkout is the student's own work.
SHIPPED = [
    (HERE / "dsviz", "*.py"),
    (HERE / "web", "*.js"),
    (HERE / "docs", "*.md"),
    (HERE / "site", "*.txt"),          # llms.txt / llms-full.txt
]
# There was a second list here, for a vendored copy of all of this in the
# student repository. The vendoring is gone: the wheel copies `dsviz/`, `web/`
# and `site/` inside the package verbatim, and all three are checked above, so
# the source is the whole shipped surface. It was a path that no longer existed
# guarded by `is_dir()`, which meant it silently checked nothing.

files = []
by_root = {}
for root, glob in SHIPPED:
    if root.is_dir():
        found = [f for f in root.rglob(glob) if "__pycache__" not in f.parts]
        by_root[f"{root.name}/{glob}"] = len(found)
        files += found

ok("there are files to check", bool(files), f"{len(files)} found")

# A leak check gets easier to pass as there is less to scan, and it says
# nothing when that happens: it reported 100 files one morning and 39 the same
# afternoon, passed both times, and the drop was only noticed by eye. Most of
# that fall was deliberate — the tasks moved out to the exercises — but a
# deliberate fall and an accidental one look identical from in here.
#
# So the count is pinned. Two ways, because a single total can be held up by
# one fat directory while another quietly empties:
FLOOR = 30
ok(f"at least {FLOOR} shipped files are scanned", len(files) >= FLOOR,
   f"{len(files)} — either something stopped shipping or SHIPPED stopped "
   f"finding it; lower the floor in the commit that removes the files"
   if len(files) < FLOOR else f"{len(files)}")
for where, n in sorted(by_root.items()):
    ok(f"{where} still has files to scan", n > 0, f"{n}")

for pattern, what in ANSWERS:
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{f.relative_to(HERE.parent)}:{i}")
    ok(f"nothing shipped contains {what}", not hits, ", ".join(hits[:5]))

# No substantial line of a reference solution may appear anywhere a student
# can read.
#
# The subject is `tests/exercise/`, not a real course. That is deliberate: this
# checks the *check* — that an answer written into a starter is recognised as
# an answer — and it does so without the language repository going looking for
# a sibling checkout it has no business knowing about. Whether a particular
# course's answers leak is that course's question, and belongs beside that
# course's solutions.
import fixture                                          # noqa: E402

def solution_adds(starter: str, solution: str) -> list:
    """The substantial lines a solution adds to the starter it began as.

    A reference solution is the task's own starter with the answer written
    into it, so most of its lines ship to every student and are not secrets:
    `world.run(job)` is in both. Comparing whole files flagged the language's
    own syntax as a leaked answer, which is noise, and noise is worse than no
    check. The answer is exactly the part the starter does not contain.

    Commented-out guidance counts as public too — a starter that says
    `#   world.run(job)` has shown everyone the uncommented form.
    """
    public = set()
    for line in starter.splitlines():
        line = line.strip()
        public.add(line)
        public.add(line.lstrip("#").strip())
    return [line.strip() for line in solution.splitlines()
            if len(line.strip()) >= 12
            and not line.strip().startswith("#")
            and line.strip() not in public]

adds = solution_adds(fixture.ASSIGNMENTS["fx-takings"].starter, fixture.SOLUTION)
ok("the fixture solution actually adds something to check", bool(adds),
   f"{len(adds)} line(s)")

leaked = []
for f in files:
    text = f.read_text(errors="ignore")
    leaked += [f"{f.name}: {line}" for line in adds if line in text]
ok("no line a reference solution adds is shipped", not leaked,
   " | ".join(leaked[:5]))

print()
if failures:
    print(f"{len(failures)} LEAK CHECK(S) FAILED")
    sys.exit(1)
print(f"ALL LEAK CHECKS PASSED — {len(files)} shipped file(s) scanned")
