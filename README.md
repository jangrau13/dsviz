# dsviz

One DSL for the Decentralized Systems course: **MapReduce, Spark, Vector
Clocks**. Simulate a cluster, measure how good a design is, render it as video
or as a live diagram in the browser.

## Why this shape

- **One language, not one per exercise.** The three exercises share machines,
  messages, failure and time, so they share a core. Exercise vocabulary is a
  thin layer on top — six notations would have hidden the structure they have
  in common.
- **Students never see Python.** They write a small line-oriented notation.
  The compiler is hidden behind an editor.
- **Strict types in the notation, permissive Python API.** A student's program
  is statically checked and rejected with a line number. The Python helpers
  used to author lecture videos stay liberal — the only caller is the lecturer.
- **Correctness is table stakes; the grade is non-functional.** Every working
  submission gets the same answer, so submissions are ranked on network
  traffic, load imbalance, tail latency and fault cost instead.

## Layout

| Module | What it does |
|---|---|
| `core.py` | Machines, messages, time, failure. SimPy-backed. |
| `patterns.py` | Exercise vocabulary: `map_reduce`, `spark_job`, `VectorClockRun` |
| `types.py` | The notation's static type system |
| `notation.py` | Student-facing syntax, type checker, linter |
| `metrics.py` | Non-functional properties measured from a trace |
| `contest.py` | Verdicts and scoring |
| `shapes.py` | Renderer-agnostic diagram primitives |
| `render_manim.py` | Shapes → video |

The pipeline is one-directional and each stage is independently testable:

```
simulation  →  Trace  →  shapes  →  Manim video
                                 →  browser SVG
```

`Trace` and `shapes` are plain data, which is what lets a lecture video and a
student's editor draw the same picture.

## Use

```bash
python3 -m venv .venv
.venv/bin/pip install simpy networkx manim

.venv/bin/python tests/run_all.py
.venv/bin/manim -pql examples/mapreduce_video.py MapReduceFlow StragglerGantt
```

### Simulate

```python
from dsviz import map_reduce, report

cluster = map_reduce(
    {"doc1": "the cat sat", "doc2": "the dog ran"},
    partitions=2,
    speeds={"mapper-2": 0.35},     # a straggler
    capacity=8,                    # visible skew above this
    crash=("mapper-1", 2.0),       # fails mid-job
)
print(report(cluster.sorted_trace()))
```

Inputs are liberal: a dict, a list, `[(name, text)]` pairs, a bare string, or a
path to a file or directory.

### The student notation

```
process P1, P2, P3

P1: event a
P1 -> P2: m1
P2: event b
P2 -> P3: m2

assert P3.clock == [2, 3, 1]
assert P1 ->> P3          # happens-before
```

`lint(source)` returns diagnostics with line numbers. Errors are causal, not
just syntactic:

```
error: line 8: P3: you claim [2, 3, 5], but the run gives [2, 3, 1].
  hint: P3 has not heard from P3 often enough for entry 2 to reach 5 (it is 1)
```

### Rank submissions

```python
from dsviz import compare
for rank, (name, score, m) in enumerate(compare(submissions), 1):
    print(rank, name, score, m["network_msgs"].value)
```

All four sample submissions below return identical answers; only their designs
differ:

| Submission | msgs | tail | imbalance |
|---|---|---|---|
| combiner | 30 | 1.00 | 1.10 |
| naive | 126 | 1.00 | 1.27 |
| straggler | 126 | 5.00 | 3.88 |

## Not done yet

- **Browser editor (Pyodide).** The plan: ship this package as WASM, with
  CodeMirror for the editor, live SVG from `shapes`, linter diagnostics inline,
  and video export. `shapes.Frame.to_json()` is the interface it needs.
- **A typed expression sub-language** for reduce functions and clock rules —
  preferred over embedding Lua, which would add a second VM and one
  dynamically typed hole in an otherwise statically checked language.
- **Per-phase metrics.** `parallelism` currently averages over the whole job,
  so a map-heavy job masks a reduce bottleneck. Splitting per phase sharpens it.
