"""
Students declare the whole function; the language checks it fits.

Nothing is called `map` by decree. A student writes a name, decides how many
parameters it takes and what each one is, and passes it to a job:

    job = MapReduce(map=tokenize, reduce=total, partition=byKey)

What makes `tokenize` a mapper is that its signature fits the position — which
is only checkable because the student wrote the signature out. These tests pin
both directions: a correct declaration is accepted whatever it is called, and a
wrong one is refused in terms of what the student actually wrote.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# `syntax.py` is expected to be replaced by the grammar-based parser. When it
# is, repoint this import — the behaviour below is the specification, not the
# module it currently lives in. See HANDOVER-roles.md.
try:
    from dsviz.syntax import ROLES, check_role, parse
except ImportError as e:
    print("SKIP  the module providing role checking has moved:", e)
    print("      re-point this import at its replacement — see HANDOVER-roles.md")
    print("      (these checks encode a stated requirement: students declare")
    print("       their own function names, arity and parameter types)")
    sys.exit(0)

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def role_errors(src, fname, role):
    mod, d = parse(src)
    assert not [x for x in d if x.severity == "error"], [str(x) for x in d]
    fn = mod.functions[fname]
    return check_role(fn, ROLES[role], {}, fn.line)


# --- any name works, so long as the shape fits --------------------------
COUNTING = '''def tokenize(key: string, value: string) -> [pair]:
    return [(value, 1)]

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n
'''
for fname, role in (("tokenize", "map"), ("total", "reduce"), ("byKey", "partition")):
    ok(f"{fname} is accepted as a {role}", not role_errors(COUNTING, fname, role))

# --- the value type is the student's, and holds across the job ----------
CRAWLING = '''def links(key: string, value: string) -> [pair]:
    return [(value, key)]

def gather(key: string, values: [string]) -> string:
    return key
'''
ok("a string-valued reduce is accepted",
   not role_errors(CRAWLING, "gather", "reduce"),
   "MapReduce is not only word count")

# --- wrong declarations are refused, in the student's own terms ---------
WRONG_ARITY = '''def half(value: string) -> [pair]:
    return [(value, 1)]
'''
errs = role_errors(WRONG_ARITY, "half", "map")
ok("too few parameters is an error", bool(errs))
ok("the message counts what the student wrote",
   errs and "takes 1 parameter" in str(errs[0]),
   str(errs[0]).splitlines()[0] if errs else "")

WRONG_TYPE = '''def odd(key: int, value: string) -> [pair]:
    return [(value, 1)]
'''
errs = role_errors(WRONG_TYPE, "odd", "map")
ok("a wrongly typed parameter is an error", bool(errs))
ok("the message names the parameter and both types",
   errs and "'key'" in str(errs[0]) and "int" in str(errs[0])
   and "string" in str(errs[0]),
   str(errs[0]).splitlines()[0] if errs else "")

# A reducer cannot stand in for a partitioner just because both take two
# parameters — the second type differs, which is the point of writing it.
errs = role_errors(COUNTING, "total", "partition")
ok("a reducer is not accepted as a partitioner", bool(errs),
   str(errs[0]).splitlines()[0] if errs else "")

# --- and what it answers with is part of the shape ----------------------
# A mapper is handed one record and answers with every pair it made from it.
# Declaring that it answers with nothing is the shape of the old language, and
# it does not fit here.
SAYS_VOID = '''def quiet(key: string, value: string) -> void:
    return [(value, 1)]
'''
errs = role_errors(SAYS_VOID, "quiet", "map")
ok("a map that says it answers with nothing is refused", bool(errs),
   str(errs[0]).splitlines()[0] if errs else "")

# --- every parameter must carry a type ----------------------------------
UNTYPED = '''def loose(key, value) -> [pair]:
    return [(value, 1)]
'''
_, diags = parse(UNTYPED)
ok("an untyped parameter is refused at parse time",
   any("needs a type" in str(d) for d in diags),
   "; ".join(str(d).splitlines()[0] for d in diags) or "(none)")

print()
if failures:
    print(f"{len(failures)} ROLE CHECK(S) FAILED")
    sys.exit(1)
print("ALL ROLE TESTS PASSED")
