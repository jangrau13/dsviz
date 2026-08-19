"""
A small exercise, for testing the machinery against.

dsviz ships no tasks. Its test suite still needs some, so it has these — four
of them, one per dialect, deliberately in a domain no course uses. They exist
to exercise judging, hand-in, the editor and the loader, not to teach anything.

Keeping them here rather than pointing the suite at a real exercise is the
whole point of the split: the language must be testable without a course, or
the course is part of the language again.
"""

from dsviz.assignment import (
    Assignment, BudgetLimit, Expectation, Requirement)


TITLE = "Fixture — one task per dialect"


GOALS = {
    "fixture": {
        "level": "understand",
        "text": "There is nothing to learn here. This objective exists so the "
                "machinery that reports objectives has one to report.",
    },
}


# A graded MapReduce: how many visits each branch had. Not a word count —
# no case to fold, no document to tokenise — so a fixture cannot double as
# an answer sheet for anybody's course.
TAKINGS = Assignment(
    name="fx-takings",
    title="Fixture: visits per branch",
    starter="""\
# Fixture - visits per branch.
#
# Each split is a day's visits: branch names, one after another, and
# the case is not consistent. Count how many visits each branch had.

@mapper
class Till:
    pass

@reducer
class Ledger:
    pass

m1 = Till(speed=1.0)
m2 = Till(speed=1.0)
r1 = Ledger(speed=1.0)
r2 = Ledger(speed=1.0)

world = World(machines=[m1, m2, r1, r2])

# --- your code -------------------------------------------------------
# Write the three functions here, then run the job in the world:
#
#     job = MapReduce(map=..., reduce=..., partition=...)
#     world.run(job)
""",
    goals=["fixture"],
    brief="Count the visits each branch had, ignoring case.",
    steps=[
        "Write the three functions.",
        "Pass them to a job, and run it in the world.",
    ],
    # `depot` and `store` hash to different reducers, so the requirement
    # that both get work can be met — and a partitioner that always answers
    # the same thing can be caught.
    setup='mappers 2\nreducers 2\n'
          'split day1: "depot Store depot"\n'
          'split day2: "store DEPOT"',
    holdout='mappers 2\nreducers 2\n'
            'split day1: "quay Dock quay"\n'
            'split day2: "dock DOCK"',
    expects=[Expectation("depot", 3), Expectation("store", 2)],
    holdout_expects=[Expectation("quay", 2), Expectation("dock", 3)],
    requires=[Requirement("every reducer gets work", "all_reducers_used",
                          why="that is what partition is for")],
    budgets=[BudgetLimit("network", "<", 20,
                         why="every pair emitted crosses the network")],
)

# A second MapReduce, so "the same code stamped against a different task" has
# a different task to be stamped against that can still run the same program.
BUSIEST = Assignment(
    name="fx-busiest",
    title="Fixture: the same counting, ungraded",
    starter="""\
# Fixture - visits per branch, ungraded.
#
# The same shape as fx-takings, written out. Nothing is checked here: it
# ships complete, which is what an exploration task is.

@mapper
class Till:
    pass

@reducer
class Ledger:
    pass

m1 = Till(speed=1.0)
m2 = Till(speed=1.0)
r1 = Ledger(speed=1.0)
r2 = Ledger(speed=1.0)

world = World(machines=[m1, m2, r1, r2])

def perDay(key: string, value: string) -> void:
    for branch: string in split(lower(value)):
        emit(branch, 1)

def addUp(key: string, values: [int]) -> int:
    return sum(values)

def spread(key: string, n: int) -> int:
    return hash(key) mod n

job = MapReduce(map=perDay, reduce=addUp, partition=spread)

world.run(job)
""",
    goals=["fixture"],
    brief="The same shape as fx-takings, with nothing checked.",
    steps=["Run it."],
    setup='mappers 2\nreducers 2\n'
          'split day1: "depot store depot"\n'
          'split day2: "store depot"',
)

CALLS = Assignment(
    name="fx-calls",
    title="Fixture: one machine asking another",
    starter="""\
# Fixture - one machine asking another.

@machine
class Warehouse:
    @duration(0.3)
    def stock(item: string) -> int:
        return 12

@machine
class Shop:
    pass

shop = Shop(speed=1.0)
depot = Warehouse(speed=1.0)     # step 2: make this 0.25

world = World(machines=[shop, depot])

def story() -> void:
    # depot.crash()
    left: int = depot.stock("ladders")

job = Calls(run=story)

world.run(job)
""",
    goals=["fixture"],
    brief="A call that crosses a network.",
    steps=["Run it.", "Make the callee slower."],
    dialect="rpc",
)

TICKS = Assignment(
    name="fx-ticks",
    title="Fixture: messages between processes",
    starter="""\
# Fixture - messages between processes.

@process
class Node:
    pass

p1 = Node(speed=1.0)
p2 = Node(speed=1.0)

world = World(machines=[p1, p2])

def story() -> void:
    p1.send(p2, "hello")
    # step 2: add another message
    # p2.send(p1, "and you")

job = Events(run=story, clock="lamport")

world.run(job)
""",
    goals=["fixture"],
    brief="Two processes and the messages between them.",
    steps=["Run it.", "Add a message."],
    dialect="clocks",
)

STAGES = Assignment(
    name="fx-stages",
    title="Fixture: a pipeline over a dataset",
    starter="""\
# Fixture - a pipeline over a dataset.

@machine
class Executor:
    pass

e1 = Executor(speed=1.0)
e2 = Executor(speed=1.0)

world = World(machines=[e1, e2])

rows   = textFile("readings.txt")
fields = rows.map(lambda row: row.split(" "))
pairs  = fields.map(lambda f: (f[0], int(f[1])))
totals = pairs.reduceByKey(lambda a, b: a + b)

job = Spark(pipeline=totals, lose=totals)   # step 2: lose=pairs

world.run(job)
""",
    goals=["fixture"],
    brief="A pipeline, run across executors.",
    steps=["Run it.", "Lose a step and watch it rebuild."],
    dialect="spark",
)


TASKS = [TAKINGS, BUSIEST, CALLS, TICKS, STAGES]
