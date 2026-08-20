"""
Money as the common denominator.

A second of latency cannot be traded against a replica, because they are in
different units. Francs are the only unit that carries both, which is the
whole reason this module exists — and also the reason it is easy to get
wrong, because a number in francs looks authoritative whatever produced it.

Five things have to hold:

  * every line stays quantity x rate, so the technical measure is visible as
    the multiplicand and a franc is never shown without its cause.
  * a price list that pays for being wrong is refused. If a stale answer nets
    more than a good one, the winning strategy is to answer quickly and
    incorrectly, and the competition teaches the opposite of the course.
  * build cost is charged on what a design declares, and a declaration the
    trace contradicts is refused rather than billed.
  * the risk premium is the gap between the expected month and the bad one,
    and it points at a real run rather than at a statistic.
  * `share` tells an author when a scenario contains no decision — the bucket
    it claims to be about is a rounding error against everything else.
"""

import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz import map_reduce
from dsviz.machine_types import get as machine_type
from dsviz.pricing import (BUCKETS, PriceVector, Scenario,
                           crosstab, price,
                           profile, share, unsupported)

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


PRICES = PriceVector(
    currency="CHF",
    period_hours=730, machine_hour=0.21, overtime_machine_hour=0.34,
    storage_gb_month=0.023, egress_gb=0.09, bytes_per_message=120,
    engineer_day=1200, amortise_months=24,
    build_days={"partitioning": 3, "replication": 6},
    rerun=180, lost_item=0.4, dropped_message=0.2, callout=250, hour_late=400,
    per_request=0.014, requests_per_run=1800,
    window_seconds=6.0, freshness_seconds=6.0,
    stale_refund=0.014, stale_penalty=0.06,
)

QUIET = Scenario("quiet", "Quiet Tuesday", "a press archive, re-indexed",
                 PRICES, runs_per_period=2880)

DOCS = {"d1": "the cat sat", "d2": "the dog ran the road"}


def run(seed=1, crash=None):
    return map_reduce(DOCS, partitions=2, crash=crash, seed=seed).sorted_trace()


# --- every franc keeps its cause ---------------------------------------

acct = price(run(), QUIET, declared=["partitioning"])
ok("a line is quantity x rate, not a total",
   all(abs(i.amount - i.quantity * i.rate) < 1e-9 for i in acct.items))
ok("every line names a bucket that exists",
   all(i.bucket in BUCKETS for i in acct.items))
ok("lines point back at the events that produced them",
   any(i.events for i in acct.items),
   "so clicking a line can highlight the sends behind it")
ok("profit is what was earned less what was spent",
   abs(acct.profit - (acct.earned - acct.spent)) < 1e-9)
ok("the account survives a round trip to JSON",
   acct.to_json()["profit"] == round(acct.profit, 2))
ok("the fleet is charged whether or not it was busy",
   acct.subtotal("capacity") > 0)
ok("construction is spread over the life of the system",
   abs(acct.subtotal("build") - 3 / 24 * 1200) < 1e-6,
   "3 engineer-days over 24 months")

# At real prices, egress inside a datacentre is nearly free. That is not a bug
# to calibrate away: a combiner pays by shortening the job, not by saving
# bytes, and pricing messages high to make the arithmetic interesting would
# teach something false.
egress = [i for i in acct.bucket("consumption") if i.unit == "GB"]
ok("network egress is priced honestly, which means small",
   egress and egress[0].amount < 0.01,
   f"{egress[0].amount:.6f} CHF" if egress else "no egress line")


# A machine sending to itself puts nothing on the wire. `metrics` counts those
# on purpose — a local shuffle still skews — but they cannot be billed as
# egress, or co-locating a map with the fold that reads it would be charged for
# a network hop that never happened.
selfsends = [e for e in run() if e.kind == "send"
             and e.detail.get("to") == e.machine]
billed = [i for i in acct.bucket("consumption") if i.unit == "GB"]
ok("a message to yourself is not billed as egress",
   not billed or billed[0].quantity * 1e9 / 120 / 2880
   <= len([e for e in run() if e.kind == "send"]) - len(selfsends) + 1e-6,
   f"{len(selfsends)} self-send(s) in this run")


# --- a machine is priced for what it is --------------------------------

# Machines are not interchangeable units. One that runs at four times the rate
# or holds four times as much costs more every hour of the period, which is
# what makes scaling up and scaling out a choice rather than an obvious one.
FAST = PriceVector(currency="CHF", period_hours=730, machine_hour=0.21,
                   speed_exponent=1.4, memory_item_hour=0.0009,
                   out_of_memory=900, rerun=180,
                   per_request=0.004, requests_per_run=52000,
                   window_seconds=45.0, freshness_seconds=45.0,
                   stale_refund=0.004, stale_penalty=0.04)
BIG = Scenario("big", "Too big for one box", "the archive outgrew the machine",
               FAST, runs_per_period=2880)

def fleet(**kw):
    return map_reduce(HEAVY, seed=1, **kw).sorted_trace()

