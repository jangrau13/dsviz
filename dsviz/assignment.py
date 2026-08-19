"""
Assignments: the criteria a submission is judged against.

Students write map, reduce and partition. They do *not* write their own tests —
`expect` and `budget` belong to the assignment, are set by the lecturer, and
may be hidden. A student who writes their own passing criteria has not been
assessed.

An assignment is data, so it can live in a file next to the exercise, be
served to the browser, and be reused by the grader without duplication.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import assets

from .contest import CaseResult, Submission, Verdict
from .metrics import measure
from .notation_mr import BUDGET_METRICS


# Resolved rather than hard-coded: installed from git the tasks ride inside
# the package, in this checkout they sit beside it. See `assets`.
TASKS = assets.tasks_dir()


def starter_for(name: str) -> str:
    """
    The code a task opens with, read from `tasks/<name>.ds`.

    Program text belongs in a program file. Holding it as a Python string meant
    the language's own syntax was quoted inside another language, where nothing
    checked it: the parser never saw it, the editor never opened it, and it
    could drift out of the syntax it was supposed to teach.
    """
    path = TASKS / f"{name}.ds"
    return path.read_text() if path.exists() else ""


@dataclass
class Expectation:
    """A correctness check: this key must end with this count."""
    key: str
    count: int
    hidden: bool = False


@dataclass
class Requirement:
    """
    Something the submission must actually do.

    Output checks alone cannot tell a written function from an unwritten one:
    a partitioner that always answers 0 still produces correct counts. A
    requirement inspects the run itself, so a step cannot be skipped.
    """
    name: str
    check: str                    # which property to test
    why: str = ""


@dataclass
class BudgetLimit:
    """A non-functional limit: this metric must stay within this bound."""
    metric: str
    op: str
    value: float
    hidden: bool = False
    why: str = ""


def build_cluster(dialect: str, source: str, *, seed: int | None = None):
    """
    Run a program of any dialect and return the cluster it produced.

    One dispatch, used by every path that needs to execute a submission. The
    exploration path and the graded path used to choose a builder separately,
    and the graded one did not choose at all — it always called `build_mr`, so
    a graded RPC or Spark task would have been parsed as MapReduce. Keeping the
    choice in one place is what stops those two from drifting apart again.

    The builders disagree about what they return: `build_rpc` gives a cluster,
    the other two give a tuple whose first element is one. That is normalised
    here so callers do not have to know.

    `seed` fixes the failure draws in every dialect, which is what lets the
    same program be run a hundred times and one of those runs be pulled back.
    """
    if dialect == "rpc":
        from .runtime import build
        return build(source, seed=seed)
    if dialect == "spark":
        from .notation_spark import build_spark
        return build_spark(source, seed=seed)[0]
    from .notation_mr import build_mr
    return build_mr(source, seed=seed)[0]


@dataclass
class Assignment:
    """
    One task: what to build, the input to develop against, and the criteria.

    Two datasets. `setup` is what the student sees and iterates on. `holdout`
    is different input, used only when they hand in — same code, different
    data. Passing on the hold-out means the logic generalises rather than
    having been fitted to the visible example.
    """
    name: str
    title: str
    brief: str = ""
    goals: list = field(default_factory=list)   # keys into GOALS
    steps: list = field(default_factory=list)   # what the student must do
    dialect: str = "mapreduce"
    setup: str = ""                       # visible input, prepended to their code
    holdout: str = ""                     # held-out input, used only at hand-in
    # Extra files the task ships alongside the entry file, as {name: text}.
    # The entry file is the task's own name, so a two-file task appears as
    # task1.ds plus whatever it names here. A file becomes visible to another
    # by being named in a `use`.
    extra_files: dict = field(default_factory=dict)
    expects: list = field(default_factory=list)
    holdout_expects: list = field(default_factory=list)
    budgets: list = field(default_factory=list)
    requires: list = field(default_factory=list)

    @property
    def starter(self) -> str:
        """What the editor opens with, read from this task's own .ds file."""
        return starter_for(self.name)

    def program(self, student_source: str, *, holdout: bool = False) -> str:
        """The full program: the task's input plus the student's code."""
        setup = (self.holdout or self.setup) if holdout else self.setup
        return f"{setup.rstrip()}\n\n{student_source}" if setup else student_source

    def visible_criteria(self) -> list[dict]:
        """What the student is allowed to see."""
        out = [{"kind": "require", "text": r.name, "why": r.why, "hidden": False}
               for r in self.requires]
        for e in self.expects:
            out.append({"kind": "expect", "text": f"{e.key} = {e.count}",
                        "hidden": e.hidden} if not e.hidden else
                       {"kind": "expect", "text": "hidden test", "hidden": True})
        for b in self.budgets:
            out.append({"kind": "budget",
                        "text": f"{b.metric} {b.op} {b.value:g}",
                        "why": b.why, "hidden": b.hidden} if not b.hidden else
                       {"kind": "budget", "text": "hidden budget", "hidden": True})
        return out

    def judge(self, student_source: str, *, holdout: bool = False) -> Submission:
        """
        Run the student's program and score it.

        With `holdout`, the same code runs against input the student has never
        seen, checked against `holdout_expects`. That is the hand-in.
        """
        from .notation import NotationError

        # Without hold-out data the browser must not pretend it graded on
        # unseen input — it says so instead, and the real grade comes from CI.
        graded_on_holdout = holdout and bool(self.holdout_expects)
        expects = self.holdout_expects if graded_on_holdout else self.expects
        sub = Submission()

        # An exploration task has no criteria: it is judged by whether it runs.
        if not expects and not self.budgets and not self.requires:
            try:
                build_cluster(self.dialect,
                              self.program(student_source, holdout=holdout))
                sub.results.append(CaseResult(
                    "runs without errors", Verdict.AC, score=1.0))
            except NotationError as e:
                d = e.diagnostics[0]
                sub.results.append(CaseResult(
                    "runs without errors", Verdict.CE,
                    f"line {d.line}: {d.message}"))
            return sub
        try:
            cluster = build_cluster(
                self.dialect, self.program(student_source, holdout=holdout))
        except NotationError as e:
            d = e.diagnostics[0]
            sub.results.append(CaseResult(
                "compile", Verdict.CE, f"line {d.line}: {d.message}"))
            return sub

        counts = {ev.detail["key"]: ev.detail["value"]
                  for ev in cluster.trace.of_kind("output")}

        for e in expects:
            got = counts.get(e.key, 0)
            ok = got == e.count
            # On the hold-out the student must not learn the expected values,
            # only whether each case passed.
            hide = e.hidden or graded_on_holdout
            sub.results.append(CaseResult(
                "held-out test" if graded_on_holdout else
                (f"{e.key} = {e.count}" if not e.hidden else "hidden test"),
                Verdict.AC if ok else Verdict.WA,
                "" if ok else ("did not match" if hide
                               else _diagnose(e.key, e.count, got, cluster)),
                score=1.0 if ok else 0.0, hidden=hide))

        for req in self.requires:
            ok, detail = _check_requirement(req, cluster)
            sub.results.append(CaseResult(
                req.name, Verdict.AC if ok else Verdict.WA,
                "" if ok else detail, score=1.0 if ok else 0.0))

        metrics = measure(cluster.sorted_trace())
        for b in self.budgets:
            key, human = BUDGET_METRICS[b.metric]
            actual = metrics[key].value
            ok = {"<": actual < b.value, "<=": actual <= b.value,
                  ">": actual > b.value, ">=": actual >= b.value}[b.op]
            sub.results.append(CaseResult(
                f"{b.metric} {b.op} {b.value:g}" if not b.hidden else "hidden budget",
                Verdict.AC if ok else Verdict.WA,
                "" if ok else f"{human} was {actual:.2f}",
                score=1.0 if ok else 0.0, hidden=b.hidden))

        return sub

    def to_json(self) -> dict:
        return {"name": self.name, "title": self.title, "brief": self.brief,
                "goals": [{"key": g, "level": GOALS[g]["level"],
                           "title": GOALS[g]["text"]}
                          for g in self.goals if g in GOALS],
                "steps": self.steps, "dialect": self.dialect,
                "setup": self.setup, "starter": self.starter,
                # The editor opens one tab per file. The entry file carries the
                # task's own name, so the student implements task1.ds.
                "files": {self.name: self.starter, **self.extra_files},
                "criteria": self.visible_criteria(),
                "holdout_cases": len(self.holdout_expects or self.expects)}


