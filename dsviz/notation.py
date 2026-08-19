"""
The student-facing notation, and its linter.

Students never see Python. They write a small line-oriented notation; this
module parses it, reports errors with line numbers, and builds a simulation.

    process P1, P2, P3
    P1: event a
    P1 -> P2: m1
    assert P2.clock == [1, 1, 0]

Deliberately line-based and regex-parsed: the whole grammar fits on a page, and
error messages can point at a column. A parser generator would buy precedence
and nesting that this notation does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .core import Cluster
from .patterns import VectorClockRun, hash_partition
from .types import SIGNATURES, SymbolTable, Type, TypeError_, check_clock_literal


@dataclass
class Diagnostic:
    """One linter message, in the shape editors expect."""
    line: int                   # 1-based
    col: int
    severity: str               # "error" | "warning"
    message: str
    hint: str = ""

    def to_json(self) -> dict:
        return {"line": self.line, "col": self.col, "severity": self.severity,
                "message": self.message, "hint": self.hint}

    def __repr__(self):
        where = f"line {self.line}"
        return f"{self.severity}: {where}: {self.message}" + (
            f"\n  hint: {self.hint}" if self.hint else "")


class NotationError(Exception):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("\n".join(str(d) for d in diagnostics))


# --- grammar ------------------------------------------------------------
# One pattern per statement. Order matters: first match wins.

RULES = [
    ("process",  re.compile(r"^process\s+(?P<names>[\w\s,]+)$", re.I)),
    ("machine",  re.compile(r"^machine\s+(?P<name>\w+)(?:\s*:\s*(?P<props>.+))?$", re.I)),
    ("event",    re.compile(r"^(?P<who>\w+)\s*:\s*event\s+(?P<label>.+)$", re.I)),
    ("send",     re.compile(r"^(?P<frm>\w+)\s*->\s*(?P<to>\w+)\s*:\s*(?P<label>.+)$")),
    ("crash",    re.compile(r"^(?P<who>\w+)\s+crashes(?:\s+at\s+(?P<at>[\d.]+))?$", re.I)),
    ("assert",   re.compile(r"^assert\s+(?P<who>\w+)\.clock\s*==\s*\[(?P<vec>[\d,\s]+)\]$", re.I)),
    ("concurrent", re.compile(r"^assert\s+(?P<a>\w+)\s*\|\|\s*(?P<b>\w+)$", re.I)),
    ("before",   re.compile(r"^assert\s+(?P<a>\w+)\s*->>\s*(?P<b>\w+)$", re.I)),
    ("note",     re.compile(r"^note\s+(?P<text>.+)$", re.I)),
]


def _strip(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def parse(source: str) -> tuple[list[tuple], list[Diagnostic]]:
    """Parse to statements, collecting diagnostics rather than stopping at the
    first error — students should see every problem at once."""
    stmts, diags = [], []
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip(raw).strip()
        if not line:
            continue
        for kind, pat in RULES:
            m = pat.match(line)
            if m:
                stmts.append((kind, m.groupdict(), i))
                break
        else:
            diags.append(Diagnostic(
                i, 1, "error", f"cannot parse: {line!r}",
                hint="expected e.g. 'P1 -> P2: msg', 'P1: event a', "
                     "'assert P1.clock == [1,0,0]'"))
    return stmts, diags


def typecheck(source: str) -> tuple[SymbolTable, list[Diagnostic]]:
    """
    Statically check a program: every name declared before use, with the right
    type, and every clock literal the right width.

    Runs before any simulation, so an ill-typed program is rejected rather than
    producing a confusing result later. Errors are collected rather than thrown
    one at a time — the editor shows them all at once.
    """
    stmts, diags = parse(source)
    table = SymbolTable()

    # Pass 1: declarations. Every name gets a type up front.
    for kind, g, line in stmts:
        try:
            if kind == "process":
                for n in (x.strip() for x in g["names"].split(",")):
                    if n:
                        table.declare(n, Type.PROCESS, line)
            elif kind == "machine":
                table.declare(g["name"], Type.MACHINE, line)
        except TypeError_ as e:
            diags.append(Diagnostic(e.line, 1, "error", e.message, e.hint))

    if not len(table):
        diags.append(Diagnostic(
            1, 1, "error", "no processes declared",
            hint="start with e.g. 'process P1, P2, P3'"))
        return table, diags

    # Pass 2: uses. Each statement is checked against its signature.
    procs = table.names_of(Type.PROCESS)
    for kind, g, line in stmts:
        sig = SIGNATURES.get(kind)
        if not sig:
            continue
        for field, expected in sig.get("args", []):
            name = g.get(field)
            if name is None:
                continue
            try:
                table.require(name, expected, line, context=f"{kind!r}")
            except TypeError_ as e:
                diags.append(Diagnostic(e.line, 1, "error", e.message, e.hint))

        if sig.get("value") is Type.CLOCK:
            try:
                vec = [int(v) for v in g["vec"].split(",")]
                check_clock_literal(vec, len(procs), line, procs)
            except TypeError_ as e:
                diags.append(Diagnostic(e.line, 1, "error", e.message, e.hint))
            except ValueError:
                diags.append(Diagnostic(
                    line, 1, "error", "clock entries must be whole numbers"))

        if kind == "send" and g["frm"] == g["to"]:
            diags.append(Diagnostic(
                line, 1, "warning", f"{g['frm']} sends to itself",
                hint="a self-message creates no causal edge"))

    return table, diags


def lint(source: str) -> list[Diagnostic]:
    """
    Everything an editor should show: type errors first, then any causal
    contradiction found by actually running the program.
    """
    table, diags = typecheck(source)
    if any(d.severity == "error" for d in diags):
        return diags        # don't run an ill-typed program

    try:
        build(source, _checked=True)
    except NotationError as e:
        diags.extend(e.diagnostics)
    return diags


def build(source: str, *, _checked: bool = False) -> VectorClockRun:
    """
    Type-check, then execute, raising NotationError with a causal explanation.

    `_checked` skips the typing pass when the caller (lint) has already run it.
    """
    stmts, _ = parse(source)
    if not _checked:
        table, diags = typecheck(source)
        if any(d.severity == "error" for d in diags):
            raise NotationError([d for d in diags if d.severity == "error"])
    else:
        table, _ = typecheck(source)

    names = table.names_of(Type.PROCESS)
    run = VectorClockRun(*names)
    for kind, g, line in stmts:
        if kind in ("process", "machine"):
            continue
        if kind == "event":
            run.event(g["who"], g["label"].strip())
        elif kind == "send":
            run.send(g["frm"], g["to"], g["label"].strip())
        elif kind == "crash":
            at = float(g["at"]) if g.get("at") else None
            run.cluster.machines[g["who"]].crash(at=at)
        elif kind == "note":
            run.cluster.note(g["text"].strip())
        elif kind == "assert":
            expected = [int(v) for v in g["vec"].split(",")]
            try:
                run.assert_clock(g["who"], expected)
            except AssertionError as e:
                raise NotationError([Diagnostic(
                    line, 1, "error", str(e).splitlines()[0],
                    hint=str(e).split("\n", 1)[1].strip() if "\n" in str(e) else "")])
        elif kind == "concurrent":
            if not run.concurrent(g["a"], g["b"]):
                raise NotationError([Diagnostic(
                    line, 1, "error",
                    f"{g['a']} and {g['b']} are not concurrent",
                    hint=f"{g['a']}={run.clocks[g['a']]} and "
                         f"{g['b']}={run.clocks[g['b']]}: one dominates the other")])
        elif kind == "before":
            if not run.happens_before(g["a"], g["b"]):
                raise NotationError([Diagnostic(
                    line, 1, "error",
                    f"{g['a']} does not happen before {g['b']}",
                    hint=f"{g['a']}={run.clocks[g['a']]}, "
                         f"{g['b']}={run.clocks[g['b']]}")])
    return run