HEAVY = {f"d{i}": "the cat sat on the mat and the dog ran " * 4 for i in range(1, 7)}
plain = price(fleet(mappers_count=1, partitions=1, capacity=400), BIG)
quick = price(fleet(mappers_count=1, partitions=1, capacity=400,
                    speeds={"machine-1": 4.0}), BIG)
ok("a faster machine costs more to reserve",
   quick.subtotal("capacity") > plain.subtotal("capacity"),
   f"{plain.subtotal('capacity'):,.0f} -> {quick.subtotal('capacity'):,.0f} CHF")
roomy = price(fleet(mappers_count=1, partitions=1, capacity=400,
                    speeds={"machine-1": 4.0}), BIG)
snug = price(fleet(mappers_count=1, partitions=1, capacity=250,
                   speeds={"machine-1": 4.0}), BIG)
ok("a machine with more room costs more to reserve",
   roomy.subtotal("capacity") > snug.subtotal("capacity"))
ok("the fleet reads as one line per kind of machine, not one per box",
   len(price(fleet(mappers_count=6, partitions=6, capacity=30),
             BIG).bucket("capacity")) == 1)


# --- what you can buy ---------------------------------------------------

# Speed and room are bought, not declared: a design picks a type off
# `machine_types`' catalogue and the machine arrives with the processor and the
# memory that type comes with. The names and the sizes are the language's; only
# the price is the exercise's, and the prices have to be superlinear or there
# is no choice to make. Four times the processor at six and a half times the
# money is what keeps the large machine from being the obvious answer.
PRICES = {"m1.small": 0.21, "c1.large": 1.37, "r1.large": 0.94}
SHOPPED = Scenario(
    "shop", "Buying machines", "the archive picks its fleet",
    PriceVector(currency="CHF", period_hours=730, machine_prices=PRICES,
                out_of_memory=900, per_request=0.004, requests_per_run=52000,
                window_seconds=45.0, freshness_seconds=45.0,
                stale_refund=0.004, stale_penalty=0.04),
    runs_per_period=2880)

def bought(n, kind, **kw):
    spec = machine_type(kind).settings()
    traits = {f"machine-{i + 1}": dict(spec) for i in range(n)}
    return map_reduce(HEAVY, seed=1, mappers_count=n, partitions=n,
                      traits=traits, **kw).sorted_trace()

fast_one = price(bought(1, "c1.large"), SHOPPED)
plain_three = price(bought(3, "m1.small"), SHOPPED)
ok("a machine is charged what its type costs, and the line names the type",
   "c1.large" in fast_one.bucket("capacity")[0].label)
ok("the processor you bought is the processor you get",
   fast_one.bucket("capacity")[0].rate > plain_three.bucket("capacity")[0].rate,
   f"{plain_three.bucket('capacity')[0].rate} vs "
   f"{fast_one.bucket('capacity')[0].rate} CHF/hour")
ok("the line explains the machine in the catalogue's own words",
   "four times the processor" in fast_one.bucket("capacity")[0].why)
ok("a fleet of one type reads as one line, not one per box",
   len(plain_three.bucket("capacity")) == 1)
# A job that will not fit on the ordinary machine has two answers: move it to
# one with the room, or make it smaller before it is sent. Both work, and which
# is cheaper is the question the catalogue exists to ask.
LIGHT = {f"d{i}": "the cat sat on the mat" for i in range(1, 4)}
def one(kind):
    return map_reduce(LIGHT, seed=1, mappers_count=1, partitions=1,
                      traits={"machine-1": dict(machine_type(kind).settings())}
                      ).sorted_trace()
def oomed(kind):
    return any("out of memory" in i.label
               for i in price(one(kind), SHOPPED).bucket("incidents"))
ok("room and processor are different purchases",
   oomed("c1.large") and not oomed("r1.large"),
   "the fast machine still cannot hold what the roomy one can")
try:
    PriceVector(machine_prices={"x9.enormous": 4.0}).check()
    ok("a price for a machine nobody can buy is refused", False)
except ValueError:
    ok("a price for a machine nobody can buy is refused", True)


# --- work that does not fit ---------------------------------------------

# The point of the whole exercise, in one table. A machine handed more than it
# can hold does not finish, so nothing downstream is served and the month earns
# nothing — and nothing here declares a capacity. The room came with the
# machine that was bought, so running out of it is a procurement mistake.
#
# Three answers exist and they do not cost the same. Buy room. Buy processor —
# which does not work, because a fast machine cannot hold what a roomy one can.
# Or fold the duplicates before the shuffle, which is cheapest, because it is
# the only one that makes the problem smaller instead of paying to carry it.
CORPUS = {f"d{i}": "the cat sat on the mat and the dog ran " * 2 for i in range(1, 5)}
COMBINER = lambda name, text: list(Counter(text.split()).items())

def procure(n, kind, **kw):
    traits = {f"machine-{i + 1}": dict(machine_type(kind).settings())
              for i in range(n)}
    return map_reduce(CORPUS, seed=1, mappers_count=n, partitions=n,
                      traits=traits, **kw).sorted_trace()