def _diagnose(key: str, want: int, got: int, cluster) -> str:
    """
    Say what is probably wrong, not just that something is.

    The shape of the error usually identifies the function: too high across the
    board points at reduce, a missing key at map, everything equal to one at a
    reduce that is not aggregating.
    """
    outputs = {e.detail["key"]: e.detail["value"]
               for e in cluster.trace.of_kind("output")}

    if got == 0 and key not in outputs:
        return (f"nothing was counted for {key!r} — does your map emit it? "
                f"(it emitted: {', '.join(sorted(outputs)[:6]) or 'nothing'})")

    # Every key collapsing to the same number means reduce ignored its values.
    distinct = set(outputs.values())
    if len(distinct) == 1 and len(outputs) > 1:
        only = next(iter(distinct))
        return (f"every key came out as {only} — your reduce is returning the "
                "same thing regardless of the values it was given")

    # Keys that differ only by case mean map did not normalise, so the same
    # word was counted as two. This looks like a reduce bug but is not.
    folded: dict = {}
    for k in outputs:
        folded.setdefault(str(k).lower(), []).append(str(k))
    variants = folded.get(str(key).lower(), [])
    if len(variants) > 1:
        return (f"{key!r} was counted as {len(variants)} separate keys "
                f"({', '.join(sorted(variants))}), so your map is not "
                "normalising before it emits")

    # If map produced the right keys, the arithmetic is reduce's job.
    emitted_keys = {p[0] for e in cluster.trace.of_kind("hold")
                    for p in e.detail.get("items", []) if isinstance(p, tuple)}
    if key in emitted_keys:
        return (f"counted {got} instead of {want} — map emitted {key!r} "
                "correctly, so check what your reduce does with the values")

    if got > want:
        return (f"counted {got} instead of {want} — more values reached this "
                "key than expected; check what map emits")

    return (f"counted {got} instead of {want} — fewer values reached this key "
            "than expected; check map, then partition")


