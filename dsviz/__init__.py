"""
dsviz — simulate decentralized systems, measure them, and render them.

One DSL for the course: MapReduce, Spark and vector clocks share a single
simulation core, a single type system and a single rendering pipeline.

    core      machines, messages, time, failure (SimPy-backed)
    patterns  exercise vocabulary: map_reduce, spark_job, VectorClockRun
    types     the notation's static type system
    notation  student-facing syntax, its type checker and linter
    metrics   non-functional properties: traffic, skew, tail, fault cost
    pricing   those properties as money: what a design costs and earns
    contest   verdicts and scoring
    shapes    renderer-agnostic diagram primitives
"""

from .contest import Case, Judge, Submission, Verdict, judge_notation
from .core import Cluster, Event, Machine, Trace
from .metrics import compare, measure, report
from .notation import Diagnostic, NotationError, build, lint, typecheck
from .pricing import (LineItem, PnL, PriceVector, RiskProfile, Scenario,
                      crosstab, detected, price, profile, share,
                      unsupported)
from .patterns import (Lineage, VectorClockRun, hash_partition, map_reduce,
                       normalize_inputs, spark_job)
from .shapes import Frame, Shape, dataflow, gantt, lineage, spacetime
from .types import SymbolTable, Type

__all__ = [
    # simulation
    "Cluster", "Machine", "Event", "Trace",
    # exercises
    "map_reduce", "spark_job", "Lineage", "VectorClockRun",
    "hash_partition", "normalize_inputs",
    # notation + types
    "build", "lint", "typecheck", "Diagnostic", "NotationError",
    "Type", "SymbolTable",
    # measurement + scoring
    "measure", "report", "compare",
    # money
    "PriceVector", "Scenario", "LineItem", "PnL", "RiskProfile",
    "price", "profile", "share", "detected", "unsupported", "crosstab",
    "Judge", "Case", "Verdict", "Submission", "judge_notation",
    # rendering
    "Frame", "Shape", "dataflow", "spacetime", "gantt", "lineage",
]