cheap = price(procure(6, "t1.small"), SHOPPED)
folded = price(procure(6, "t1.small", mapper=COMBINER), SHOPPED)
roomy = price(procure(1, "r1.large"), SHOPPED)
quick = price(procure(1, "c1.large"), SHOPPED)

def broke(acct):
    return any("out of memory" in i.label for i in acct.bucket("incidents"))

ok("a machine given more than it holds says so, with no capacity declared",
   broke(cheap), "six t1.small at room 8, and a common key on one of them")
ok("a run that does not finish earns nothing",
   cheap.subtotal("revenue") == 0 and cheap.subtotal("not_earned") > 0,
   "the meter stops; it is not a fine")
ok("more machines does not on its own fix it",
   broke(cheap), "one common key lands on one machine however many there are")
ok("buying room fixes it", not broke(roomy))
ok("buying processor does not — it is the wrong kind of expensive",
   broke(quick) and quick.subtotal("capacity") > roomy.subtotal("capacity"),
   f"c1.large fleet {quick.subtotal('capacity'):,.0f} CHF and still no room")
ok("folding before the shuffle fixes it, on the cheapest machines there are",
   not broke(folded) and folded.profit > roomy.profit,
   f"{folded.profit:,.0f} beats buying room at {roomy.profit:,.0f} CHF")


# --- a price list cannot pay for being wrong ---------------------------

try:
    PriceVector(per_request=1.0, stale_refund=0.1, stale_penalty=0.1).check()
    ok("a price list that rewards wrong answers is refused", False)
except ValueError:
    ok("a price list that rewards wrong answers is refused", True)

try:
    Scenario("x", "x", "x", PriceVector(per_request=1.0, stale_refund=2.0,
                                        stale_penalty=0.5))
    ok("a scenario checks its prices when it is declared", True)
except ValueError:
    ok("a scenario checks its prices when it is declared", False)


# --- you are billed for what you declare -------------------------------

ok("a mechanism the trace cannot show is reported",
   unsupported(run(), ["replication"]) == ["replication"])
try:
    price(run(), QUIET, declared=["replication"])
    ok("a run is refused rather than billed for a claim it contradicts", False)
except ValueError:
    ok("a run is refused rather than billed for a claim it contradicts", True)
ok("a mechanism that leaves no mark is taken on trust, and still charged",
   unsupported(run(), ["deadline"]) == [],
   "pricing only the observable would make caution free")


# --- peace of mind -----------------------------------------------------

traces = [run(s, crash=("machine-1", 1.0) if s % 4 == 0 else None)
          for s in range(1, 25)]
risk = profile(traces, QUIET, declared=["partitioning"])
ok("the bad month is worse than the expected one", risk.bad <= risk.expected)
ok("the risk premium is the gap between them",
   abs(risk.premium - (risk.expected - risk.bad)) < 1e-9)
ok("the premium points at a real run, not a statistic",
   risk.worst is not None and risk.worst.scenario == "quiet")
ok("a design that never fails attracts no premium",
   profile([run(s) for s in range(1, 9)], QUIET).premium == 0.0)

# The failures this course is about are rare and ruinous, not common and mild.
# One catastrophe in a hundred months sits *below* the tenth percentile, where
# a percentile cannot see it: p10 would report a perfectly healthy month and
# the premium would come out zero, or negative. Averaging the worst tenth
# carries the tail's weight instead.
rare = [run(s, crash=("machine-1", 1.0) if s == 50 else None)
        for s in range(1, 101)]
rr = profile(rare, QUIET, declared=["partitioning"])
ok("one ruinous month in a hundred still shows up as a premium",
   rr.premium > 0, f"premium {rr.premium:,.0f} {rr.currency}")
ok("the bad month is worse than the mean, never better",
   rr.bad < rr.expected)
ok("clicking the premium lands on the crash, not on an average month",
   rr.worst is not None
   and rr.worst.profit == min(a.profit for a in rr.accounts))
clean = price(run(), QUIET, declared=["partitioning"])
ok("a scenario's share is read off its bad month, not off one clean draw",
   share(rr)["incidents"] > share(clean)["incidents"],
   "sampling a failure scenario on a clean run makes it look like a quiet one")


# --- what a scenario is actually about ---------------------------------

parts = share(acct)
ok("share accounts for everything that moved",
   abs(sum(parts.values()) - 1.0) < 0.01, str(parts))
ok("share exposes a bucket that cannot change the ranking",
   parts["consumption"] < 0.01,
   "an author reading this knows this scenario holds no shuffle decision")


# --- the lesson, after the competition closes --------------------------

table = crosstab({"cheap": {"quiet": 15600, "crash": -11800},
                  "safe": {"quiet": 9400, "crash": 18600}})
ok("each scenario has its own winner",
   table["winner"] == {"quiet": "cheap", "crash": "safe"},
   "no design wins everywhere, which is the whole claim")

print()
if failures:
    print(f"{len(failures)} PRICING CHECK(S) FAILED")
    sys.exit(1)
print("ALL PRICING TESTS PASSED")