def _check_requirement(req: "Requirement", cluster) -> tuple[bool, str]:
    """Test one design property against what actually happened."""
    if req.check == "all_reducers_used":
        # A partitioner that always answers the same thing leaves reducers
        # idle. The counts can still be right, so only the run reveals it.
        reducers = [m for m in cluster.machines.values() if m.role == "reducer"]
        used = {e.machine for e in cluster.trace.of_kind("recv")}
        idle = [r.name for r in reducers if r.name not in used]
        if idle:
            return False, (f"{', '.join(idle)} received nothing — every key "
                           f"went to the same reducer")
        return True, ""

    if req.check == "combines_locally":
        # A combiner leaves each mapper holding fewer pairs than it saw words:
        # duplicates within one split have already been folded together.
        # Comparing sends to what the mappers hold cannot detect this, because
        # by then the combining has already happened.
        words = sum(len(str(e.detail.get("value", "")).split())
                    for e in cluster.trace.of_kind("input"))
        pairs = sum(e.detail.get("total", 0)
                    for e in cluster.trace.of_kind("hold"))
        if words and pairs >= words:
            return False, (f"nothing was combined before the shuffle — all "
                           f"{pairs} pair(s) still crossed the network")
        return True, ""

    return True, ""


# --- learning goals -----------------------------------------------------
# Stated once here so a task can point at them, and so a student can see what
# an exercise is *for* rather than only what it asks them to type. The wording
# follows the questions the written report already asks.

