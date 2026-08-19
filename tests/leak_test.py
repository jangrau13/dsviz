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
    then `emit(word, 1)` — the reference mapper, line for line.

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
    (r"emit\s*\(\s*word\b", "emitting a word as the key"),
    (r"for\s+word\b.*\bin\s+split", "iterating a document's words"),
    (r"return\s+sum\s*\(\s*values\s*\)", "the counting reducer's body"),
    (r"hash\s*\(\s*key\s*\)\s*mod\s*n", "the partitioner's body"),
    (r"abs\s*\(\s*hash\s*\(\s*key\s*\)\s*\)", "the partitioner's body"),
]

# What a student can read. `tasks/` is excluded on purpose: a task states its
# own requirement, and Task 1 saying "emit(word, 1)" is the assignment, not a
# leak. `src/` in a checkout is the student's own work.
SHIPPED = [
    (HERE / "dsviz", "*.py"),
    (HERE / "web", "*.js"),
    (HERE / "docs", "*.md"),
    (HERE / "site", "*.txt"),          # llms.txt / llms-full.txt
]
STUDENT_REPO = HERE.parent / "spikey-dsl-1" / "app"
if STUDENT_REPO.is_dir():
    SHIPPED += [(STUDENT_REPO / "dsviz", "*.py"),
                (STUDENT_REPO, "*.js"),
                (STUDENT_REPO / "docs", "*.txt")]

files = []
for root, glob in SHIPPED:
    if root.is_dir():
        files += [f for f in root.rglob(glob) if "__pycache__" not in f.parts]

ok("there are files to check", bool(files), f"{len(files)} found")

for pattern, what in ANSWERS:
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{f.relative_to(HERE.parent)}:{i}")
    ok(f"nothing shipped contains {what}", not hits, ", ".join(hits[:5]))

# The reference solutions, when the sibling repository is checked out: no
# substantial line of one may appear anywhere a student can read.
#
# Only what a solution *adds* counts. A reference solution is the task's own
# starter with the answer written into it, and the starter ships to every
# student — so lines like `world.run(job)` are in both, and comparing whole
# files flags the language's own syntax as a leaked answer. A starter's
# commented-out guidance is public too: `#   world.run(job)` is shown to
# everyone, so the uncommented form is not a secret either.
sol = HERE.parent / "spikey-dsl-sol" / "solutions"
if sol.is_dir():
    import sys as _sys
    _sys.path.insert(0, str(HERE.parent))
    from dsviz.assignment import ASSIGNMENTS

    lines = []
    for f in sorted(sol.glob("*.ds")):
        spec = ASSIGNMENTS.get(f.stem)
        public = set()
        for ln in (spec.starter if spec else "").splitlines():
            ln = ln.strip()
            public.add(ln)
            public.add(ln.lstrip("#").strip())
        for line in f.read_text().splitlines():
            line = line.strip()
            if len(line) >= 12 and not line.startswith("#") and line not in public:
                lines.append((f.name, line))
    leaked = []
    for f in files:
        text = f.read_text(errors="ignore")
        leaked += [f"{f.name}: {line}" for name, line in lines if line in text]
    ok("no line a reference solution adds is shipped", not leaked,
       " | ".join(leaked[:5]))
else:
    print("note  spikey-dsl-sol not checked out — line-for-line check skipped")

print()
if failures:
    print(f"{len(failures)} LEAK CHECK(S) FAILED")
    sys.exit(1)
print(f"ALL LEAK CHECKS PASSED — {len(files)} shipped file(s) scanned")
