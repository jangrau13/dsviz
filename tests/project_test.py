"""Multi-file programs: ordering, merging, and diagnostics per file."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dsviz.langserver import analyse_project
from dsviz.project import Project

FILES = {
 "helpers": "def clean(text: string) -> [string]:\n    return split(lower(text))\n",
 "main": '''use helpers

def tokenize(key: string, value: string) -> void:
    for word: string in clean(value):
        emit(word, 1)

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n


@mapper
class Worker:
    pass

@reducer
class Collector:
    pass

m1 = Worker(speed=1.0)
m2 = Worker(speed=1.0)
r1 = Collector(speed=1.0)
r2 = Collector(speed=1.0)

world = World(machines=[m1, m2, r1, r2])

job = MapReduce(map=tokenize, reduce=total, partition=byKey)
world.run(job)
''',
}

r = json.loads(analyse_project(FILES, "main", "a1-wordcount"))
assert not [d for d in r["diagnostics"] if d["severity"] == "error"], r["diagnostics"]
assert r["verdict"]["verdict"] == "AC", r["verdict"]
print("ok a helper in another file is usable from main")

# A mistake must point at the file the student is editing.
broken = dict(FILES, helpers="def clean(text: string) -> [string]:\n    return lower(text)\n")
errs = [d for d in json.loads(analyse_project(broken, "main", "a1-wordcount"))["diagnostics"]
        if d["severity"] == "error"]
assert errs and errs[0]["file"] == "helpers" and errs[0]["line"] == 2, errs
print("ok an error in a helper is reported against that file and line")

_, cyc = Project.of({"a": "use b\n", "b": "use a\n"}, "a").order()
assert any("circular" in d.message for d in cyc), cyc
_, missing = Project.of({"main": "use nope\n"}).order()
assert any("no file called" in d.message for d in missing), missing
print("ok cycles and missing files are diagnostics, not crashes")

print("\nALL PROJECT TESTS PASSED")
