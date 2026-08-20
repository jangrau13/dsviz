"""
The language server.

One entry point the editor talks to. It owns everything the browser needs:
parse, lint, type-check, run, lay out, measure, and describe symbols for
completions and hovers.

Runs in the browser under Pyodide, so there is no backend and no round trip —
the same code that renders lecture videos also powers the editor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lark import UnexpectedInput

from .grammar import parser
from .notation import Diagnostic, NotationError

# --- dialects -----------------------------------------------------------
# One language, three exercises. Which statements a program uses tells us
# which exercise it is, so the student never declares a mode.

MAPREDUCE, SPARK, RPC, CLOCKS = "mapreduce", "spark", "rpc", "clocks"


def detect_dialect(source: str) -> str:
    """Pick the exercise from what the program actually contains."""
    import re
    # A pipeline is what makes a program a Spark program, not the absence of
    # machines: a Spark job declares executors with `@machine` exactly like
    # every other job, so testing for that first classified all three Spark
    # tasks as RPC and offered their editors the wrong completions.
    if re.search(r"\b(textFile|parallelize)\s*\(|\bSpark\s*\(", source, re.M):
        return SPARK
    # And for the same reason, a MapReduce job is what makes a program a
    # MapReduce program. Its machines are declared `@machine` like every other
    # kind, so the job has to be asked about before the machines are.
    if re.search(r"\bMapReduce\s*\(", source, re.M):
        return MAPREDUCE
    if re.search(r"^\s*@machine\b", source, re.M):
        return RPC
    if re.search(r"^\s*@process\b|\.clock\s*==|\->>", source, re.M):
        return CLOCKS
    return MAPREDUCE


def distribution(source: str, runs: int = 100) -> str:
    """
    Run a program many times and return the spread of results, as JSON.

    The editor draws one run; this answers the different question of what
    tends to happen. Nothing is rendered, so a hundred runs cost about what
    one drawing does.
    """
    from .runtime import evaluate

    try:
        return json.dumps(evaluate(source, runs=runs))
    except NotationError as err:
        return json.dumps({"error": [d.to_json() for d in err.diagnostics]})


# --- documentation ------------------------------------------------------
# The single source of truth for hovers, completions and the docs page.

@dataclass
class SymbolDoc:
    name: str
    signature: str
    summary: str
    detail: str = ""
    dialects: tuple = ()
    example: str = ""

    def to_json(self) -> dict:
        return {"name": self.name, "signature": self.signature,
                "summary": self.summary, "detail": self.detail,
                "dialects": list(self.dialects), "example": self.example}


DOCS: list[SymbolDoc] = [
    # --- describing the system ---
    SymbolDoc("class", "@kind\nclass Name:", "A kind of machine.",
              "The decorator says what the class is to the simulator. "
              "@machine both answers calls and makes them, and @process "
              "carries a clock. A machine is a machine: which half of a job "
              "it does is the job's to decide, the same way a master hands "
              "out tasks. A class is only a kind of machine. What runs is an "
              "instance of it.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@machine\nclass Ledger:\n"
              "    @duration(0.4)\n"
              "    def balance(account: string) -> int:\n"
              "        return 120"),
    SymbolDoc("instance", "name = Kind(speed=N)", "A machine that exists.",
              "Declaring a class runs nothing. Each instance carries its "
              "own settings, so two machines of one kind can differ. That is "
              "how you make a straggler.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "fast = Worker(speed=1.0)\nslow = Worker(speed=0.3)"),
    SymbolDoc("World", "world = World(machines=[...])", "The system to run in.",
              "Everything about the setting lives here and nothing about "
              "the computation, so you can run one job in a fast world and "
              "then in a broken one. Without a world, a job has nowhere to "
              "run.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "world = World(machines=[m1, m2, r1])"),
    SymbolDoc("run", "world.run(job [, on=[...]])", "Run a job in the world.",
              "`on` gives the job a subsystem instead of the whole world, "
              "so you can run one job on three machines and then on ten.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "world.run(job)\nworld.run(job, on=[m1, r1])"),
    SymbolDoc("parallel", "with parallel():",
              "Calls that all leave at the same time.",
              "Calls are one after another: each moves the caller past the "
              "whole round trip, so the next one leaves from where the last "
              "finished. Everything written inside this block leaves at the "
              "moment the block begins instead, and the block ends when the "
              "last reply is back \u2014 so asking three machines costs one "
              "round trip rather than three. The far end is unchanged, which "
              "is the half worth watching: a machine answers one request at a "
              "time, so three calls sent at once to the same machine still "
              "queue behind each other. What the block saves is the waiting "
              "on the wire, never the work \u2014 it pays because the machines "
              "are different, not because the calls were written together. "
              "No call in the block can be given what "
              "another call in it answers, because none of them has answered "
              "yet; use the value below the block.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'def story() -> void:\n'
              '    with parallel():\n'
              '        here: int = bank.balance("savings")\n'
              '        there: int = mirror.balance("savings")'),
    SymbolDoc("times", "Job(..., times=N)", "How many rounds the job runs.",
              "The job runs N times over. That is what you want in a video, "
              "and it is what makes an unreliable run worth watching, because "
              "the same call does not fail every time.",
              (MAPREDUCE, SPARK, RPC, CLOCKS), "job = Calls(run=story, times=3)"),
    SymbolDoc("error_rate", "Kind(error_rate=P)", "How likely a machine is to break.",
              "0.25 means each piece of work it does has one chance in four "
              "of breaking it. The draw is random, so no two runs are alike "
              "and one run never settles a question. Every machine runs this "
              "risk: a mapper grinding through its split is as exposed as a "
              "service answering a request. What happens after it breaks is "
              "said separately, with on_crash.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "flaky = Ledger(speed=1.0, error_rate=0.25)"),
    SymbolDoc("on_crash", 'Kind(on_crash="stay_dead" | "restart")',
              "What this machine does after it breaks.",
              "How often a machine breaks is only half of how it behaves. "
              "A machine that stays down and one that is back in two seconds "
              "fail at the same rate and behave nothing alike, because "
              "retries help the second and are wasted on the first. The "
              "default is \"stay_dead\", which leaves it down until something "
              "restarts it by hand. Coming back does not bring back what it "
              "was holding, so a restarted mapper runs its splits again.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'flaky = Ledger(error_rate=0.25, on_crash="restart", '
              'restart_after=1.5)'),
    SymbolDoc("restart_after", "Kind(restart_after=T)",
              "How long a restarting machine is down.",
              "Seconds between breaking and answering again. It means "
              'something only alongside on_crash="restart". The gap shows on '
              "the timeline as the machine sitting idle, and that idle time "
              "is what you weigh against losing the work outright.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'slow_to_recover = Worker(error_rate=0.2, on_crash="restart", '
              'restart_after=3.0)'),
    SymbolDoc("state", "field: type = value", "What a machine remembers.",
              "Written in the class body, above the methods: a name, its "
              "type, and the value it starts at. A machine without state "
              "answers the same thing however often it is asked; with it, "
              "the second call can see what the first one did. Every machine "
              "of that kind has its own — two of them never share a value — "
              "and an instance may start somewhere else by naming the field "
              "when it is made. It is drawn along the bottom of the machine "
              "on the diagram, and it changes there as the run goes on.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@machine\nclass Ledger:\n"
              "    balance: int = 120\n\n"
              "    @duration(0.4)\n"
              "    def deposit(amount: int) -> int:\n"
              "        balance: int = balance + amount\n"
              "        return balance"),
    SymbolDoc("update", "field: type = expression",
              "Change what a machine remembers.",
              "Inside a method the field is an ordinary name: it reads as "
              "what the machine currently holds, and writing to it changes "
              "the machine rather than a local that is thrown away when the "
              "call returns. Which of the two you get is decided by the class "
              "declaration and nothing else, so a parameter may not carry a "
              "field's name.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@duration(0.4)\ndef deposit(amount: int) -> int:\n"
              "    balance: int = balance + amount\n"
              "    return balance"),
    SymbolDoc("starts", "name = Kind(field=value)",
              "Start one machine somewhere else.",
              "The class says which fields exist, because that is what makes "
              "it this kind of machine; each instance may say what its own "
              "start at. That is how two machines of one kind differ in what "
              "they hold rather than only in how fast they are.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "vault = Ledger(balance=5000)\npetty = Ledger(balance=40)"),
    SymbolDoc("duration", "@duration(T)", "How long a method takes.",
              "Seconds of work at speed 1.0. A machine with speed 0.5 takes "
              "twice as long over the same method.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@duration(0.4)\ndef balance(account: string) -> int:\n"
              "    return 120"),
    SymbolDoc("type", 'Kind(type="m1.small")', "Which machine to buy.",
              "A machine is not built to order: there is a catalogue, you "
              "pick a type off it, and the machine arrives with the processor "
              "and the room that type comes with. `m1.small` is the ordinary "
              "one and what you get if you say nothing.\n\n"
              "The letter says what it is built for. `c` has the processor, "
              "for work that is slow because there is a lot of it. `r` has "
              "the room, for a machine handed more than it can hold — that "
              "one does not get better with a faster processor. `m` is the "
              "middle of both and `t` is the cheap one, which is how you make "
              "a straggler.\n\n"
              "Each type is drawn in its own colour, so a fleet of mixed "
              "machines reads as mixed at a glance.\n\n```\n  t1.small   0.3x  room 8   cheap and slow\n  m1.small     1x  room 16  the ordinary machine, and what to reach for first\n  m1.large     2x  room 32  twice the processor and twice the room\n  c1.large     4x  room 16  four times the processor, ordinary room\n  r1.large     1x  room 96  ordinary processor, six times the room\n```",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'slow = Worker(type="t1.small")'),
    SymbolDoc("crash", "machine.crash()", "Take a machine down.",
              "Everything it remembers goes back to the value it started at, "
              "whatever it had been counted up to since, and messages already "
              "in flight to it are dropped. On the diagram the machine's own "
              "values drop back at the moment it breaks, which is the cost of "
              "losing it.",
              (MAPREDUCE, SPARK, RPC), "bank.crash()"),
    SymbolDoc("restart", "machine.restart()", "Bring a machine back.",
              "It comes back as it was declared, not as it was a moment "
              "before it broke, so anything it had worked out has to be "
              "worked out again.",
              (MAPREDUCE, SPARK, RPC), "bank.restart()"),

    # --- messages between processes ---
    SymbolDoc("send", "sender.send(receiver, \"label\")",
              "One message, from one process to one other.",
              "The send happens before the receive, and that is the only "
              "ordering either process can be sure of. Both carry a logical "
              "clock, which the message advances.",
              (CLOCKS,), 'depotA.send(depotB, "restock")'),
    SymbolDoc("broadcast", "sender.broadcast(\"label\")",
              "One message, to every other process.",
              "One send, one stamp, and a copy on its way to each of the "
              "others. Delivery rules that talk about \"the next message that "
              "process sent\" are only defined over broadcast, because "
              "otherwise the next message may not have been addressed to you.",
              (CLOCKS,), 'depotA.broadcast("restock")'),
    SymbolDoc("late", "sender.broadcast(\"label\", late=who)",
              "Send one copy the slow way.",
              "Everyone else has the message at once; this process does not. "
              "Without it every arrival is in send order and nothing is ever "
              "out of place, so a delivery rule has nothing to do.",
              (CLOCKS,), 'depotA.broadcast("restock", late=depotC)'),
    SymbolDoc("clock", 'Calls(clock="vector" | "lamport")',
              "Which logical clock the processes keep.",
              "A vector clock has one entry per process and can say that two "
              "events are concurrent. A Lamport clock is a single number: it "
              "guarantees that if a happened before b then L(a) < L(b), and "
              "nothing in the other direction.",
              (CLOCKS,), 'job = Calls(run=deliveries, clock="lamport")'),
    SymbolDoc("delivery", 'Calls(delivery="causal")',
              "Hold a message until the ones it depends on have arrived.",
              "Without this a message is shown when it arrives, however out "
              "of order that is. With it, one that arrives too early is held "
              "and offered again each time something is delivered. Nothing is "
              "dropped and nothing is reordered on the wire — only the moment "
              "each message is shown.",
              (CLOCKS,), 'job = Calls(run=deliveries, delivery="causal")'),

    # --- writing functions ---
    SymbolDoc("def", "def name(param: type) -> type:", "A function you write.",
              "Every parameter and the return type is written down, and "
              "nothing is inferred. Writing them is what lets a job check that "
              "a function fits the position it is passed to.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "def hottest(city: string, readings: [int]) -> int:\n"
              "    top: int = 0\n"
              "    for reading: int in readings:\n"
              "        if reading > top:\n"
              "            top: int = reading\n"
              "    return top"),
    SymbolDoc("comprehension", "[element for name: type in list]",
              "One element out for each element in.",
              "How a function that has to produce many things produces them. "
              "A map is handed one record and answers with every pair it made "
              "from it, which may be none, one, or thousands — so it answers "
              "with a list, and this is how that list is built.\n\n"
              "Read it right to left: take each `reading` out of "
              "`split(payload)`, and for each one put a pair into the list. "
              "The loop variable carries its type for the same reason it does "
              "in a `for` statement: nothing here is inferred.",
              (MAPREDUCE,),
              "def perStation(station: string, payload: string) -> [pair]:\n"
              "    return [(station, reading) "
              "for reading: string in split(payload)]"),
    SymbolDoc("pair", "(key, value)",
              "The two halves of one intermediate result.",
              "What a map answers with a list of. The key decides which "
              "partition it goes to, and the value is whatever the reducer "
              "takes — a count, a document name, anything the job is about. "
              "A list of them is written `[pair]`, which is what a map "
              "declares as its return type.",
              (MAPREDUCE,), "(station, reading)"),
    SymbolDoc("Calls", "job = Calls(run=f)", "A job that is a sequence of calls.",
              "The work is one function, and the job is that function run "
              "in the world. Nothing has to be wrapped in a machine to hold "
              "it. A MapReduce job has the same shape and takes three "
              "functions instead of one.",
              (RPC, CLOCKS, SPARK),
              "def story() -> void:\n"
              '    chf: int = bank.balance("savings")\n\n'
              "job = Calls(run=story)\nworld.run(job)"),
    SymbolDoc("MapReduce",
              "job = MapReduce(map=f, reduce=g, partition=h, partitions=N)",
              "Wire your functions into a job.",
              "A function is the mapper because it was passed as the "
              "mapper, and it is accepted there only if its signature fits. "
              "Its name has no say in it. `combine=` adds a combiner.\n\n"
              "`partitions=N` says how many ways the keys are split. It is "
              "the `N` your partitioner is handed, and it is not the number "
              "of machines: every machine maps, and after the shuffle N of "
              "them are each handed a partition to fold. Ask for two "
              "partitions in a world of five machines and three of them do "
              "no folding at all.",
              (MAPREDUCE,),
              "job = MapReduce(map=readSensor, reduce=hottest, "
              "partition=spread, partitions=2)"),

    # --- calling across the network ---
    SymbolDoc("call", "machine.method(arg [, deadline=T] [, retries=N])",
              "Make a synchronous call.",
              "The caller waits for the round trip, so a slow server shows up "
              "as caller idle time. Statuses follow gRPC: ok, unavailable, "
              "unimplemented, deadline_exceeded.",
              (RPC, CLOCKS, SPARK),
              'chf: int = bank.balance("savings", deadline=0.5, retries=2)'),

    # --- checks the exercise sets ---
    SymbolDoc("expect", "expect KEY = N", "Assert a final count.",
              "The correctness check.", (MAPREDUCE, SPARK), "expect zurich = 3"),
    SymbolDoc("note", "note TEXT", "A caption on the diagram.",
              "Shown at this point in the run. Useful for narrating a video.",
              (MAPREDUCE, SPARK, RPC, CLOCKS), "note the shuffle starts here"),

    # --- Spark pipelines ---
    # The functions are real PySpark lambdas. There is no SparkContext to
    # write: the executors are the machines the world was given, and the data
    # is what the pipeline reads, so the context is already on the page.
    SymbolDoc("textFile", "textFile(name)", "Read input into an RDD.",
              "The start of a pipeline. The name is an input the program "
              "declares or a file the task ships.",
              (SPARK,), 'departures = textFile("departures.csv")'),
    SymbolDoc("parallelize", "parallelize([...])", "Make an RDD from a list.",
              "Useful when the data is short enough to write down.",
              (SPARK,), 'stops = parallelize(["bern,4", "chur,0"])'),
    SymbolDoc("map", ".map(lambda x: ...)", "One record in, one out.",
              "Narrow, so it needs no shuffle and pipelines inside the "
              "current stage.",
              (SPARK,), 'rows.map(lambda row: row.split(","))'),
    SymbolDoc("flatMap", ".flatMap(lambda x: [...])", "One record in, many out.",
              "Narrow. The function returns a list, and every element of it "
              "becomes a record of its own.",
              (SPARK,), 'rows.flatMap(lambda row: row.split(","))'),
    SymbolDoc("filter", ".filter(lambda x: ...)", "Keep records that match.",
              "Narrow, and the cheapest thing you can do before a wide step: "
              "every record it drops is one the shuffle does not carry.",
              (SPARK,), 'delays.filter(lambda d: int(d) > 0)'),
    SymbolDoc("mapValues", ".mapValues(lambda v: ...)", "Change values, keep keys.",
              "Narrow. It leaves the key alone, so nothing has to move.",
              (SPARK,), "grouped.mapValues(lambda xs: sum(xs) / len(xs))"),
    SymbolDoc("reduceByKey", ".reduceByKey(lambda a, b: ...)",
              "Combine values per key.",
              "Wide: it forces a shuffle and begins a new stage. It combines "
              "on the map side first, so only one partial result per key "
              "crosses the network — which is what groupByKey does not do.",
              (SPARK,), "byStop.reduceByKey(lambda a, b: a + b)"),
    SymbolDoc("groupByKey", ".groupByKey()", "Gather every value per key.",
              "Wide, and it ships every record. reduceByKey reaches the same "
              "answer while moving far less, so prefer it when you can.",
              (SPARK,), "byStop.groupByKey()"),
    SymbolDoc("sortByKey", ".sortByKey()", "Order the records by key.",
              "Wide: an order across the whole RDD cannot be decided inside "
              "one partition.", (SPARK,), "totals.sortByKey()"),
    SymbolDoc("distinct", ".distinct()", "Drop repeated records.",
              "Wide, because two equal records may sit on different machines.",
              (SPARK,), "stops.distinct()"),
    SymbolDoc("join", ".join(other)", "Match two pair RDDs on their keys.",
              "Wide: both sides have to be brought together by key.",
              (SPARK,), "delays.join(platforms)"),
    SymbolDoc("partitionBy", ".partitionBy(lambda k: ...)",
              "Choose which partition a key goes to.",
              "Wide. Deciding the split yourself is how you keep everything "
              "that must be compared together on one machine — and how you "
              "cause skew if the function is a poor one.",
              (SPARK,), "byStop.partitionBy(lambda k: hash(k))"),
    SymbolDoc("cache", ".cache()", "Keep this RDD in memory.",
              "Without it, an RDD read by two branches is computed twice: "
              "the lineage is replayed for each. That is what makes an "
              "iterative job expensive.",
              (SPARK,), "kept = rows.cache()"),
    SymbolDoc("Spark", "job = Spark(pipeline=rdd, lose=rdd)",
              "The job to run in the world.",
              "`pipeline` names the last step, which is what forces the "
              "whole lineage to run. `lose` throws a step away so you can "
              "watch it rebuilt from lineage rather than reloaded.",
              (SPARK,), "job = Spark(pipeline=totals, lose=byStop)"),

    # --- clocks ---
    SymbolDoc("assert", "assert P.clock == [..]", "Check a claimed clock.",
              "Write 'assert A || B' for concurrency and 'assert A ->> B' "
              "for happens-before. A wrong claim is reported causally.",
              (CLOCKS,), "assert P3.clock == [2, 3, 1]"),
]

# How the reference is organised.
#
# By what a construct is, never by what it is used for. Grouping the pages
# MapReduce / Spark / calls / clocks made the reference a tour of the course's
# topics, which told a student which technology to reach for. That question
# belongs to the exercise. These groups answer a different one: how do I
# declare a machine, how do I build a world, how do I write a function.
#
# The site (docs.py) and the editor's own panel both read this, so they cannot
# describe the language differently.
GROUPS = [
    ("functions", "Functions", "What you write, and how it is written.",
     ["def", "comprehension", "pair", "parallel"]),
    ("machines", "Machines",
     "Declaring a kind of machine, and making ones that exist.",
     ["class", "instance", "state", "update", "starts", "type", "duration",
      "error_rate", "on_crash", "restart_after", "crash", "restart", "call"]),
    ("worlds", "Worlds",
     "The machines that exist together, and running in them.",
     ["World", "run"]),
    ("jobs", "Jobs", "Handing your functions to something that runs them.",
     ["Calls", "MapReduce", "times"]),
    ("messages", "Messages",
     "Processes talking to each other, and what order anyone can be sure of.",
     ["send", "broadcast", "late", "clock", "delivery"]),
    ("datasets", "Datasets",
     "Values built from other values, and what is remembered about how.",
     ["textFile", "parallelize", "map", "flatMap", "filter", "mapValues",
      "reduceByKey", "groupByKey", "sortByKey", "distinct", "join",
      "partitionBy", "cache", "Spark"]),
    ("checks", "Checks", "Statements that assert something about a run.",
     ["expect", "assert", "note"]),
]

DOC_INDEX = {d.name: d for d in DOCS}

# Every builtin, documented in full: what it does, why it exists, and one
# line a student could type. Generated from `expr.BUILTINS` at import so the
# two can never drift — a builtin added without documentation is a test
# failure rather than an empty hover.
#
# Kept in the same words a bachelor student would use. These are the only
# functions the language provides; anything shaped like an exercise is a
# function the student writes.
BUILTIN_HELP = {
    "split": ("Cut a string into its words.",
              "Any run of whitespace separates two pieces, so double "
              "spaces and newlines produce no empty ones. Punctuation stays "
              "attached to the piece it touches.",
              'for part: string in split("zurich bern chur"):'),
    "lower": ("The same string in lower case.",
              "Every letter is folded, and digits and punctuation are left "
              "alone. Two strings that differ only in case come out equal.",
              'lower("Zurich HB")      # "zurich hb"'),
    "upper": ("The same string in upper case.",
              "The mirror of lower. Neither one changes how long the string "
              "is.",
              'upper("chur")           # "CHUR"'),
    "strip": ("The string without leading or trailing spaces.",
              "Useful when input arrives with ragged edges. It touches only "
              "the ends, so spaces inside the string stay.",
              'strip("  chur  ")       # "chur"'),
    "len": ("How many items are in a list, or characters in a string.",
            "Works on both, and always returns an int.",
            'len(readings)           # how many items'),
    "sum": ("The total of a list of ints.",
            "Adds every item and returns the result. An empty list totals "
            "zero rather than failing.",
            'sum(readings)'),
    "max": ("The largest int in a list, or 0 if it is empty.",
            "An empty list gives 0 rather than an error, so nothing has to "
            "guard the call.",
            'max(readings)'),
    "min": ("The smallest int in a list, or 0 if it is empty.",
            "An empty list gives 0 here too, exactly as it does for max.",
            'min(readings)'),
    "hash": ("A number derived from a string, the same every time.",
             "The same string always gives the same number, and nothing "
             "else changes it: not timing, and not which machine asks. Java "
             "and JavaScript use the same 31-hash, so a number computed here "
             "matches one computed there.",
             'hash("zurich")'),
    "sort": ("The same values, in alphabetical order.",
             "Sorting is what makes an answer reproducible: the values reach "
             "a reducer in whatever order the network delivered them, so two "
             "runs of the same correct code can otherwise disagree about how "
             "the answer is written down.",
             'sort(["chur", "bern"])   # ["bern", "chur"]'),
    "join": ("Several strings written out as one.",
             "The separator goes between the pieces and not at either end, "
             "so joining nothing gives the empty string and joining one "
             "value gives that value back.",
             'join(["bern", "chur"], ", ")   # "bern, chur"'),
    "unique": ("Each value once, in the order it first appeared.",
               "Repeats are dropped and the first of each is kept, so the "
               "result is the same on every run. It does not sort: the order "
               "you get is the order the values arrived in, which is "
               "something you can reason about.",
               'for city: string in unique(arrivals):'),
    "abs": ("The size of a number, ignoring its sign.",
            "Returns the number without its sign, so -7 and 7 both give 7.",
            'abs(-7)                 # 7'),
}


def _builtin_docs() -> dict:
    """One line per builtin, from the signature and the help table."""
    from .expr import BUILTINS
    out = {}
    for name, (_params, _ret, _impl, signature) in BUILTINS.items():
        summary = BUILTIN_HELP.get(name, ("", "", ""))[0]
        out[name] = f"{signature} — {summary}" if summary else signature
    return out


BUILTIN_DOCS = _builtin_docs()


# --- the server ---------------------------------------------------------

@dataclass
class Analysis:
    """Everything the editor needs after one edit."""
    dialect: str
    diagnostics: list = field(default_factory=list)
    frame: dict | None = None
    gantt: dict | None = None
    metrics: list = field(default_factory=list)
    verdict: dict | None = None
    outputs: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "dialect": self.dialect,
            "diagnostics": self.diagnostics,
            "frame": self.frame,
            "gantt": self.gantt,
            "metrics": self.metrics,
            "verdict": self.verdict,
            "outputs": self.outputs,
        })


def syntax_check(source: str) -> list[Diagnostic]:
    """Grammar-level errors, with column-accurate positions from Lark."""
    from .pyspark import mask_arguments

    masked, _ = mask_arguments(source)
    try:
        parser().parse(masked if masked.endswith("\n") else masked + "\n")
        return []
    except UnexpectedInput as e:
        expected = ""
        accepts = getattr(e, "accepts", None) or getattr(e, "expected", None)
        if accepts:
            words = sorted(_readable(t) for t in list(accepts)[:6])
            expected = "expected " + ", ".join(w for w in words if w)
        line = getattr(e, "line", 1) or 1
        column = getattr(e, "column", 1) or 1
        # On a line carrying a lambda, "syntax error here" names the wrong
        # authority. The functions a transformation takes are Python, so
        # Python is asked what is wrong with them.
        from .pyspark import explain
        lines = source.splitlines()
        better = explain(lines[line - 1] if line <= len(lines) else "",
                         line, column)
        if better is not None:
            return [better]
        # The editor is where this matters most: the squiggle is already on
        # the right line, and "expected 'if'" is what it says about it.
        from .syntax import _untyped_local
        named = _untyped_local(lines, line)
        if named is not None:
            at, var = named
            return [Diagnostic(at, 1, "error",
                               f"{var} is given a value but never a type",
                               f"write the type you expect, e.g. {var}: int = ...")]
        return [Diagnostic(line, column, "error", "syntax error here", expected)]
    except Exception as e:                      # grammar bugs must not hide
        return [Diagnostic(1, 1, "error", f"could not parse: {e}", "")]


def _readable(terminal: str) -> str:
    """Turn a terminal name into something a student can act on."""
    t = str(terminal)
    if t.startswith("KW_"):
        return f"'{t[3:].lower()}'"
    return {"NAME": "a name", "NUMBER": "a number", "STRING": "a quoted string",
            "_NL": "a new line", "COMPARE": "a comparison"}.get(t, "")


def analyse_project(files: dict, entry: str = "main", assignment: str = "") -> str:
    """
    Analyse a multi-file program.

    `files` maps file name to contents. Files are merged in dependency order
    (see `project.Project`), and every diagnostic is translated back to the
    file and line the student is actually looking at.
    """
    from .project import Project

    proj = Project.of(files, entry)
    merged, line_map, order_diags = proj.combine()

    payload = json.loads(analyse(merged, assignment))
    # Diagnostics about the program as a whole — a missing input declaration,
    # no reducer count — carry no real position, and reporting them at line 1
    # would blame whichever file merged first. They belong to the entry file,
    # which is where a student would fix them.
    whole_program = {"no input splits declared", "no processes declared"}
    for d in payload.get("diagnostics", []):
        if d["line"] <= 1 and (d["message"] in whole_program
                               or "defaulting to" in d["message"]):
            d["file"], d["line"] = entry, 1
            continue
        name, line = proj.locate(line_map, d["line"])
        d["file"], d["line"] = name, line
    payload["diagnostics"] = (
        [dict(d.to_json(), file=entry) for d in order_diags]
        + payload.get("diagnostics", []))
    payload["files"] = list(proj.files)
    return json.dumps(payload)


def analyse(source: str, assignment: str = "") -> str:
    """
    The single entry point the editor calls on every edit.

    When `assignment` is given, the student's code is run inside that
    assignment's setup and judged against its criteria — which may include
    hidden tests. Students never write their own `expect`.

    Returns JSON: diagnostics, the diagram, metrics and a verdict.
    """
    if assignment:
        from .assignment import ASSIGNMENTS
        spec = ASSIGNMENTS.get(assignment)
        if spec is not None:
            return _analyse_assignment(spec, source)

    dialect = detect_dialect(source)
    result = Analysis(dialect=dialect)

    # Syntax first — everything downstream assumes a parseable program.
    syntax = syntax_check(source)
    if syntax:
        result.diagnostics = [d.to_json() for d in syntax]
        return result.to_json()

    try:
        result = _analyse_dialect(source, dialect, result)
    except Exception as e:
        from .notation import NotationError
        if isinstance(e, NotationError):
            result.diagnostics += [d.to_json() for d in e.diagnostics]
        else:
            result.diagnostics.append(
                Diagnostic(1, 1, "error", f"{type(e).__name__}: {e}").to_json())
    return result.to_json()


def _analyse_assignment(spec, student_source: str) -> str:
    """Analyse a student's code in the context of its task."""
    from .metrics import measure
    from .notation import NotationError
    from .notation_mr import build_mr, lint_mr
    from .notation_spark import build_spark, lint_spark
    from .runtime import build as build_rpc
    from .shapes import dataflow, gantt
    from .syntax import lint as lint_program

    def lint_rpc(src):
        return lint_program(src)[1]

    result = Analysis(dialect=spec.dialect)
    full = spec.program(student_source)

    # Each task names its own dialect, so an RPC task is not fed through the
    # MapReduce checker.
    if spec.dialect in (RPC, CLOCKS, SPARK):
        linter, builder = lint_rpc, lambda s: build_rpc(s)
    else:
        linter, builder = lint_mr, lambda s: build_mr(s)[0]

    # Diagnostics are reported against the *student's* lines, so the setup the
    # assignment prepends must not shift the numbers they see.
    offset = len(spec.setup.rstrip().splitlines()) + 1 if spec.setup else 0

    syntax = syntax_check(full)
    diags = syntax or linter(full)
    for d in diags:
        d.line = max(1, d.line - offset)
    result.diagnostics = [d.to_json() for d in diags]
    if any(d.severity == "error" for d in diags):
        return result.to_json()

    try:
        cluster = builder(full)
    except NotationError as e:
        for d in e.diagnostics:
            d.line = max(1, d.line - offset)
        result.diagnostics += [d.to_json() for d in e.diagnostics]
        return result.to_json()

    trace = cluster.sorted_trace()
    result.frame = dataflow(trace, title="").to_json()
    result.gantt = gantt(trace, title="").to_json()
    result.metrics = _metrics_json(measure(trace))
    result.verdict = spec.judge(student_source).to_json()
    result.outputs = {e.detail["key"]: e.detail["value"]
                      for e in cluster.trace.of_kind("output")}
    return result.to_json()