# level: the Krathwohl tier the verb belongs to, so a task can be checked for
# spread rather than sitting entirely at "understand".
GOALS = {
    "causality": {
        "level": "analyse",
        "text": "Students will distinguish events that are ordered from those "
                "that are concurrent, using only the messages between them, and "
                "explain why wall-clock time cannot settle it.",
    },
    "rpc": {
        "level": "understand",
        "text": "Students will explain what a remote procedure call is and "
                "when it is useful, distinguishing it from a local call in "
                "terms of what can fail.",
    },
    "failure": {
        "level": "analyse",
        "text": "Students will distinguish an unresponsive service from a "
                "failed one, and justify a caller's choice of deadline and "
                "retry in terms of the guarantees each provides.",
    },
    "mapreduce": {
        "level": "understand",
        "text": "Students will describe how MapReduce processes a job, "
                "identifying which stages the programmer writes and which the "
                "framework provides.",
    },
    "independence": {
        "level": "analyse",
        "text": "Students will examine why a deterministic partitioner is "
                "required for reducers to run independently, and distinguish "
                "the failures that follow from its absence.",
    },
    "locality": {
        "level": "understand",
        "text": "Students will explain data locality and its effect on "
                "efficiency, identifying the shuffle as the point at which it "
                "can no longer be preserved.",
    },
    "cost": {
        "level": "evaluate",
        "text": "Students will evaluate competing implementations that produce "
                "identical output, judging them on network traffic, load "
                "balance and tail latency.",
    },
    "latency": {
        "level": "evaluate",
        "text": "Students will appraise MapReduce's suitability for iterative "
                "workloads, supporting their judgement with the cost of "
                "barriers and of writing intermediate results to disk.",
    },
    "implement": {
        "level": "apply",
        "text": "Students will construct working map, reduce and partition "
                "functions in a typed notation, demonstrated by passing tests "
                "on input they have not seen.",
    },
    "combiner": {
        "level": "create",
        "text": "Students will design a combiner that reduces network traffic "
                "without altering the result, and defend why the operation it "
                "performs must be associative and commutative.",
    },
}


# --- the course's tasks -------------------------------------------------
# Starters are scaffolds: the signature and the shape are given, the logic is
# the student's to write. Reference solutions are never in this file: it is
# served to the browser, so anything here is visible to students.

RPC_BASICS = Assignment(
    name="t0-rpc",
    title="Task 0: calls over a network",
    goals=["rpc", "failure"],
    brief="Before anything is distributed, one machine has to ask another for "
          "something. Find out what that costs, and what happens when it fails.",
    steps=[
        "Run the program as given. Watch the client wait while each server works.",
        "Give the slow call a deadline it cannot meet, and see what the client "
        "is told.",
        "Crash a server before a call, and add retries until the call succeeds "
        "after a restart.",
        "Answer for yourself: how does the client tell 'slow' from 'dead'?",
    ],
    dialect="rpc",
)

WORD_COUNT = Assignment(
    name="t1-wordcount",
    title="Task 1: word count",
    goals=["mapreduce", "implement", "independence", "cost"],
    brief="Count how often each word appears, ignoring case.",
    steps=[
        "Write the mapper: it is given a document and emits one pair per "
        "word.",
        "Write the reducer: it is given a word and all its counts, and "
        "produces the total.",
        "Write the partitioner: every occurrence of a key must reach the "
        "same reducer.",
        "Pass the three to a job, and run it until the visible tests pass. "
        "Handing in re-runs your code on input you have not seen.",
    ],
    setup='mappers 3\nreducers 2\n'
          'split doc1: "The cat sat on the mat"\n'
          'split doc2: "the dog ran"\n'
          'split doc3: "The CAT ran"',
    holdout='mappers 3\nreducers 2\n'
            'split doc1: "birds SING and birds fly"\n'
            'split doc2: "the Fish swims"\n'
            'split doc3: "birds fly SOUTH"',
    expects=[Expectation("the", 4), Expectation("cat", 2),
             Expectation("ran", 2), Expectation("mat", 1)],
    holdout_expects=[Expectation("birds", 3), Expectation("fly", 2),
                     Expectation("sing", 1), Expectation("swims", 1)],
    requires=[Requirement(
        "every reducer gets work", "all_reducers_used",
        why="that is what partition is for")],
    budgets=[BudgetLimit("network", "<", 40,
                         why="every pair you emit crosses the network")],
)

