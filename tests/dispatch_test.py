"""
A submission must be parsed by the builder its dialect names.

`Assignment.judge` had two paths that chose a builder independently: the
exploration path branched on `self.dialect`, and the graded path did not branch
at all — it always called `build_mr`. The bug was invisible because every graded
task happens to be MapReduce today, so it would have surfaced the moment a graded
RPC or Spark task was added. Both paths now go through `build_cluster`, and these
tests pin that it routes by dialect rather than by luck.
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from dsviz.assignment import build_cluster
from dsviz.core import Cluster
from dsviz.notation import NotationError

RPC = """@machine
class Ledger:
    @duration(0.5)
    def balance(account: string) -> int:
        return 1

bank = Ledger(speed=1.0)

@machine
class App:
    def main() -> void:
        owed: int = bank.balance("savings")

app = App()

world = World(machines=[bank, app])
"""

MAPREDUCE = """split doc1: "the cat sat"

def map(key: string, value: string) -> void:
    for word: string in split(value):
        emit(word, 1)

def reduce(key: string, values: [int]) -> int:
    sum(values)
"""

print("=== each dialect builds through its own builder ===")

rpc_cluster = build_cluster("rpc", RPC)
assert isinstance(rpc_cluster, Cluster), f"rpc gave {type(rpc_cluster).__name__}"
assert rpc_cluster.trace.of_kind("rpc"), "an rpc program should produce rpc events"
print(f"ok   rpc      -> Cluster with {len(rpc_cluster.trace.of_kind('rpc'))} rpc event(s)")

mr_cluster = build_cluster("mapreduce", MAPREDUCE)
assert isinstance(mr_cluster, Cluster), f"mapreduce gave {type(mr_cluster).__name__}"
assert mr_cluster.trace.of_kind("output"), "a mapreduce program should emit output"
print(f"ok   mapreduce-> Cluster with {len(mr_cluster.trace.of_kind('output'))} output(s)")

# The regression itself: an RPC program must NOT be handed to the MapReduce
# builder. Before the fix the graded path did exactly that, and this is what it
# looked like — the MR parser cannot read `service`/`client` lines.
print("\n=== the old behaviour is genuinely broken, so the fix is load-bearing ===")
try:
    build_cluster("mapreduce", RPC)
except NotationError as e:
    print(f"ok   rpc source through the mr builder fails: {e.diagnostics[0].message[:60]}")
else:
    raise AssertionError(
        "an RPC program parsed cleanly as MapReduce — this test can no longer "
        "detect the dispatch bug it was written for")

print("\n=== an unknown dialect falls back to mapreduce, not a crash ===")
assert isinstance(build_cluster("", MAPREDUCE), Cluster)
print("ok   unknown dialect -> mapreduce")

print("\nALL DISPATCH TESTS PASSED")
