"""
The reference must describe the language that exists.

Documentation drifts silently: it is not executed, so nothing notices when it
teaches a syntax the parser no longer accepts. The reference has advertised
untyped signatures (`map(key, value):`) in a language whose whole premise is
that students write every type, and has listed builtins that were deleted.
These checks make that a failure rather than a discovery.

If the surface syntax changes — and the parser collapse should change it —
this suite will fail. That failure is the point: it is the reminder to update
the reference in the same commit. Adjust the expected shape, but keep the two
assertions that matter: a documented signature carries its types, and an
example returns its result. See HANDOVER-roles.md.
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.expr import BUILTINS
from dsviz.langserver import BUILTIN_HELP, DOCS, hover
import json

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


# --- the language is shown as it is actually written -------------------
# A reference that advertises a signature the parser rejects is worse than no
# reference: the student types it, it fails, and they doubt the tool. Every
# multi-line `def` example is fed to the real front end.
from dsviz.langserver import syntax_check  # noqa: E402

WORLD = ("@machine\n"
         "class Ledger:\n"
         "    @duration(0.4)\n"
         "    def balance(account: string) -> int:\n"
         "        return 120\n\n"
         "bank = Ledger(speed=1.0)\n"
         # A second machine, because the reference documents a construct whose
         # whole point is two of them being asked at once. One machine could
         # not demonstrate it, and an example that cannot be checked is the
         # thing this file exists to prevent.
         "mirror = Ledger(speed=1.0)\n"
         "world = World(machines=[bank, mirror])\n\n")
DRIVER = ('\ndef story() -> void:\n'
          '    owed: int = bank.balance("savings")\n\n'
          "job = Calls(run=story)\nworld.run(job)\n")

for d in DOCS:
    if not d.example.startswith("def ") or "\n" not in d.example:
        continue
    errs = syntax_check(WORLD + d.example + "\n" + DRIVER)
    ok(f"{d.name}'s example is syntax the parser accepts", not errs,
       "; ".join(str(e) for e in errs))

# A function example that leaves its value lying around teaches the wrong
# language: this is Python-shaped, so a result is returned.
for d in DOCS:
    if d.example.startswith("def ") and "-> void" not in d.example.splitlines()[0]:
        ok(f"{d.name}'s example returns its result", "return " in d.example,
           d.example.replace("\n", " ⏎ "))

# --- documentation matches the builtins that exist ----------------------
documented = set(BUILTIN_HELP)
actual = set(BUILTINS)
ok("no builtin is undocumented", not (actual - documented),
   ", ".join(sorted(actual - documented)))
ok("no documentation for a removed builtin", not (documented - actual),
   ", ".join(sorted(documented - actual)))

# Every builtin hover carries all four parts, or it is not an explanation.
for name in sorted(BUILTINS):
    h = json.loads(hover(name))
    missing = [k for k in ("signature", "summary", "detail", "example")
               if not h.get(k)]
    ok(f"hover for {name} is complete", not missing, ", ".join(missing))
    if h.get("signature"):
        ok(f"{name}'s signature names its types",
           "->" in h["signature"], h["signature"])


# --- the reference documents the language, and only the language --------
#
# Two failure modes, both of which have happened here. The reference once
# demonstrated `def` with the exercise's own mapper — `split(lower(value))`
# then `emit(word, 1)` — which is the reference solution's body line for
# line, published as a documentation example. And the site once generated a
# page per task, importing `dsviz.assignment`, the one module that holds
# held-out data. Both are closed below and must stay closed.
import tempfile  # noqa: E402

import docs as docs_module  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    written = docs_module.write_site(tmp)
    pages = {pathlib.Path(p).relative_to(tmp).as_posix(): pathlib.Path(p).read_text()
             for p in written}

ok("the site has no per-task pages",
   not [p for p in pages if p.startswith("tasks/")],
   ", ".join(p for p in pages if p.startswith("tasks/")))

imports = re.findall(r"^\s*(?:from|import)\s+\S*assignment\S*",
                     pathlib.Path("docs.py").read_text(), re.M)
ok("docs.py does not import the module holding the answers", not imports,
   "; ".join(i.strip() for i in imports))

blob = "\n".join(pages.values()).lower()

# Named exercises, and the shape of the work they ask for.
# `combine=` is deliberately absent: it is a parameter of the MapReduce job,
# so it has to be documented or it cannot be used. What must stay out is the
# reasoning about when it pays off, which is a task's question to ask.
FORBIDDEN = [
    "word count", "wordcount", "a1-rpc", "t1-", "t2-", "t3-", "t4-",
    "t5-", "t6-", "t7-", "t8-", "t9-",
    "chunk001", "task 0", "task 1", "task 2", "task 3", "task 4",
    "split(lower(",          # the mapper body, in one call
]
for term in FORBIDDEN:
    ok(f"no page mentions {term!r}", term not in blob)

from dsviz.assignment import ASSIGNMENTS  # noqa: E402

# No line a solution adds may appear in the documentation.
#
# Checked against `tests/exercise/` rather than a real course, for the same
# reason as in leak_test: this tests that the check works, and the language
# repository should not need a sibling checkout to be green.
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

leaked = [line for line in solution_adds(
    fixture.ASSIGNMENTS["fx-takings"].starter, fixture.SOLUTION)
    if line.lower() in blob]
ok("no line a solution adds appears in the docs", not leaked,
   " | ".join(leaked))

# Held-out input never travels: the docs are generated from tables that must
# not contain it either.

# The answers, not the cluster settings: `holdout` is lines like
# "mappers 3", whose words are ordinary language vocabulary. What must never
# appear is a key the hand-in checks for.
held = {str(e.key).lower() for a in ASSIGNMENTS.values()
        for e in a.holdout_expects}
# Whole words only: a held-out key of "red" must not fail on "recomputed".
found = sorted(h for h in held
               if re.search(rf"\b{re.escape(h)}\b", blob))
ok("no held-out literal reaches the docs", not found, ", ".join(found))

# The grading repository keeps the whole reference as one file, so an examiner
# has the language to hand mid-viva. It said it was generated long before
# anything generated it, and drifted into documenting `def map(...)` — syntax
# the engine now refuses. It comes off the same tables as everything else.
import importlib.util  # noqa: E402
import tempfile  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "dsviz_docs", pathlib.Path(__file__).resolve().parents[1] / "docs.py")
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)

with tempfile.TemporaryDirectory() as tmp:
    at = pathlib.Path(tmp) / "LANGUAGE.md"
    docs.write_single(str(at))
    single = at.read_text()
absent = sorted(n for n in BUILTINS if f"`{n}`" not in single)
ok("the one-file reference covers every builtin", not absent, ", ".join(absent))
ok("and does not carry the syntax the engine refuses",
   "def map(" not in single and "def reduce(" not in single)

# The built site ships inside the wheel, which means it is a committed
# artefact and can fall behind the tables it was built from. Every documented
# symbol has to appear in the built HTML; a language entry added without a
# rebuild fails here rather than reaching a student as a missing page.
from dsviz import assets  # noqa: E402

site = assets.site_dir()
if not site.is_dir():
    ok("the documentation site is built", False,
       "run: python docs.py --site docs && mkdocs build")
else:
    built = "\n".join(p.read_text(errors="ignore")
                       for p in site.rglob("*.html"))
    # By signature, not by name: an entry is rendered under the signature it
    # declares, and some of those never mention the entry's own name —
    # `update` is written `field: type = expression`. Checking the name here
    # reported a stale site that was not stale.
    #
    # The longest line of the signature, with `<` and `>` escaped and quotes
    # left alone — which is exactly what a code span does to it. A two-line
    # signature like `@kind` / `class Name:` is never one string on the page,
    # hence the longest line rather than the whole thing.
    import html as _html

    def rendered(sig: str) -> str:
        return _html.escape(max(sig.split("\n"), key=len), quote=False)

    stale = sorted(d.name for d in DOCS if rendered(d.signature) not in built)
    ok("the built site covers every documented symbol", not stale,
       ", ".join(stale) + " — rebuild with: python docs.py --site docs "
       "&& mkdocs build" if stale else "")
    missing_builtins = sorted(n for n in BUILTINS if f"{n}(" not in built)
    ok("and every builtin", not missing_builtins, ", ".join(missing_builtins))

print()
if failures:
    print(f"{len(failures)} DOCUMENTATION CHECK(S) FAILED")
    sys.exit(1)
print("ALL DOCUMENTATION TESTS PASSED")