def _analyse_dialect(source: str, dialect: str, result: Analysis) -> Analysis:
    from .metrics import measure
    from .shapes import dataflow, gantt, spacetime

    # A clocks program is a program of `@process` machines, which the same
    # grammar and the same runtime handle. Sending it to the old line-oriented
    # clocks linter instead meant free play on a vector-clock example reported
    # `cannot parse: '@process'` against every line of a file that is perfectly
    # valid — fourteen errors in a program with none. The dialect names the
    # exercise, not a separate front end.
    if dialect in (RPC, CLOCKS, SPARK):
        from .runtime import build
        from .syntax import lint as lint_program

        _, diags = lint_program(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        cluster = build(source)
        # What the pipeline noticed while running: a reducer that would not
        # survive being split across partitions is reported at its own line.
        for warning in getattr(getattr(cluster, "pipeline", None), "warnings", []):
            result.diagnostics.append(warning.to_json())

    else:
        from .notation_mr import build_mr, judge_mr, lint_mr
        diags = lint_mr(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        cluster, expects = build_mr(source)
        result.verdict = judge_mr(source).to_json()
        result.outputs = {e.detail["key"]: e.detail["value"]
                          for e in cluster.trace.of_kind("output")}

    trace = cluster.sorted_trace()
    result.frame = dataflow(trace, title="").to_json()
    result.gantt = gantt(trace, title="").to_json()
    result.metrics = _metrics_json(measure(trace))
    # Whatever the run produced, whichever exercise produced it. This was set
    # only on the MapReduce path, so a Spark pipeline computed its answer and
    # then showed the student nothing.
    result.outputs = {e.detail["key"]: e.detail["value"]
                      for e in trace.of_kind("output")}
    return result


def _metrics_json(metrics: dict) -> list:
    return [{"name": m.name, "value": round(m.value, 3), "unit": m.unit,
             "detail": m.detail, "lower_is_better": m.lower_is_better,
             "explain": m.explanation}
            for m in metrics.values()]


def _preview(values, limit: int = 8):
    """A short, JSON-safe preview of an RDD's contents."""
    head = [list(v) if isinstance(v, tuple) else v for v in values[:limit]]
    return {"count": len(values), "sample": head}


# --- editor support -----------------------------------------------------

def completions(dialect: str | None = None) -> str:
    """Completion items, optionally filtered to one exercise."""
    items = [d for d in DOCS if not dialect or not d.dialects
             or dialect in d.dialects]
    out = [d.to_json() for d in items]
    out += [{"name": n, "signature": doc.split("—")[0].strip(),
             "summary": doc.split("—", 1)[-1].strip(), "detail": "",
             "dialects": [], "example": ""}
            for n, doc in BUILTIN_DOCS.items()]
    return json.dumps(out)


def hover(word: str) -> str:
    """
    Documentation for one symbol, or an empty object.

    Builtins are answered first. Some share a name with a keyword — `split` is
    both a function and an input declaration — and inside a function body it is
    the function the student means, so answering with the keyword's docs was
    simply wrong.
    """
    from .expr import BUILTINS

    if word in BUILTINS:
        signature = BUILTINS[word][3]
        summary, detail, example = BUILTIN_HELP.get(word, ("", "", ""))
        return json.dumps({"name": word, "signature": signature,
                           "summary": summary, "detail": detail,
                           "dialects": [], "example": example})
    d = DOC_INDEX.get(word)
    if d:
        return json.dumps(d.to_json())
    return json.dumps({})


def reference() -> str:
    """The whole language, for the editor's panel and its search."""
    placed = [n for _, _, _, names in GROUPS for n in names]
    missing = [d.name for d in DOCS if d.name not in placed]
    if missing:
        raise RuntimeError(f"GROUPS does not place: {missing}")
    index = {d.name: d for d in DOCS}
    return json.dumps({
        "groups": [{"slug": slug, "title": title, "note": note,
                    "items": [index[n].to_json() for n in names]}
                   for slug, title, note, names in GROUPS],
        "builtins": BUILTIN_DOCS})