COMBINER = Assignment(
    name="t2-combiner",
    title="Task 2: add a combiner",
    goals=["locality", "combiner", "cost", "latency"],
    brief="Same answer, far less network traffic.",
    steps=[
        "Bring your mapper and reducer across from Task 1.",
        "Add a combiner, which aggregates a mapper's own pairs before the "
        "shuffle.",
        "Check that the network metric falls while the counts stay "
        "identical.",
        "Work out why a combiner has to be safe to run zero, one or many "
        "times.",
    ],
    setup='mappers 3\nreducers 2\n'
          'split doc1: "the cat sat the dog the cat"\n'
          'split doc2: "the dog ran the cat the sat"\n'
          'split doc3: "the cat ran the sat the dog"',
    holdout='mappers 3\nreducers 2\n'
            'split doc1: "red red blue red green"\n'
            'split doc2: "blue red green blue red"\n'
            'split doc3: "green red blue red green"',
    expects=[Expectation("the", 9), Expectation("cat", 4),
             Expectation("dog", 3)],
    holdout_expects=[Expectation("red", 7), Expectation("blue", 4),
                     Expectation("green", 4)],
    # 21 messages without a combiner, 14 with one. The budget sits between,
    # so it can only be met by actually combining.
    requires=[Requirement(
        "combines before the shuffle", "combines_locally",
        why="a combiner must reduce what crosses the network")],
    budgets=[BudgetLimit("network", "<=", 15,
                         why="without a combiner this is 21")],
)


SPARK_MEMORY = Assignment(
    name="t3-spark",
    title="Task 3: keeping data in memory",
    goals=["locality", "cost"],
    brief="MapReduce writes to disk between stages. Spark keeps the data "
          "and remembers how it was made, so a lost partition is recomputed "
          "rather than reloaded.",
    steps=[
        "Run it and watch the stages run across the executors.",
        "Lose a step, and watch the lineage rebuild it instead of reloading "
        "it.",
        "Lose an earlier step, and see how much more has to be rebuilt.",
        "Make one executor slow, and find the straggler on the timeline.",
    ],
    dialect="rpc",
)

CLOCKS = Assignment(
    name="t4-clocks",
    title="Task 4: what happened before what",
    goals=["causality"],
    brief="With no shared clock, the only order anyone can know is the one "
          "messages create. Everything else is concurrent.",
    steps=[
        "Run it and read the diagram: time runs down, messages run across.",
        "Add a message and watch which events become ordered by it.",
        "Find two events that are concurrent, where neither can see the other.",
        "Make a process slow, and confirm order is not about wall time.",
    ],
    dialect="rpc",
)

MR_OVER_RPC = Assignment(
    name="t5-mr-rpc",
    title="Task 3: map and reduce on separate servers",
    goals=["rpc", "failure", "mapreduce", "locality"],
    brief="The same word count as Task 1, except the map and the reduce now "
          "live on different machines and the client has to ask each of them "
          "across a network.",
    steps=[
        "Run it as given, and watch the client wait for each server in turn.",
        "Give the map call a deadline it cannot meet, and read what the "
        "client is told.",
        "Crash the map server before the call, and retry into it. A machine "
        "that is down is still down when the retry arrives.",
        "Make it unreliable instead of dead, and work out when a retry is "
        "worth anything at all.",
    ],
    dialect="rpc",
)

TELEMETRY = Assignment(
    name="t6-telemetry",
    title="Task 2: finding the shuffle",
    goals=["locality", "cost"],
    brief="Readings arrive one row per sensor; the question is per room. "
          "Somewhere in between, every reading for a room has to reach the "
          "same machine, and that move is the expensive part.",
    steps=[
        "Run it and say, from the diagram, which line caused the barrier.",
        "Move the filter earlier and see whether the shuffle gets cheaper.",
        "Lose the grouped step, then lose the one before it, and compare "
        "what has to be rebuilt.",
        "Make one executor slow, and find the straggler on the timeline.",
    ],
    dialect="rpc",
)

