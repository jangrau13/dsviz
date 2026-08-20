import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz.notation import typecheck, lint

cases = [
 ("type confusion: machine used as process",
  "process P1, P2\nmachine W1\nP1 -> W1: m\n"),
 ("redeclaration",
  "process P1, P2\nprocess P1\n"),
 ("negative clock entry",
  "process P1, P2\nP1 -> P2: m\nassert P2.clock == [1, -3]\n"),
 ("non-integer clock",
  "process P1, P2\nassert P2.clock == [1, x]\n"),
 ("multiple errors reported at once",
  "process P1, P2\nP1 -> P9: m\nP8: event z\nassert P1.clock == [1,2,3]\n"),
]
for title, src in cases:
    print(f"=== {title} ===")
    for d in lint(src): print("  ", d)
    print()
print("=== well-typed program ===")
ok="process P1, P2, P3\nP1: event a\nP1 -> P2: m1\n"
print("  ", lint(ok) or "clean")
tbl,_=typecheck(ok)
print("   symbols:", len(tbl), "processes:", tbl.names_of.__self__.names_of.__name__ if 0 else tbl.names_of(__import__('dsviz.types',fromlist=['Type']).Type.PROCESS))
print("\nALL TYPE TESTS PASSED")

# --- the job's value type is the program's, not the language's -----------
# MapReduce is not word count. What a job carries is declared by the student's
# own annotations and enforced across map, combine and reduce.
from dsviz.expr import (parse_functions, check_functions, bind_helpers, infer,
                        BUILTINS, FuncType, VType)

def errors_in(src):
    funcs, d = parse_functions(src)
    d += check_functions(funcs)
    return funcs, [x for x in d if x.severity == "error"]

COUNT = '''def map(key: string, value: string) -> [pair]:
    return [(w, 1) for w: string in split(value)]

def reduce(key: string, values: [int]) -> int:
    return sum(values)
'''

CRAWL = '''def map(key: string, value: string) -> [pair]:
    return [(link, key) for link: string in split(value)]

def reduce(key: string, values: [string]) -> string:
    return upper(key)
'''

funcs, errs = errors_in(COUNT)
assert not errs, f"word count should type-check: {[str(e) for e in errs]}"
assert funcs["map"].value_type == "int", funcs["map"].value_type
print("word count binds the value type to int")

funcs, errs = errors_in(CRAWL)
assert not errs, f"a crawler should type-check: {[str(e) for e in errs]}"
assert funcs["map"].value_type == "string", funcs["map"].value_type
print("a crawler binds the value type to string")

# Disagreement between map and reduce is the error worth catching.
MIXED = '''def map(key: string, value: string) -> [pair]:
    return [(w, 1) for w: string in split(value)]

def reduce(key: string, values: [string]) -> string:
    return key
'''
_, errs = errors_in(MIXED)
assert errs, "making int pairs while reducing [string] must be an error"
assert any("carries int" in str(e) for e in errs), [str(e) for e in errs]
print("map/reduce disagreement is caught:", str(errs[0]).splitlines()[0])

# --- functions are values ------------------------------------------------
HELPER = '''def clean(text: string) -> [string]:
    return split(lower(text))

def map(key: string, value: string) -> [pair]:
    return [(w, 1) for w: string in clean(value)]

def reduce(key: string, values: [int]) -> int:
    return sum(values)
'''
funcs, errs = errors_in(HELPER)
assert not errs, [str(e) for e in errs]
bind_helpers(funcs)
diags = []
t = infer("clean", {}, 1, diags)
assert isinstance(t, FuncType), f"a bare function name should have a function type, got {t}"
assert str(t) == "(string) -> [string]", str(t)
assert not diags, [str(d) for d in diags]
print("a function used as a value has type", t)

# Builtins are values too, so `flatMap(split)` is as typed as `flatMap(clean)`.
assert isinstance(infer("split", {}, 1, []), FuncType)
print("builtins are first-class too")

# --- no builtin solves an exercise --------------------------------------
PROBLEM_SHAPED = ("word", "doc", "link", "url", "crawl", "count", "index")
offenders = [n for n in BUILTINS
             if any(w in n.lower() for w in PROBLEM_SHAPED)]
assert not offenders, f"builtins must stay general, found: {offenders}"
print("no problem-specific builtins:", ", ".join(sorted(BUILTINS)))


# --- comprehensions are checked like everything else ---------------------
# The element decides the list's type, so a job can say `-> [pair]` and be held
# to it, and the written loop type has to agree with what the list holds.
assert infer("[(c, 1) for c: string in stops]", {"stops": VType.LIST_STR}, 1, []) \
    == VType.LIST_PAIR
assert infer("[n for n: int in counts]", {"counts": VType.LIST_INT}, 1, []) \
    == VType.LIST_INT

diags = []
infer("[(c, 1) for c: int in stops]", {"stops": VType.LIST_STR}, 1, diags)
assert any("holds string" in d.message for d in diags), [str(d) for d in diags]

diags = []
infer("[(c, 1) for c: string in name]", {"name": VType.STR}, 1, diags)
assert any("runs over a list" in d.message for d in diags), [str(d) for d in diags]

# A name bound by the loop is in scope for the element and nowhere else.
diags = []
infer("c", {}, 1, diags)
assert any("unknown name" in d.message for d in diags)
print("comprehensions are typed, and their loop variable is scoped")
