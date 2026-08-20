import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz import map_reduce, Lineage, spark_job, VectorClockRun

print("=== MapReduce ===")
c = map_reduce({"doc1":"the cat sat","doc2":"the dog ran","doc3":"the cat ran"},
               partitions=2, capacity=4, speeds={"machine-2":0.4})
out = sorted((e.detail["key"], e.detail["value"]) for e in c.trace.of_kind("output"))
print("output:", out)
assert dict(out)=={"the":3,"cat":2,"ran":2,"sat":1,"dog":1}, "word counts wrong"
print("straggler mapper-2 finished at:", c.machines["machine-2"].clock)

print("\n=== MapReduce with a crash ===")
c2 = map_reduce({"doc1":"the cat sat","doc2":"the dog ran"}, crash=("machine-2", 0.5))
print("drops:", len(c2.trace.of_kind("drop")), "| crash events:", len(c2.trace.of_kind("crash")))
assert c2.trace.of_kind("crash"), "crash must be recorded"

print("\n=== Spark lineage ===")
lin = Lineage()
lin.rdd("input", op="textFile")
lin.rdd("words", parents=["input"], op="flatMap")
lin.rdd("pairs", parents=["words"], op="map")
lin.rdd("counts", parents=["pairs"], op="reduceByKey")
print("stages:", lin.stages())
print("recompute counts:", lin.recompute_set("counts"))
assert lin.recompute_set("counts")==["input","words","pairs","counts"]
cs = spark_job(lin, executors=2, lose="counts")
print("spark events:", len(cs.trace), "| recompute work:",
      len([e for e in cs.trace.of_kind("work") if "recompute" in e.detail.get("label","")]))

print("\n=== Vector clocks ===")
r = VectorClockRun("P1","P2","P3")
r.event("P1","a").send("P1","P2","m1").event("P2","b").send("P2","P3","m2")
print("clocks:", r.clocks)
r.assert_clock("P3", r.clocks["P3"])   # self-consistent
try:
    r.assert_clock("P3",[9,9,9])
except AssertionError as e:
    print("\ncheckable error message:\n", e)
print("\nP1 happens-before P3?", r.happens_before("P1","P3"))
print("ALL PATTERN TESTS PASSED")
