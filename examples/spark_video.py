"""Render a Spark pipeline — its lineage and its timeline — to video.

    manim -pql examples/spark_video.py LineageScene GanttScene

dsviz ships no tasks of its own, so the exercise that owns them is loaded
first. Point EXERCISE at any checkout that has a `tasks.py`.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.assignment import load_exercise, starter_for
from dsviz.render_manim import to_manim

EXERCISE = pathlib.Path(os.environ.get(
    "EXERCISE",
    pathlib.Path(__file__).resolve().parents[2] / "BCS-DS-Assignment-2"))
TASK = os.environ.get("TASK", "a2-kmeans")

load_exercise(EXERCISE)
globals().update(to_manim(starter_for(TASK),
                          views=("lineage", "gantt"),
                          module=__name__))
