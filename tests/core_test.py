import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz.core import Cluster

c = Cluster()
fast = c.machine("fast", speed=1.0, role="mapper")
slow = c.machine("slow", speed=0.25, role="mapper")   # straggler
red  = c.machine("red0", capacity=2, role="reducer")

fast.work("map", 2.0)
slow.work("map", 2.0)
print("fast clock:", fast.clock, "| slow clock:", slow.clock)
assert fast.clock == 2.0 and slow.clock == 8.0, "speed must scale duration"

fast.send(red, ("the",1))
slow.send(red, ("cat",1))
red.hold(("dog",1))
print("red items:", red.items, "overloaded:", red.is_overloaded)
assert red.is_overloaded, "capacity 2 with 3 items should be overloaded"

# crash while a message is in flight
victim = c.machine("victim")
fast.send(victim, "payload", latency=5.0)   # arrives at fast.clock+5
victim.crash(at=fast.clock + 1.0)           # dies before it lands

tr = c.sorted_trace()
kinds = [e.kind for e in tr]
print("\nkinds:", sorted(set(kinds)))
drops = tr.of_kind("drop")
print("drops:", drops)
assert drops, "message in flight to a crashed machine must drop"

print("\n--- trace ---")
for e in tr: print(e)
print("\nduration:", tr.duration, "machines:", tr.machines())
print("\nALL CORE ASSERTIONS PASSED")
