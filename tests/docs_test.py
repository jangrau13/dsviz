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
         "world = World(machines=[bank])\n\n")
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
    "word count", "wordcount", "t0-rpc", "t1-", "t2-", "t3-", "t4-",
    "chunk001", "task 0", "task 1", "task 2", "task 3", "task 4",
    "split(lower(",          # the mapper body, in one call
]
for term in FORBIDDEN:
    ok(f"no page mentions {term!r}", term not in blob)

# The reference solutions live in a sibling repository; when it is checked
# out, no line of one may appear in the documentation.
sol = pathlib.Path("..") / "spikey-dsl-sol" / "solutions"
if sol.is_dir():
    leaked = []
    for f in sorted(sol.glob("*.ds")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if len(line) < 12 or line.startswith("#"):
                continue
            if line.lower() in blob:
                leaked.append(f"{f.name}: {line}")
    ok("no line of a reference solution appears in the docs", not leaked,
       " | ".join(leaked))
else:
    print("note  reference solutions not checked out — line check skipped")

# Held-out input never travels: the docs are generated from tables that must
# not contain it either.
from dsviz.assignment import ASSIGNMENTS  # noqa: E402

# The answers, not the cluster settings: `holdout` is lines like
# "mappers 3", whose words are ordinary language vocabulary. What must never
# appear is a key the hand-in checks for.
held = {str(e.key).lower() for a in ASSIGNMENTS.values()
        for e in a.holdout_expects}
# Whole words only: a held-out key of "red" must not fail on "recomputed".
found = sorted(h for h in held
               if re.search(rf"\b{re.escape(h)}\b", blob))
ok("no held-out literal reaches the docs", not found, ", ".join(found))

print()
if failures:
    print(f"{len(failures)} DOCUMENTATION CHECK(S) FAILED")
    sys.exit(1)
print("ALL DOCUMENTATION TESTS PASSED")
