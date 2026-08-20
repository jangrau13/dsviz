"""Multi-file programs: ordering, merging, and diagnostics per file."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixture  # noqa: E402,F401  dsviz ships no tasks; this brings some
from dsviz.langserver import analyse_project
from dsviz.project import Project

FILES = {
 "helpers": "def clean(text: string) -> [string]:\n    return split(lower(text))\n",
 "main": '''use helpers

def tokenize(key: string, value: string) -> [pair]:
    return [(word, 1) for word: string in clean(value)]

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n


@machine
class Worker:
    pass

m1 = Worker(type="m1.small")
m2 = Worker(type="m1.small")

world = World(machines=[m1, m2])

job = MapReduce(map=tokenize, reduce=total, partition=byKey, partitions=2)
world.run(job)
''',
}

r = json.loads(analyse_project(FILES, "main", "fx-takings"))
assert not [d for d in r["diagnostics"] if d["severity"] == "error"], r["diagnostics"]
assert r["verdict"]["verdict"] == "AC", r["verdict"]
print("ok a helper in another file is usable from main")

# A mistake must point at the file the student is editing.
broken = dict(FILES, helpers="def clean(text: string) -> [string]:\n    return lower(text)\n")
errs = [d for d in json.loads(analyse_project(broken, "main", "fx-takings"))["diagnostics"]
        if d["severity"] == "error"]
assert errs and errs[0]["file"] == "helpers" and errs[0]["line"] == 2, errs
print("ok an error in a helper is reported against that file and line")

_, cyc = Project.of({"a": "use b\n", "b": "use a\n"}, "a").order()
assert any("circular" in d.message for d in cyc), cyc
_, missing = Project.of({"main": "use nope\n"}).order()
assert any("no file called" in d.message for d in missing), missing
print("ok cycles and missing files are diagnostics, not crashes")

print("\nALL PROJECT TESTS PASSED")
