import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz import map_reduce
from dsviz.metrics import report, compare, measure

docs = {f"doc{i}": "the cat sat the dog ran the "*3 for i in range(6)}

# --- four submissions differing only in non-functional quality ---
naive     = map_reduce(docs, partitions=2)                      # baseline
combiner  = map_reduce(docs, partitions=2,
    mapper=lambda n,t: [(w,c) for w,c in __import__("collections").Counter(t.split()).items()])
skewed    = map_reduce(docs, partitions=1)                      # one reducer: no parallelism
straggler = map_reduce(docs, partitions=2, speeds={"machine-3":0.2})

subs = {"naive":naive.sorted_trace(), "combiner":combiner.sorted_trace(),
        "one-reducer":skewed.sorted_trace(), "straggler":straggler.sorted_trace()}

for name, tr in subs.items():
    print(report(tr, title=name)); print()

# correctness is identical — only the non-functional properties differ
def out(c): return {e.detail["key"]:e.detail["value"] for e in c.trace.of_kind("output")}
assert out(naive)==out(combiner)==out(skewed)==out(straggler), "answers must match"
print("all four submissions produce the SAME answer:", out(naive))

print("\n=== ranked on non-functional properties ===")
for rank,(name,score,ms) in enumerate(compare(subs, weights={"network_msgs":2.0}),1):
    print(f"{rank}. {name:<12} score {score:6.2f}   "
          f"msgs={ms['network_msgs'].value:.0f} tail={ms['tail_ratio'].value:.2f} "
          f"imbalance={ms['load_imbalance'].value:.2f}")

m_n, m_c = measure(subs["naive"]), measure(subs["combiner"])
print(f"\ncombiner cuts network traffic {m_n['network_msgs'].value:.0f} -> {m_c['network_msgs'].value:.0f}")
assert m_c["network_msgs"].value < m_n["network_msgs"].value, "combiner must reduce traffic"
assert measure(subs["straggler"])["tail_ratio"].value > m_n["tail_ratio"].value, "straggler must show tail"
print("\nALL METRIC TESTS PASSED")
