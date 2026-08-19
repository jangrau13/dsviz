import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz import map_reduce
from dsviz.patterns import normalize_inputs
from dsviz.shapes import dataflow, gantt

print("=== input forms ===")
print("dict   :", list(normalize_inputs({"a":"x y"}).items()))
print("list   :", list(normalize_inputs(["the cat","the dog"]).items()))
print("pairs  :", list(normalize_inputs([("d1","a b"),("d2","c")]).items()))
print("string :", list(normalize_inputs("just one split").items()))
open("/tmp/in.txt","w").write("line one here\nline two here\n")
print("file   :", list(normalize_inputs("/tmp/in.txt").items()))

print("\n=== scale: 20 mappers, 7 partitions ===")
docs = [f"word{i%9} common shared{i%4}" for i in range(20)]
c = map_reduce(docs, partitions=7)
out = {e.detail["key"]: e.detail["value"] for e in c.trace.of_kind("output")}
print("machines:", len(c.machines), "| distinct keys:", len(out))
print("total counted:", sum(out.values()), "| expected:", sum(len(d.split()) for d in docs))
assert sum(out.values())==sum(len(d.split()) for d in docs), "lost pairs at scale"

print("\n=== fewer mappers than splits (round-robin) ===")
c2 = map_reduce(docs, partitions=3, mappers_count=4)
out2 = {e.detail["key"]: e.detail["value"] for e in c2.trace.of_kind("output")}
print("mappers:", len(c2.machines_of("mapper")), "| total:", sum(out2.values()))
assert sum(out2.values())==sum(out.values()), "round-robin must not lose pairs"

print("\n=== layout survives 20 machines ===")
tr=c.sorted_trace()
df=dataflow(tr); g=gantt(tr)
xs=[s.x for s in df if s.kind=="box"]; ys=[s.y for s in g if s.kind=="box"]
print(f"dataflow x range: {min(xs):.1f}..{max(xs):.1f}  (frame is about -7..7)")
print(f"gantt   y range: {min(ys):.1f}..{max(ys):.1f}  (frame is about -4..4)")
assert max(abs(min(xs)),abs(max(xs))) < 7.5, "dataflow ran off screen"
assert max(abs(min(ys)),abs(max(ys))) < 4.5, "gantt ran off screen"
print("\nALL FLEXIBILITY TESTS PASSED")
