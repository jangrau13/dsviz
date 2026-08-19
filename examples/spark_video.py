"""Render a Spark pipeline — its lineage and its timeline — to video.

    manim -pql examples/spark_video.py LineageScene GanttScene
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.assignment import starter_for
from dsviz.render_manim import to_manim

globals().update(to_manim(starter_for("a2-kmeans"),
                          views=("lineage", "gantt"),
                          module=__name__))
