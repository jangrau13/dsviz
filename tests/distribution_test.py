"""
A hundred runs must answer a question one run cannot.

Three things have to hold for that to be true, and each one was broken:

  1. The runs have to reach the *right* builder. `evaluate` always called
     `runtime.build`, so a finished MapReduce submission produced five spawn
     events and a distribution of zeros — green on nothing, in the shape of
     evidence.
  2. A seeded run has to replay. `_breaks` drew from the module-level
     `random`, so seeding the cluster changed nothing and "seeded by index"
     was decoration.
  3. A program with nothing random in it has to say so, rather than report a
     hundred identical runs as though agreement between them meant anything.
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from dsviz.runtime import evaluate

UNRELIABLE = """@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120

bank = Ledger(speed=1.0, error_rate=0.4)
world = World(machines=[bank])

def story() -> void:
    owed: int = bank.balance("savings")

client = Calls(run=story, times=4)
world.run(client)
"""

RELIABLE = UNRELIABLE.replace("error_rate=0.4", "error_rate=0.0")

MAPREDUCE = """split doc1: "the cat sat on the mat"

@mapper
class W:
    pass

@reducer
class C:
    pass

m1 = W(speed=1.0)
r1 = C(speed=1.0)
world = World(machines=[m1, r1])

def tok(key: string, value: string) -> void:
    for word: string in split(value):
        emit(lower(word), 1)

def total(key: string, values: [int]) -> int:
    return sum(values)

def owner(key: string, reducers: int) -> int:
    return hash(key) mod reducers

job = MapReduce(map=tok, reduce=total, partition=owner)
world.run(job)
"""

print("=== an unreliable program produces a spread, not one story ===")
r = evaluate(UNRELIABLE, runs=100)
assert r["runs"] == 100, r["runs"]
assert not r["deterministic"]
span = r["metrics"]["makespan"]
assert span["n"] == 100, span["n"]
assert span["max"] > span["min"], "every run agreed — the failures never fired"
assert span["min"] <= span["p50"] <= span["p95"] <= span["max"]
print(f"ok   makespan min={span['min']:.1f} p50={span['p50']:.1f} "
      f"p95={span['p95']:.1f} max={span['max']:.1f}")

print("\n=== the same hundred runs, twice, give the same answer ===")
assert evaluate(UNRELIABLE, runs=100) == r, (
    "seeding does not reach the failure draw, so the spread cannot be reproduced")
print("ok   identical across two calls")

print("\n=== nothing random in it: run it once, and say why ===")
flat = evaluate(RELIABLE, runs=100)
assert flat["deterministic"], "a program with no error_rate reported as varying"
assert flat["runs"] == 1 and flat["asked"] == 100, (flat["runs"], flat["asked"])
print("ok   runs=1, asked=100, deterministic=True")

print("\n=== a mapreduce submission is run by the mapreduce builder ===")
mr = evaluate(MAPREDUCE, runs=100)
makespan = mr["metrics"]["makespan"]
assert makespan["p50"] > 0, (
    "a finished MapReduce job measured zero — evaluate is running it through "
    "the machine builder again")
assert mr["deterministic"] and mr["runs"] == 1
print(f"ok   makespan={makespan['p50']:.1f}, run once because nothing can fail")


# --- what a machine does about breaking ---------------------------------
#
# `error_rate` alone describes half of a failure. These pin the other half:
# that a machine says what happens next, that saying it changes the outcome,
# and that every dialect can break — MapReduce and Spark could not fail at
# all however they were written, because the failure draw sat inside the RPC
# round trip and their machines never make one.

from dsviz.assignment import build_cluster
from dsviz.syntax import lint

FLAKY = """@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120

bank = Ledger(speed=1.0, error_rate=0.5, on_crash=%s, restart_after=0.5)
world = World(machines=[bank])

def story() -> void:
    owed: int = bank.balance("savings", retries=3)