KMEANS = Assignment(
    name="t7-kmeans",
    title="Task 3: the same points, round after round",
    goals=["latency", "cost", "locality"],
    brief="k-means repeats one pass until it settles. Assigning points needs "
          "no coordination; recomputing the centroids needs all of them. "
          "Keeping the points in memory is what makes the repetition cheap.",
    steps=[
        "Run it and count the stages. Say which had to wait for every "
        "machine and which did not.",
        "Take the cache away, and say what a third round would now cost.",
        "Lose the cached points, and note that the lineage rebuilt them "
        "rather than reading them off disk.",
        "Add a third round, and describe how the pipeline grows per round.",
    ],
    dialect="rpc",
)

LAMPORT = Assignment(
    name="t8-lamport",
    title="Task 1: one number per process",
    goals=["causality"],
    brief="A Lamport timestamp guarantees that if a happened before b then "
          "L(a) < L(b). It does not guarantee the converse, and seeing where "
          "that breaks is the point of the task.",
    steps=[
        "Run it and read the counters off the diagram.",
        "Add a message from a process that has not heard from the others, "
        "and compare its number with one it cannot know about.",
        "Find two events whose numbers cannot order them.",
        "Make a process slow, and confirm the counters do not move.",
    ],
    dialect="rpc",
)

BUFFERING = Assignment(
    name="t9-buffering",
    title="Task 2.2: delivering messages in order",
    goals=["causality"],
    brief="A vector clock says which messages depend on which. It does not "
          "stop one being shown before the message it answers — that takes a "
          "delivery rule, and holding what the rule refuses.",
    steps=[
        "Run it with the rule off, and read the line where a reply is shown "
        "before the message it answers.",
        "Switch the rule on. Note which message is held, and what released "
        "it.",
        "Make a process slow, and confirm the delivery order does not move.",
        "Add a message, choose who is late, and predict what is held before "
        "you run it.",
    ],
    dialect="rpc",
)

ASSIGNMENTS = {a.name: a for a in (
    RPC_BASICS, WORD_COUNT, COMBINER, MR_OVER_RPC,
    SPARK_MEMORY, TELEMETRY, KMEANS,
    LAMPORT, CLOCKS, BUFFERING)}


def load_holdout(path: str | None = None) -> bool:
    """
    Attach the held-out data, if it is available.

    The file is deliberately absent from a student's checkout: it lives in a
    private repository and is materialised only inside CI. Without it the
    browser still grades the visible criteria, which is all a student is meant
    to see; with it, `judge(..., holdout=True)` grades for real.

    Returns True when hold-out data was loaded.
    """
    import os

    candidate = path or os.environ.get("DSVIZ_HOLDOUT", "")
    if not candidate or not os.path.exists(candidate):
        return False

    with open(candidate) as fh:
        data = json.load(fh)

    for name, spec in data.items():
        task = ASSIGNMENTS.get(name)
        if task is None:
            continue
        task.holdout = spec.get("setup", "")
        task.holdout_expects = [
            Expectation(e["key"], e["count"], hidden=True)
            for e in spec.get("expects", [])
        ]
    return True


def has_holdout(name: str) -> bool:
    """Whether this task can currently be graded on unseen input."""
    task = ASSIGNMENTS.get(name)
    return bool(task and task.holdout_expects)


def catalogue() -> str:
    return json.dumps([a.to_json() for a in ASSIGNMENTS.values()])


def judge_assignment(name: str, student_source: str, holdout: bool = False) -> str:
    """Score a submission. With `holdout`, run it against unseen input."""
    a = ASSIGNMENTS.get(name)
    if a is None:
        return json.dumps({"verdict": "CE", "score": 0, "max_score": 0,
                           "cases": [{"name": "unknown task",
                                      "verdict": "CE", "message": name}]})
    out = a.judge(student_source, holdout=holdout).to_json()
    # `holdout` says what was asked for; `graded_on_holdout` says whether
    # unseen input was actually available. Without the private data the browser
    # must not claim it graded on the hold-out.
    out["holdout"] = holdout
    out["graded_on_holdout"] = bool(holdout and a.holdout_expects)
    return json.dumps(out)
