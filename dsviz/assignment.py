"""
Assignments: the criteria a submission is judged against.

Students write map, reduce and partition. They do *not* write their own tests —
`expect` and `budget` belong to the assignment, are set by the lecturer, and
may be hidden. A student who writes their own passing criteria has not been
assessed.

**No task lives here.** This module is the shape of a task and the machinery
for judging one; which tasks exist is the exercise's business, declared in its
own `tasks.py` and loaded by `register`. dsviz shipped a particular course's
fourteen tasks for a while, which meant that course could not renumber a task
without a commit to the language, and nobody else could use the language
without inheriting its assignments.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from .contest import CaseResult, Submission, Verdict
from .metrics import measure
from .notation_mr import BUDGET_METRICS


# The data files an exercise's tasks read — `textFile("climate.csv")` and the
# like. Set by `register`; None until an exercise has been loaded. Starters are
# not here: they are Python strings on the task itself, see `Assignment`.
TASKS: "pathlib.Path | None" = None


@dataclass
class Expectation:
    """
    A correctness check: this key must end with this value.

    A count, for the jobs that count. But MapReduce is not counting, and the
    value type is the job's to choose: an inverted index reduces a term to the
    documents it appears in, so the expected answer there is a string. The
    field keeps its name because every existing task is a count.
    """
    key: str
    count: int | str
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
    # "clocks" is a program of `@process` machines, which the same runtime
    # runs — the dialect names the exercise, not a separate builder. Leaving it
    # out meant it fell through to MapReduce, so every clocks task failed to
    # hand in with "no input splits declared": `detect_dialect` says "clocks",
    # nothing here claimed it, and the default claimed everything.
    # Spark joined this list once its pipelines became part of the one
    # language: a Spark program declares machines, builds a world and runs a
    # job like every other, so it is built by the same builder.
    if dialect in ("rpc", "clocks", "spark"):
        from .runtime import build
        return build(source, seed=seed)
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
    #: What the editor opens with. A string, not a path to one: a `.ds` file
    #: sitting in the checkout is a file a student can edit, and then the
    #: scaffold and the work are the same object and the editor is optional.
    #: The way in is the editor, and the way out is a hand-in it stamped.
    starter: str = ""
    expects: list = field(default_factory=list)
    holdout_expects: list = field(default_factory=list)
    budgets: list = field(default_factory=list)
    requires: list = field(default_factory=list)


    def program(self, student_source: str, *, holdout: bool = False) -> str:
        """The full program: the task's input plus the student's code."""
        setup = (self.holdout or self.setup) if holdout else self.setup
        return f"{setup.rstrip()}\n\n{student_source}" if setup else student_source

    def visible_criteria(self) -> list[dict]:
        """What the student is allowed to see."""
        out = [{"kind": "require", "text": r.name, "why": r.why, "hidden": False}
               for r in self.requires]
        for e in self.expects:
            shown = f'"{e.count}"' if isinstance(e.count, str) else e.count
            out.append({"kind": "expect", "text": f"{e.key} = {shown}",
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


def _diagnose(key: str, want, got, cluster) -> str:
    """
    Say what is probably wrong, not just that something is.

    The shape of the error usually identifies the function: too high across the
    board points at reduce, a missing key at map, everything equal to one at a
    reduce that is not aggregating.
    """
    outputs = {e.detail["key"]: e.detail["value"]
               for e in cluster.trace.of_kind("output")}

    if isinstance(want, str):
        return _diagnose_text(key, want, got, outputs)

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


def _diagnose_text(key: str, want: str, got, outputs: dict) -> str:
    """
    Say what is wrong with a text answer, in the terms the job is about.

    A posting list has three ways to be wrong and they are not the same
    mistake. The words can be right and the order wrong, which means the
    answer depends on which mapper finished first. A document can be repeated,
    which means nothing removed the repeats. Or the documents themselves are
    wrong, which is a map or a partition problem.
    """
    if key not in outputs:
        return (f"nothing came out for {key!r} — does your map emit it? "
                f"(it emitted: {', '.join(sorted(outputs)[:6]) or 'nothing'})")

    text = str(got)
    mine, theirs = text.split(), want.split()

    if sorted(mine) == sorted(theirs):
        return (f"got {text!r}, which is the right documents in the wrong "
                "order — the answer must not depend on which mapper finished "
                "first")

    if len(mine) != len(set(mine)) and sorted(set(mine)) == sorted(theirs):
        repeated = sorted({d for d in mine if mine.count(d) > 1})
        verb = "appears" if len(repeated) == 1 else "appear"
        return (f"got {text!r} — {', '.join(repeated)} {verb} more than "
                "once, so a document is being counted once per occurrence "
                "rather than once")

    extra = sorted(set(mine) - set(theirs))
    missing = sorted(set(theirs) - set(mine))
    detail = ", ".join(
        ([f"should not be there: {', '.join(extra)}"] if extra else [])
        + ([f"missing: {', '.join(missing)}"] if missing else []))
    return f"got {text!r} — {detail or 'not what was expected'}"


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
# --- the registry -------------------------------------------------------
# Empty until an exercise is loaded. Callers hold on to this dict rather than
# rebinding it, so filling it in place is what keeps `from .assignment import
# ASSIGNMENTS` working from anywhere.

GOALS: dict = {}
ASSIGNMENTS: dict = {}


def register(tasks, goals: dict | None = None, *, tasks_dir=None) -> dict:
    """
    Declare which tasks exist. Replaces whatever was registered before.

    `tasks` is a list of `Assignment`, in the order the exercise wants them
    shown; `goals` is that exercise's learning objectives, keyed the way its
    tasks name them. Both come from the exercise, because both are the
    exercise's to decide.
    """
    global TASKS
    ASSIGNMENTS.clear()
    ASSIGNMENTS.update({a.name: a for a in tasks})
    GOALS.clear()
    GOALS.update(goals or {})
    if tasks_dir is not None:
        TASKS = pathlib.Path(tasks_dir)
    return ASSIGNMENTS


def load_exercise(root) -> dict:
    """
    Load an exercise's own tasks, from `tasks.py` in its checkout.

    The file defines `TASKS` — a list of `Assignment` — and optionally
    `GOALS`. It is ordinary Python because a task's criteria are ordinary
    values, and because the exercise author is already writing Python to
    describe a program that runs in this language.

    Returns the registry. A checkout with no `tasks.py` has no tasks, and
    ends up with an empty registry rather than an inherited one — leaving
    whatever was loaded last in place would mean opening a directory with no
    manifest and being offered somebody else's tasks. The editor reports an
    empty dropdown; nothing raises.
    """
    import importlib.util

    root = pathlib.Path(root)
    manifest = root / "tasks.py"
    if not manifest.exists():
        return register([], {})

    spec = importlib.util.spec_from_file_location("dsviz_exercise_tasks",
                                                  manifest)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return register(getattr(module, "TASKS", []),
                    getattr(module, "GOALS", {}),
                    tasks_dir=root / "tasks")


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
