"""
Multi-file programs.

A task can span several files: helpers in one, the entry points in another,
exactly as a student would organise real code. Files are combined in dependency
order before checking, so the type checker sees one program and can still point
at the file and line a mistake came from.

    # helpers.ds
    def warmest(readings: [int]) -> int:
        return max(readings)

    # main.ds
    use helpers

    def report(city: string, readings: [int]) -> int:
        return warmest(readings)

(Written in a domain no task uses: this module ships to students, so an
example here is documentation whether or not it is on the docs site.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .notation import Diagnostic

USE_RE = re.compile(r"^use\s+(?P<name>[\w./-]+)\s*$", re.I | re.M)


@dataclass
class SourceFile:
    name: str
    text: str

    def uses(self) -> list[str]:
        """The files this one depends on, in the order they are named."""
        return [m.group("name").removesuffix(".ds")
                for m in USE_RE.finditer(self.text)]

    def without_uses(self) -> str:
        """The body, with `use` lines blanked so line numbers do not shift."""
        return USE_RE.sub(lambda m: "", self.text)


@dataclass
class Project:
    """
    A set of files that make up one program.

    `combine()` returns the merged source plus a line map, so a diagnostic
    reported against the merged program can be translated back to the file and
    line the student is looking at.
    """
    files: dict = field(default_factory=dict)      # name -> SourceFile
    entry: str = "main"

    @classmethod
    def of(cls, files: dict, entry: str = "main") -> "Project":
        return cls({n: SourceFile(n, t) for n, t in files.items()}, entry)

    def add(self, name: str, text: str) -> "Project":
        self.files[name] = SourceFile(name, text)
        return self

    # -- ordering ------------------------------------------------------
    def order(self) -> tuple[list[str], list[Diagnostic]]:
        """
        Depth-first over `use`, so a file is always placed after what it needs.

        Cycles and missing files are reported rather than raised: a student
        mid-edit should see a diagnostic, not a crash.
        """
        diags: list[Diagnostic] = []
        order: list[str] = []
        state: dict[str, str] = {}          # name -> visiting | done

        def visit(name: str, requested_by: str | None, line: int):
            if state.get(name) == "done":
                return
            if state.get(name) == "visiting":
                diags.append(Diagnostic(
                    line, 1, "error", f"circular use of {name!r}",
                    hint=f"{name} and {requested_by} each need the other"))
                return
            f = self.files.get(name)
            if f is None:
                diags.append(Diagnostic(
                    line, 1, "error", f"no file called {name!r}",
                    hint=f"files here: {', '.join(sorted(self.files)) or 'none'}"))
                return
            state[name] = "visiting"
            for dep in f.uses():
                visit(dep, name, _line_of_use(f.text, dep))
            state[name] = "done"
            order.append(name)

        start = self.entry if self.entry in self.files else next(iter(self.files), "")
        if start:
            visit(start, None, 1)
        # Anything not reached from the entry still gets checked, so a student
        # sees errors in a file they are editing even before they `use` it.
        for name in self.files:
            visit(name, None, 1)
        return order, diags

    # -- combining -----------------------------------------------------
    def combine(self) -> tuple[str, list[tuple[int, str, int]], list[Diagnostic]]:
        """
        Merge into one program.

        Returns (source, line_map, diagnostics), where line_map[i] is
        (merged_line, file, original_line) for translating diagnostics back.
        """
        order, diags = self.order()
        parts: list[str] = []
        line_map: list[tuple[int, str, int]] = []
        merged_line = 1

        for name in order:
            body = self.files[name].without_uses()
            for i, text in enumerate(body.splitlines(), start=1):
                parts.append(text)
                line_map.append((merged_line, name, i))
                merged_line += 1
            parts.append("")                 # keep files apart
            line_map.append((merged_line, name, 0))
            merged_line += 1

        return "\n".join(parts) + "\n", line_map, diags

    def locate(self, line_map: list, merged_line: int) -> tuple[str, int]:
        """Translate a line in the merged program back to (file, line)."""
        for ml, name, original in line_map:
            if ml == merged_line:
                return name, max(original, 1)
        return self.entry, merged_line


def _line_of_use(text: str, dep: str) -> int:
    for i, line in enumerate(text.splitlines(), start=1):
        m = USE_RE.match(line.strip())
        if m and m.group("name").removesuffix(".ds") == dep:
            return i
    return 1
