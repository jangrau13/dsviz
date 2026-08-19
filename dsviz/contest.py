"""
Scoring: run a submission against test cases and return a verdict.

Enables competitive framing — a leaderboard, or simply pass/fail with a reason.
Verdicts follow the judge conventions students recognise (AC, WA, TLE, RE),
because they are terse, unambiguous, and already familiar.

Cases can be hidden: students see the verdict and a causal reason, not the
expected answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


def label_for(verdict: "Verdict") -> str:
    """Plain English, for anything a student reads. 'AC' means nothing to them."""
    return LABELS.get(verdict.name, verdict.name)


class Verdict(Enum):
    AC = "accepted"
    WA = "wrong answer"
    TLE = "time limit exceeded"
    RE = "runtime error"
    CE = "compile error"          # ill-typed notation

    def __str__(self):
        return self.name


LABELS = {
    "AC": "passed",
    "WA": "wrong answer",
    "TLE": "took too long",
    "RE": "crashed while running",
    "CE": "does not compile",
}


@dataclass
class CaseResult:
    name: str
    verdict: Verdict
    message: str = ""
    seconds: float = 0.0
    score: float = 0.0
    hidden: bool = False

    def to_json(self) -> dict:
        d = {"name": self.name, "verdict": self.verdict.name,
             "label": label_for(self.verdict),
             "seconds": round(self.seconds, 4), "score": self.score}
        # A hidden case reveals the verdict but never the expected value.
        d["message"] = "" if self.hidden and self.verdict is Verdict.WA else self.message
        return d


@dataclass
class Submission:
    """The outcome of judging one submission."""
    results: list[CaseResult] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        """First non-AC verdict, in case order — the judge convention."""
        for r in self.results:
            if r.verdict is not Verdict.AC:
                return r.verdict
        return Verdict.AC

    @property
    def score(self) -> float:
        return sum(r.score for r in self.results)

    @property
    def max_score(self) -> float:
        return float(len(self.results))

    def to_json(self) -> dict:
        return {"verdict": self.verdict.name, "label": label_for(self.verdict),
                "score": self.score, "max_score": self.max_score,
                "cases": [r.to_json() for r in self.results]}

    def __repr__(self):
        head = f"{self.verdict} ({self.score:g}/{self.max_score:g})"
        return "\n".join([head] + [
            f"  {r.verdict!s:<4} {r.name}" + (f" — {r.message}" if r.message else "")
            for r in self.results])


@dataclass
class Case:
    """
    One test case.

    `check` receives whatever `run` returned and either returns True/False, or
    raises AssertionError carrying an explanation.
    """
    name: str
    check: Callable[[object], bool]
    hidden: bool = False
    weight: float = 1.0
    time_limit: float = 5.0


class Judge:
    """
    Runs a submission against cases.

    `run` is any callable producing a result to check — a parsed notation
    program, a simulated cluster, or a plain value.
    """

    def __init__(self, *cases: Case):
        self.cases = list(cases)

    def judge(self, run: Callable[[], object]) -> Submission:
        sub = Submission()
        for case in self.cases:
            started = time.perf_counter()
            try:
                value = run()
                elapsed = time.perf_counter() - started
                if elapsed > case.time_limit:
                    sub.results.append(CaseResult(
                        case.name, Verdict.TLE,
                        f"took {elapsed:.2f}s, limit {case.time_limit:g}s",
                        elapsed, 0.0, case.hidden))
                    continue
                ok = case.check(value)
                sub.results.append(CaseResult(
                    case.name, Verdict.AC if ok else Verdict.WA,
                    "" if ok else "check returned false",
                    elapsed, case.weight if ok else 0.0, case.hidden))
            except AssertionError as e:
                sub.results.append(CaseResult(
                    case.name, Verdict.WA, str(e),
                    time.perf_counter() - started, 0.0, case.hidden))
            except Exception as e:
                sub.results.append(CaseResult(
                    case.name, Verdict.RE, f"{type(e).__name__}: {e}",
                    time.perf_counter() - started, 0.0, case.hidden))
        return sub


def judge_notation(source: str, *cases: Case) -> Submission:
    """
    Judge a notation program: type errors become CE, everything else is checked
    against the cases.
    """
    from .notation import NotationError, build, lint

    diags = [d for d in lint(source) if d.severity == "error"]
    if diags:
        sub = Submission()
        sub.results.append(CaseResult(
            "compile", Verdict.CE,
            f"line {diags[0].line}: {diags[0].message}"))
        return sub

    return Judge(*cases).judge(lambda: build(source))
