"""
What an exercise is, from the outside.

An exercise is a checkout with a `tasks.py` in it. That file says what the
exercise is called and which tasks it consists of, in the order it wants them
shown, and dsviz has none of its own — the language, the simulator, the editor
and the grader are general, and the tasks are somebody's course.

    # tasks.py, in the exercise
    TITLE = "Assignment 2 — Spark"
    TASKS = [SPARK_MEMORY, TELEMETRY, KMEANS]

This module used to be a scoping mechanism: one package shipped every task the
course had, and each exercise named the few it wanted in `[tool.dsviz]`. That
premise is gone. An exercise cannot name a task it does not have, so there is
nothing left to scope and nothing left to go out of step.
"""

from __future__ import annotations

import pathlib


def load(root: pathlib.Path) -> dict:
    """Load this exercise's tasks and return them, keyed by name."""
    from .assignment import load_exercise

    return load_exercise(root)


def task_names(root: pathlib.Path) -> list[str]:
    """The tasks this exercise offers, in the order it wants them shown."""
    return list(load(root))


def titles(root: pathlib.Path) -> dict:
    """
    What this exercise calls each task.

    A task is reusable across exercises; its number is not. Vector clocks are
    the fourth thing the course covers and the second thing one exercise asks
    for, and a student reading "Task 4" in an exercise with three tasks is
    being told something untrue. The heading is the exercise's to write, which
    is why it sits beside the task rather than in the language.
    """
    return {name: task.title for name, task in load(root).items()}


def title_for(root: pathlib.Path, name: str, default: str = "") -> str:
    """This exercise's heading for one task."""
    return titles(root).get(name, default)


def title(root: pathlib.Path) -> str:
    """What the exercise as a whole is called."""
    import importlib.util

    manifest = pathlib.Path(root) / "tasks.py"
    if not manifest.is_file():
        return ""
    spec = importlib.util.spec_from_file_location("dsviz_exercise_title",
                                                  manifest)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(getattr(module, "TITLE", ""))
