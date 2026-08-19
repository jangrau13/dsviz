"""
The notation's type system.

The student-facing notation is statically typed: every name is declared with a
type before use, every statement has a typing rule, and a program that breaks
one is rejected before it runs. The point is that a mistake is reported as a
type error at a line and column, not as a confusing result three phases later.

The Python API in `patterns.py` stays deliberately permissive — that is the
authoring surface for lecture videos, where guessing is convenient and the
only caller is the lecturer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Type(Enum):
    """Every type the notation knows. There are deliberately few."""
    PROCESS = "process"      # participates in message passing, has a clock
    MACHINE = "machine"      # a worker: does work, holds data, can fail
    MESSAGE = "message"      # payload in flight
    CLOCK = "clock"          # a vector of per-process counters
    INT = "int"
    FLOAT = "float"
    STRING = "string"

    def __str__(self):
        return self.value


@dataclass(frozen=True)
class Symbol:
    """A declared name, its type, and where it was declared."""
    name: str
    type: Type
    line: int
    props: dict = field(default_factory=dict)


class TypeError_(Exception):
    """Raised for a type violation. Carries the location for the editor."""

    def __init__(self, line: int, message: str, hint: str = ""):
        self.line, self.message, self.hint = line, message, hint
        super().__init__(message)


class SymbolTable:
    """
    Names in scope, with their types.

    Redeclaration is an error rather than a silent overwrite: in a notation
    this small, redeclaring a name is always a mistake.
    """

    def __init__(self):
        self._syms: dict[str, Symbol] = {}

    def declare(self, name: str, type_: Type, line: int, **props) -> Symbol:
        if name in self._syms:
            prev = self._syms[name]
            raise TypeError_(
                line, f"{name!r} is already declared as {prev.type} on line {prev.line}",
                hint="pick a different name, or remove the earlier declaration")
        sym = Symbol(name, type_, line, props)
        self._syms[name] = sym
        return sym

    def require(self, name: str, expected: Type, line: int, *,
                context: str = "") -> Symbol:
        """Look a name up and check its type. The core of the checker."""
        sym = self._syms.get(name)
        if sym is None:
            known = ", ".join(sorted(self._syms)) or "nothing yet"
            raise TypeError_(
                line, f"undeclared name {name!r}"
                      + (f" in {context}" if context else ""),
                hint=f"declare it first — in scope: {known}")
        if sym.type is not expected:
            raise TypeError_(
                line,
                f"{name!r} is a {sym.type}, but {expected} is required"
                + (f" in {context}" if context else ""),
                hint=f"{name!r} was declared as {sym.type} on line {sym.line}")
        return sym

    def of_type(self, type_: Type) -> list[Symbol]:
        return [s for s in self._syms.values() if s.type is type_]

    def names_of(self, type_: Type) -> list[str]:
        return [s.name for s in self.of_type(type_)]

    def __contains__(self, name: str) -> bool:
        return name in self._syms

    def __len__(self):
        return len(self._syms)


# --- typing rules -------------------------------------------------------
# One entry per statement form: what it needs, and what it produces. Keeping
# these as data means the checker and the documentation cannot drift apart.

SIGNATURES: dict[str, dict] = {
    "process":    {"declares": Type.PROCESS, "args": []},
    "machine":    {"declares": Type.MACHINE, "args": []},
    "event":      {"args": [("who", Type.PROCESS)]},
    "send":       {"args": [("frm", Type.PROCESS), ("to", Type.PROCESS)]},
    "crash":      {"args": [("who", Type.PROCESS)]},
    "assert":     {"args": [("who", Type.PROCESS)], "value": Type.CLOCK},
    "concurrent": {"args": [("a", Type.PROCESS), ("b", Type.PROCESS)]},
    "before":     {"args": [("a", Type.PROCESS), ("b", Type.PROCESS)]},
    "note":       {"args": []},
}


def check_clock_literal(vec: list[int], n_processes: int, line: int,
                        names: list[str]) -> None:
    """A clock must have exactly one entry per process, and none negative."""
    if len(vec) != n_processes:
        raise TypeError_(
            line,
            f"clock has {len(vec)} entries but there are {n_processes} processes",
            hint=f"expected one entry per process, in order: {', '.join(names)}")
    for i, v in enumerate(vec):
        if v < 0:
            raise TypeError_(
                line, f"clock entry {i} is negative ({v})",
                hint="counters only ever increase")
