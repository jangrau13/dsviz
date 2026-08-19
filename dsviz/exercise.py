"""
Which tasks an exercise consists of.

One installed package ships every task the course has, and each exercise wants
a few of them: the Spark exercise has no business offering a word-count
MapReduce in its dropdown. Rather than cutting the package up per exercise —
three packages to keep in step, which is the problem the package was meant to
end — an exercise names the tasks it wants and everything else stays hidden.

    # pyproject.toml, in the exercise
    [tool.dsviz]
    title = "Assignment 2 — Spark"
    tasks = ["t3-spark", "t6-telemetry"]

    [tool.dsviz.titles]
    "t3-spark" = "Task 1: word count in Spark"

The declaration lives in the exercise's own `pyproject.toml` because that file
already has to exist to depend on dsviz at all, and a second config file would
be a second thing to forget. An exercise that names nothing gets everything,
which is what a bare checkout of this repository wants.
"""

from __future__ import annotations

import pathlib
import tomllib


def config(root: pathlib.Path) -> dict:
    """The `[tool.dsviz]` table of the exercise at `root`, or an empty one."""
    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return {}
    try:
        data = tomllib.loads(manifest.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    table = data.get("tool", {}).get("dsviz", {})
    return table if isinstance(table, dict) else {}


def task_names(root: pathlib.Path) -> list[str]:
    """
    The tasks this exercise offers, in the order it wants them shown.

    Names that no longer exist are dropped rather than raising: a task renamed
    in the package should leave the exercise offering one task fewer, not
    refusing to start. `dsviz tasks` is where the mismatch is meant to show up.
    """
    from .assignment import ASSIGNMENTS

    declared = config(root).get("tasks")
    if not declared:
        return list(ASSIGNMENTS)
    return [n for n in declared if n in ASSIGNMENTS]


def unknown_tasks(root: pathlib.Path) -> list[str]:
    """Declared names the installed package does not have."""
    from .assignment import ASSIGNMENTS

    return [n for n in (config(root).get("tasks") or []) if n not in ASSIGNMENTS]


def titles(root: pathlib.Path) -> dict:
    """
    What this exercise calls each task, where that differs from the package.

    A task is reusable across exercises; its number is not. Vector clocks are
    the fourth thing the course covers and the second thing this exercise
    asks for, and a student reading "Task 4" in an exercise with three tasks
    is being told something untrue. The package keeps a stable id, the
    exercise supplies the heading.
    """
    given = config(root).get("titles", {})
    return {k: str(v) for k, v in given.items()} if isinstance(given, dict) else {}


def title_for(root: pathlib.Path, name: str, default: str) -> str:
    return titles(root).get(name, default)


def title(root: pathlib.Path) -> str:
    return str(config(root).get("title", ""))
