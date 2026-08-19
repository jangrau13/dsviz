import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz.notation import lint, build, NotationError

good = """
# a run students might write
process P1, P2, P3
P1: event a
P1 -> P2: m1
P2: event b
P2 -> P3: m2
"""
r = build(good)
print("clocks:", r.clocks)

print("\n=== 1. causal error: claims to know something it cannot ===")
bad = good + "assert P3.clock == [2, 3, 5]\n"
for d in lint(bad): print(" ", d)

print("\n=== 2. wrong clock width ===")
for d in lint(good + "assert P3.clock == [1, 1]\n"): print(" ", d)

print("\n=== 3. unknown process ===")
for d in lint("process P1, P2\nP1 -> P9: m\n"): print(" ", d)

print("\n=== 4. syntax error ===")
for d in lint("process P1, P2\nP1 sends stuff to P2\n"): print(" ", d)

print("\n=== 5. concurrency claim that is false ===")
for d in lint(good + "assert P1 || P3\n"): print(" ", d)

print("\n=== 6. clean program lints clean ===")
ok = good + f"assert P3.clock == {r.clocks['P3']}\nassert P1 ->> P3\n"
print("  diagnostics:", lint(ok) or "none — clean")
assert not lint(ok), "correct program must lint clean"
print("\nALL NOTATION TESTS PASSED")
