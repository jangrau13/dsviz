"""Render a MapReduce job, including a straggler, to video.

    manim -pql examples/mapreduce_video.py MapReduceFlow StragglerGantt
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz import map_reduce
from dsviz.shapes import dataflow, gantt
from dsviz.render_manim import FrameScene

cluster = map_reduce(
    {"doc1": "the cat sat", "doc2": "the dog ran", "doc3": "the cat ran"},
    partitions=2,
    speeds={"mapper-2": 0.35},      # mapper-2 is a straggler
)
trace = cluster.sorted_trace()

MapReduceFlow = FrameScene.with_frame(
    dataflow(trace, title="MapReduce"), "MapReduceFlow")
StragglerGantt = FrameScene.with_frame(
    gantt(trace, title="Stragglers"), "StragglerGantt")