client = Calls(run=story, times=1)
world.run(client)
"""


def outcomes(source, n=40):
    got = {}
    for seed in range(n):
        c = build_cluster("rpc", source, seed=seed)
        status = [e.detail.get("status") for e in c.trace if e.kind == "rpc"]
        last = status[-1] if status else "none"
        got[last] = got.get(last, 0) + 1
    return got

print("\n=== retries help a machine that comes back, and only that one ===")
dead = outcomes(FLAKY % '"stay_dead"')
back = outcomes(FLAKY % '"restart"')
assert back.get("ok", 0) > dead.get("ok", 0), (
    f"restarting made no difference: stay_dead={dead} restart={back}")
print(f"ok   stay_dead {dead.get('ok', 0)}/40 succeed, "
      f"restart {back.get('ok', 0)}/40 — same rate, same retries")

MR_FLAKY = MAPREDUCE.replace(
    "m1 = W(speed=1.0)",
    'm1 = W(speed=1.0, error_rate=0.5, on_crash=%s, restart_after=1.0)')

print("\n=== a mapreduce job can fail at all, and its answer depends on this ===")
complete = incomplete = 0
for seed in range(30):
    c = build_cluster("mapreduce", MR_FLAKY % '"stay_dead"', seed=seed)
    outs = len([e for e in c.trace if e.kind == "output"])
    if outs < 5:
        incomplete += 1
    else:
        complete += 1
assert incomplete, (
    "a mapper with error_rate=0.5 never lost any output — the failure draw is "
    "not reaching the machines that do the mapping")
print(f"ok   a mapper that stays dead loses counts in {incomplete}/30 runs")

# The same mapper, told to come back, gets its splits again. It can still
# exhaust its attempts and die for good — that is the point of a *rate* — so
# the claim is that restarting saves the answer more often, not always.
restarted = sum(
    len([e for e in build_cluster("mapreduce", MR_FLAKY % '"restart"',
                                  seed=s).trace if e.kind == "output"]) >= 5
    for s in range(30))
assert restarted > complete, (
    f"restarting rescued no more runs than staying dead "
    f"({restarted} vs {complete} of 30) — its splits are not being re-run")
print(f"ok   a mapper that restarts re-runs its splits: {restarted}/30 runs "
      f"complete, against {complete}/30 when it stays dead")

print("\n=== the spread now covers every dialect ===")
spread = evaluate(MR_FLAKY % '"stay_dead"', runs=100)
assert not spread["deterministic"] and spread["runs"] == 100
assert spread["metrics"]["makespan"]["max"] > spread["metrics"]["makespan"]["min"]
assert evaluate(MR_FLAKY % '"stay_dead"', runs=100) == spread, "not reproducible"
print(f"ok   mapreduce makespan p50={spread['metrics']['makespan']['p50']:.1f} "
      f"max={spread['metrics']['makespan']['max']:.1f}, and it replays")

print("\n=== a crash behaviour that is not one is refused ===")
bad = lint(FLAKY % '"restarts"')[1]
assert any("crashing" in d.message for d in bad), [d.message for d in bad]
print(f"ok   {[d.message for d in bad if 'crashing' in d.message][0]}")


# --- a crash written in a story happens where it is written ---------------
#
# `bank.restart()` reads as "and now bring it back", so it has to land after
# whatever precedes it. It used to be stamped on the *target's* clock, and a
# crashed machine's clock is frozen at the moment it died — so a restart
# written after two calls was recorded before them, and the trace came out
# non-monotonic. Task 0 walks students through exactly this, in this order.

STORY = """@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120

@machine
class Rates:
    @duration(0.9)
    def to_euros(amount: int) -> int:
        return amount * 2

bank = Ledger(speed=1.0)
fx = Rates(speed=1.0)
world = World(machines=[bank, fx])

def story() -> void:
    bank.crash()
    chf: int = bank.balance("savings")
    eur: int = fx.to_euros(chf)
    bank.restart()
    again: int = bank.balance("savings")

client = Calls(run=story, times=1)
world.run(client)
"""

print("\n=== crash and restart land where the story puts them ===")
from dsviz.runtime import build

trace = build(STORY, seed=0).trace
times = [e.t for e in trace]
assert times == sorted(times), (
    "the trace runs backwards: " +
    ", ".join(f"{e.kind}@{e.t:.2f}" for e in trace))

restart = next(e for e in trace if e.kind == "restart")
before = [e.t for e in trace if e.kind == "rpc" and e.t < restart.t]
assert before and restart.t >= max(before), (
    f"restart at {restart.t} was recorded before calls it comes after")
after = [e for e in trace if e.kind == "rpc" and e.t > restart.t]
assert any(e.detail.get("status") == "ok" for e in after), (
    "the call after the restart did not succeed — bringing it back did nothing")
print(f"ok   crash@{next(e.t for e in trace if e.kind == 'crash'):.2f} "
      f"→ unavailable → restart@{restart.t:.2f} → ok, in that order")

print("\nALL DISTRIBUTION AND CRASH-BEHAVIOUR TESTS PASSED")
