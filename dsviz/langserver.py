"""
The language server.

One entry point the editor talks to. It owns everything the browser needs:
parse, lint, type-check, run, lay out, measure, and describe symbols for
completions and hovers.

Runs in the browser under Pyodide, so there is no backend and no round trip —
the same code that renders lecture videos also powers the editor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lark import UnexpectedInput

from .grammar import parser
from .notation import Diagnostic, NotationError

# --- dialects -----------------------------------------------------------
# One language, three exercises. Which statements a program uses tells us
# which exercise it is, so the student never declares a mode.

MAPREDUCE, SPARK, RPC, CLOCKS = "mapreduce", "spark", "rpc", "clocks"


def detect_dialect(source: str) -> str:
    """Pick the exercise from what the program actually contains."""
    import re
    if re.search(r"^\s*@machine\b", source, re.M):
        return RPC
    if re.search(r"^\s*(executors\b|input\s+\w+\s*:)|textFile\s*\(", source, re.I | re.M):
        return SPARK
    if re.search(r"^\s*@process\b|\.clock\s*==|\->>", source, re.M):
        return CLOCKS
    return MAPREDUCE


def distribution(source: str, runs: int = 100) -> str:
    """
    Run a program many times and return the spread of results, as JSON.

    The editor draws one run; this answers the different question of what
    tends to happen. Nothing is rendered, so a hundred runs cost about what
    one drawing does.
    """
    from .runtime import evaluate

    try:
        return json.dumps(evaluate(source, runs=runs))
    except NotationError as err:
        return json.dumps({"error": [d.to_json() for d in err.diagnostics]})


# --- documentation ------------------------------------------------------
# The single source of truth for hovers, completions and the docs page.

@dataclass
class SymbolDoc:
    name: str
    signature: str
    summary: str
    detail: str = ""
    dialects: tuple = ()
    example: str = ""

    def to_json(self) -> dict:
        return {"name": self.name, "signature": self.signature,
                "summary": self.summary, "detail": self.detail,
                "dialects": list(self.dialects), "example": self.example}


DOCS: list[SymbolDoc] = [
    # --- describing the system ---
    SymbolDoc("class", "@kind\nclass Name:", "A kind of machine.",
              "The decorator says what the class is to the simulator. "
              "@machine both answers calls and makes them, @mapper and "
              "@reducer are the two halves of a job, and @process carries a "
              "clock. A class is only a kind of machine. What runs is an "
              "instance of it.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@machine\nclass Ledger:\n"
              "    @duration(0.4)\n"
              "    def balance(account: string) -> int:\n"
              "        return 120"),
    SymbolDoc("instance", "name = Kind(speed=N)", "A machine that exists.",
              "Declaring a class runs nothing. Each instance carries its "
              "own settings, so two machines of one kind can differ. That is "
              "how you make a straggler.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "fast = Worker(speed=1.0)\nslow = Worker(speed=0.3)"),
    SymbolDoc("World", "world = World(machines=[...])", "The system to run in.",
              "Everything about the setting lives here and nothing about "
              "the computation, so you can run one job in a fast world and "
              "then in a broken one. Without a world, a job has nowhere to "
              "run.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "world = World(machines=[m1, m2, r1])"),
    SymbolDoc("run", "world.run(job [, on=[...]])", "Run a job in the world.",
              "`on` gives the job a subsystem instead of the whole world, "
              "so you can run one job on three machines and then on ten.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "world.run(job)\nworld.run(job, on=[m1, r1])"),
    SymbolDoc("times", "Job(..., times=N)", "How many rounds the job runs.",
              "The job runs N times over. That is what you want in a video, "
              "and it is what makes an unreliable run worth watching, because "
              "the same call does not fail every time.",
              (MAPREDUCE, SPARK, RPC, CLOCKS), "job = Calls(run=story, times=3)"),
    SymbolDoc("error_rate", "Kind(error_rate=P)", "How likely a machine is to break.",
              "0.25 means each piece of work it does has one chance in four "
              "of breaking it. The draw is random, so no two runs are alike "
              "and one run never settles a question. Every machine runs this "
              "risk: a mapper grinding through its split is as exposed as a "
              "service answering a request. What happens after it breaks is "
              "said separately, with on_crash.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "flaky = Ledger(speed=1.0, error_rate=0.25)"),
    SymbolDoc("on_crash", 'Kind(on_crash="stay_dead" | "restart")',
              "What this machine does after it breaks.",
              "How often a machine breaks is only half of how it behaves. "
              "A machine that stays down and one that is back in two seconds "
              "fail at the same rate and behave nothing alike, because "
              "retries help the second and are wasted on the first. The "
              "default is \"stay_dead\", which leaves it down until something "
              "restarts it by hand. Coming back does not bring back what it "
              "was holding, so a restarted mapper runs its splits again.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'flaky = Ledger(error_rate=0.25, on_crash="restart", '
              'restart_after=1.5)'),
    SymbolDoc("restart_after", "Kind(restart_after=T)",
              "How long a restarting machine is down.",
              "Seconds between breaking and answering again. It means "
              'something only alongside on_crash="restart". The gap shows on '
              "the timeline as the machine sitting idle, and that idle time "
              "is what you weigh against losing the work outright.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              'slow_to_recover = Worker(error_rate=0.2, on_crash="restart", '
              'restart_after=3.0)'),
    SymbolDoc("duration", "@duration(T)", "How long a method takes.",
              "Seconds of work at speed 1.0. A machine with speed 0.5 takes "
              "twice as long over the same method.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "@duration(0.4)\ndef balance(account: string) -> int:\n"
              "    return 120"),
    SymbolDoc("speed", "Kind(speed=N)", "Relative speed of one machine.",
              "1.0 is nominal and 0.25 takes four times as long. This is "
              "how you make a straggler.",
              (MAPREDUCE, SPARK, RPC, CLOCKS), "slow = Worker(speed=0.25)"),
    SymbolDoc("crash", "machine.crash()", "Take a machine down.",
              "The machine loses its in-memory state, and messages already "
              "in flight to it are dropped.",
              (MAPREDUCE, SPARK, RPC), "bank.crash()"),
    SymbolDoc("restart", "machine.restart()", "Bring a machine back.",
              "It comes back with no state, so anything it held has to be "
              "recomputed.",
              (MAPREDUCE, SPARK, RPC), "bank.restart()"),

    # --- writing functions ---
    SymbolDoc("def", "def name(param: type) -> type:", "A function you write.",
              "Every parameter and the return type is written down, and "
              "nothing is inferred. Writing them is what lets a job check that "
              "a function fits the position it is passed to.",
              (MAPREDUCE, SPARK, RPC, CLOCKS),
              "def hottest(city: string, readings: [int]) -> int:\n"
              "    top: int = 0\n"
              "    for reading: int in readings:\n"
              "        if reading > top:\n"
              "            top: int = reading\n"
              "    return top"),
    SymbolDoc("emit", "emit(key, value)", "Produce one intermediate pair.",
              "Only the function passed as the job's map may emit. The key "
              "chooses a reducer by its hash, and the value is whatever the "
              "reducer takes.", (MAPREDUCE,), "emit(city, reading)"),
    SymbolDoc("Calls", "job = Calls(run=f)", "A job that is a sequence of calls.",
              "The work is one function, and the job is that function run "
              "in the world. Nothing has to be wrapped in a machine to hold "
              "it. A MapReduce job has the same shape and takes three "
              "functions instead of one.",
              (RPC, CLOCKS, SPARK),
              "def story() -> void:\n"
              '    chf: int = bank.balance("savings")\n\n'
              "job = Calls(run=story)\nworld.run(job)"),
    SymbolDoc("MapReduce", "job = MapReduce(map=f, reduce=g, partition=h)",
              "Wire your functions into a job.",
              "A function is the mapper because it was passed as the "
              "mapper, and it is accepted there only if its signature fits. "
              "Its name has no say in it. `combine=` adds a combiner.",
              (MAPREDUCE,),
              "job = MapReduce(map=readSensor, reduce=hottest, "
              "partition=spread)"),

    # --- calling across the network ---
    SymbolDoc("call", "machine.method(arg [, deadline=T] [, retries=N])",
              "Make a synchronous call.",
              "The caller waits for the round trip, so a slow server shows up "
              "as caller idle time. Statuses follow gRPC: ok, unavailable, "
              "unimplemented, deadline_exceeded.",
              (RPC, CLOCKS, SPARK),
              'chf: int = bank.balance("savings", deadline=0.5, retries=2)'),

    # --- checks the exercise sets ---
    SymbolDoc("budget", "budget METRIC < N", "A non-functional limit.",
              "Correctness is the floor. Budgets are what separate a good "
              "design from one that merely works. The metrics are network, "
              "makespan, imbalance, tail, memory and faults.",
              (MAPREDUCE, SPARK), "budget network < 40"),
    SymbolDoc("expect", "expect KEY = N", "Assert a final count.",
              "The correctness check.", (MAPREDUCE, SPARK), "expect zurich = 3"),
    SymbolDoc("note", "note TEXT", "A caption on the diagram.",
              "Shown at this point in the run. Useful for narrating a video.",
              (MAPREDUCE, SPARK, RPC, CLOCKS), "note the shuffle starts here"),

    # --- Spark pipelines ---
    SymbolDoc("textFile", "textFile(input)", "Create an RDD from input.",
              "The start of every pipeline.", (SPARK,),
              "rows = textFile(readings)"),
    SymbolDoc("flatMap", ".flatMap(expr)", "One record in, many out.",
              "Narrow, so it needs no shuffle and pipelines inside the "
              "current stage.",
              (SPARK,), "rows.flatMap(split(value))"),
    SymbolDoc("mapToPair", ".mapToPair(key, value)", "Turn records into pairs.",
              "Narrow. It produces a pair RDD, which is what the byKey "
              "operations need.", (SPARK,), "rows.mapToPair(value, 1)"),
    SymbolDoc("reduceByKey", ".reduceByKey(a + b)", "Combine values per key.",
              "Wide, so it forces a shuffle and begins a new stage. It "
              "combines on the map side first, which groupByKey does not.",
              (SPARK,), "readings.reduceByKey(a + b)"),
    SymbolDoc("groupByKey", ".groupByKey()", "Gather all values per key.",
              "Wide, and it ships everything. reduceByKey moves less data "
              "for the same answer.",
              (SPARK,), "readings.groupByKey()"),
    SymbolDoc("filter", ".filter(expr)", "Keep records matching a condition.",
              "Narrow.", (SPARK,), "totals.filter(value > 1)"),
    SymbolDoc("cache", ".cache()", "Keep this RDD in memory.",
              "Without it, every action recomputes the whole lineage.",
              (SPARK,), "totals.cache()"),
    SymbolDoc("collect", ".collect()", "An action: bring results back.",
              "Spark is lazy, so nothing runs until an action asks for a "
              "result.",
              (SPARK,), "totals.collect()"),
    SymbolDoc("lose", "lose RDD", "Lose a cached partition.",
              "Shows recomputation from lineage, which is what Spark does "
              "and MapReduce cannot.", (SPARK,), "lose totals"),

    # --- clocks ---
    SymbolDoc("assert", "assert P.clock == [..]", "Check a claimed clock.",
              "Write 'assert A || B' for concurrency and 'assert A ->> B' "
              "for happens-before. A wrong claim is reported causally.",
              (CLOCKS,), "assert P3.clock == [2, 3, 1]"),
]

# How the reference is organised.
#
# By what a construct is, never by what it is used for. Grouping the pages
# MapReduce / Spark / calls / clocks made the reference a tour of the course's
# topics, which told a student which technology to reach for. That question
# belongs to the exercise. These groups answer a different one: how do I
# declare a machine, how do I build a world, how do I write a function.
#
# The site (docs.py) and the editor's own panel both read this, so they cannot
# describe the language differently.
GROUPS = [
    ("functions", "Functions", "What you write, and how it is written.",
     ["def", "emit"]),
    ("machines", "Machines",
     "Declaring a kind of machine, and making ones that exist.",
     ["class", "instance", "speed", "duration", "error_rate", "on_crash",
      "restart_after", "crash", "restart", "call"]),
    ("worlds", "Worlds",
     "The machines that exist together, and running in them.",
     ["World", "run"]),
    ("jobs", "Jobs", "Handing your functions to something that runs them.",
     ["Calls", "MapReduce", "times"]),
    ("datasets", "Datasets",
     "Values built from other values, and what is remembered about how.",
     ["textFile", "flatMap", "mapToPair", "reduceByKey", "groupByKey",
      "filter", "cache", "collect", "lose"]),
    ("checks", "Checks", "Statements that assert something about a run.",
     ["budget", "expect", "assert", "note"]),
]

DOC_INDEX = {d.name: d for d in DOCS}

# Every builtin, documented in full: what it does, why it exists, and one
# line a student could type. Generated from `expr.BUILTINS` at import so the
# two can never drift — a builtin added without documentation is a test
# failure rather than an empty hover.
#
# Kept in the same words a bachelor student would use. These are the only
# functions the language provides; anything shaped like an exercise is a
# function the student writes.
BUILTIN_HELP = {
    "split": ("Cut a string into its words.",
              "Any run of whitespace separates two pieces, so double "
              "spaces and newlines produce no empty ones. Punctuation stays "
              "attached to the piece it touches.",
              'for part: string in split("zurich bern chur"):'),
    "lower": ("The same string in lower case.",
              "Every letter is folded, and digits and punctuation are left "
              "alone. Two strings that differ only in case come out equal.",
              'lower("Zurich HB")      # "zurich hb"'),
    "upper": ("The same string in upper case.",
              "The mirror of lower. Neither one changes how long the string "
              "is.",
              'upper("chur")           # "CHUR"'),
    "strip": ("The string without leading or trailing spaces.",
              "Useful when input arrives with ragged edges. It touches only "
              "the ends, so spaces inside the string stay.",
              'strip("  chur  ")       # "chur"'),
    "len": ("How many items are in a list, or characters in a string.",
            "Works on both, and always returns an int.",
            'len(readings)           # how many items'),
    "sum": ("The total of a list of ints.",
            "Adds every item and returns the result. An empty list totals "
            "zero rather than failing.",
            'sum(readings)'),
    "max": ("The largest int in a list, or 0 if it is empty.",
            "An empty list gives 0 rather than an error, so nothing has to "
            "guard the call.",
            'max(readings)'),
    "min": ("The smallest int in a list, or 0 if it is empty.",
            "An empty list gives 0 here too, exactly as it does for max.",
            'min(readings)'),
    "hash": ("A number derived from a string, the same every time.",
             "The same string always gives the same number, and nothing "
             "else changes it: not timing, and not which machine asks. Java "
             "and JavaScript use the same 31-hash, so a number computed here "
             "matches one computed there.",
             'hash("zurich")'),
    "abs": ("The size of a number, ignoring its sign.",
            "Returns the number without its sign, so -7 and 7 both give 7.",
            'abs(-7)                 # 7'),
}


def _builtin_docs() -> dict:
    """One line per builtin, from the signature and the help table."""
    from .expr import BUILTINS
    out = {}
    for name, (_params, _ret, _impl, signature) in BUILTINS.items():
        summary = BUILTIN_HELP.get(name, ("", "", ""))[0]
        out[name] = f"{signature} — {summary}" if summary else signature
    return out


BUILTIN_DOCS = _builtin_docs()


# --- the server ---------------------------------------------------------

@dataclass
class Analysis:
    """Everything the editor needs after one edit."""
    dialect: str
    diagnostics: list = field(default_factory=list)
    frame: dict | None = None
    gantt: dict | None = None
    metrics: list = field(default_factory=list)
    verdict: dict | None = None
    outputs: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "dialect": self.dialect,
            "diagnostics": self.diagnostics,
            "frame": self.frame,
            "gantt": self.gantt,
            "metrics": self.metrics,
            "verdict": self.verdict,
            "outputs": self.outputs,
        })


def syntax_check(source: str) -> list[Diagnostic]:
    """Grammar-level errors, with column-accurate positions from Lark."""
    try:
        parser().parse(source if source.endswith("\n") else source + "\n")
        return []
    except UnexpectedInput as e:
        expected = ""
        accepts = getattr(e, "accepts", None) or getattr(e, "expected", None)
        if accepts:
            words = sorted(_readable(t) for t in list(accepts)[:6])
            expected = "expected " + ", ".join(w for w in words if w)
        return [Diagnostic(getattr(e, "line", 1) or 1,
                           getattr(e, "column", 1) or 1,
                           "error", "syntax error here", expected)]
    except Exception as e:                      # grammar bugs must not hide
        return [Diagnostic(1, 1, "error", f"could not parse: {e}", "")]


def _readable(terminal: str) -> str:
    """Turn a terminal name into something a student can act on."""
    t = str(terminal)
    if t.startswith("KW_"):
        return f"'{t[3:].lower()}'"
    return {"NAME": "a name", "NUMBER": "a number", "STRING": "a quoted string",
            "_NL": "a new line", "COMPARE": "a comparison"}.get(t, "")


def analyse_project(files: dict, entry: str = "main", assignment: str = "") -> str:
    """
    Analyse a multi-file program.

    `files` maps file name to contents. Files are merged in dependency order
    (see `project.Project`), and every diagnostic is translated back to the
    file and line the student is actually looking at.
    """
    from .project import Project

    proj = Project.of(files, entry)
    merged, line_map, order_diags = proj.combine()

    payload = json.loads(analyse(merged, assignment))
    # Diagnostics about the program as a whole — a missing input declaration,
    # no reducer count — carry no real position, and reporting them at line 1
    # would blame whichever file merged first. They belong to the entry file,
    # which is where a student would fix them.
    whole_program = {"no input splits declared", "no processes declared"}
    for d in payload.get("diagnostics", []):
        if d["line"] <= 1 and (d["message"] in whole_program
                               or "defaulting to" in d["message"]):
            d["file"], d["line"] = entry, 1
            continue
        name, line = proj.locate(line_map, d["line"])
        d["file"], d["line"] = name, line
    payload["diagnostics"] = (
        [dict(d.to_json(), file=entry) for d in order_diags]
        + payload.get("diagnostics", []))
    payload["files"] = list(proj.files)
    return json.dumps(payload)


def analyse(source: str, assignment: str = "") -> str:
    """
    The single entry point the editor calls on every edit.

    When `assignment` is given, the student's code is run inside that
    assignment's setup and judged against its criteria — which may include
    hidden tests. Students never write their own `expect` or `budget`.

    Returns JSON: diagnostics, the diagram, metrics and a verdict.
    """
    if assignment:
        from .assignment import ASSIGNMENTS
        spec = ASSIGNMENTS.get(assignment)
        if spec is not None:
            return _analyse_assignment(spec, source)

    dialect = detect_dialect(source)
    result = Analysis(dialect=dialect)

    # Syntax first — everything downstream assumes a parseable program.
    syntax = syntax_check(source)
    if syntax:
        result.diagnostics = [d.to_json() for d in syntax]
        return result.to_json()

    try:
        result = _analyse_dialect(source, dialect, result)
    except Exception as e:
        from .notation import NotationError
        if isinstance(e, NotationError):
            result.diagnostics += [d.to_json() for d in e.diagnostics]
        else:
            result.diagnostics.append(
                Diagnostic(1, 1, "error", f"{type(e).__name__}: {e}").to_json())
    return result.to_json()


def _analyse_assignment(spec, student_source: str) -> str:
    """Analyse a student's code in the context of its task."""
    from .metrics import measure
    from .notation import NotationError
    from .notation_mr import build_mr, lint_mr
    from .notation_spark import build_spark, lint_spark
    from .runtime import build as build_rpc
    from .shapes import dataflow, gantt
    from .syntax import lint as lint_program

    def lint_rpc(src):
        return lint_program(src)[1]

    result = Analysis(dialect=spec.dialect)
    full = spec.program(student_source)

    # Each task names its own dialect, so an RPC task is not fed through the
    # MapReduce checker.
    if spec.dialect == RPC:
        linter, builder = lint_rpc, lambda s: build_rpc(s)
    elif spec.dialect == SPARK:
        linter, builder = lint_spark, lambda s: build_spark(s)[0]
    else:
        linter, builder = lint_mr, lambda s: build_mr(s)[0]

    # Diagnostics are reported against the *student's* lines, so the setup the
    # assignment prepends must not shift the numbers they see.
    offset = len(spec.setup.rstrip().splitlines()) + 1 if spec.setup else 0

    syntax = syntax_check(full)
    diags = syntax or linter(full)
    for d in diags:
        d.line = max(1, d.line - offset)
    result.diagnostics = [d.to_json() for d in diags]
    if any(d.severity == "error" for d in diags):
        return result.to_json()

    try:
        cluster = builder(full)
    except NotationError as e:
        for d in e.diagnostics:
            d.line = max(1, d.line - offset)
        result.diagnostics += [d.to_json() for d in e.diagnostics]
        return result.to_json()

    trace = cluster.sorted_trace()
    result.frame = dataflow(trace, title="").to_json()
    result.gantt = gantt(trace, title="").to_json()
    result.metrics = _metrics_json(measure(trace))
    result.verdict = spec.judge(student_source).to_json()
    result.outputs = {e.detail["key"]: e.detail["value"]
                      for e in cluster.trace.of_kind("output")}
    return result.to_json()


def _analyse_dialect(source: str, dialect: str, result: Analysis) -> Analysis:
    from .metrics import measure
    from .shapes import dataflow, gantt, spacetime

    if dialect == RPC:
        from .runtime import build
        from .syntax import lint as lint_program

        _, diags = lint_program(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        cluster = build(source)

    elif dialect == SPARK:
        from .notation_spark import build_spark, lint_spark
        diags = lint_spark(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        cluster, lineage, rdds, expects, budgets = build_spark(source)
        result.outputs = {k: _preview(v) for k, v in rdds.items()}

    elif dialect == CLOCKS:
        from .notation import build, lint
        diags = lint(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        run = build(source)
        cluster = run.cluster
        result.outputs = {k: str(v) for k, v in run.clocks.items()}
        trace = cluster.sorted_trace()
        result.frame = spacetime(trace, title="").to_json()
        result.gantt = gantt(trace, title="").to_json()
        result.metrics = _metrics_json(measure(trace))
        return result

    else:
        from .notation_mr import build_mr, judge_mr, lint_mr
        diags = lint_mr(source)
        result.diagnostics = [d.to_json() for d in diags]
        if any(d.severity == "error" for d in diags):
            return result
        cluster, expects, budgets = build_mr(source)
        result.verdict = judge_mr(source).to_json()
        result.outputs = {e.detail["key"]: e.detail["value"]
                          for e in cluster.trace.of_kind("output")}

    trace = cluster.sorted_trace()
    result.frame = dataflow(trace, title="").to_json()
    result.gantt = gantt(trace, title="").to_json()
    result.metrics = _metrics_json(measure(trace))
    return result


def _metrics_json(metrics: dict) -> list:
    return [{"name": m.name, "value": round(m.value, 3), "unit": m.unit,
             "detail": m.detail, "lower_is_better": m.lower_is_better,
             "explain": m.explanation}
            for m in metrics.values()]


def _preview(values, limit: int = 8):
    """A short, JSON-safe preview of an RDD's contents."""
    head = [list(v) if isinstance(v, tuple) else v for v in values[:limit]]
    return {"count": len(values), "sample": head}


# --- editor support -----------------------------------------------------

def completions(dialect: str | None = None) -> str:
    """Completion items, optionally filtered to one exercise."""
    items = [d for d in DOCS if not dialect or not d.dialects
             or dialect in d.dialects]
    out = [d.to_json() for d in items]
    out += [{"name": n, "signature": doc.split("—")[0].strip(),
             "summary": doc.split("—", 1)[-1].strip(), "detail": "",
             "dialects": [], "example": ""}
            for n, doc in BUILTIN_DOCS.items()]
    return json.dumps(out)


def hover(word: str) -> str:
    """
    Documentation for one symbol, or an empty object.

    Builtins are answered first. Some share a name with a keyword — `split` is
    both a function and an input declaration — and inside a function body it is
    the function the student means, so answering with the keyword's docs was
    simply wrong.
    """
    from .expr import BUILTINS

    if word in BUILTINS:
        signature = BUILTINS[word][3]
        summary, detail, example = BUILTIN_HELP.get(word, ("", "", ""))
        return json.dumps({"name": word, "signature": signature,
                           "summary": summary, "detail": detail,
                           "dialects": [], "example": example})
    d = DOC_INDEX.get(word)
    if d:
        return json.dumps(d.to_json())
    return json.dumps({})


def reference() -> str:
    """The whole language, for the editor's panel and its search."""
    placed = [n for _, _, _, names in GROUPS for n in names]
    missing = [d.name for d in DOCS if d.name not in placed]
    if missing:
        raise RuntimeError(f"GROUPS does not place: {missing}")
    index = {d.name: d for d in DOCS}
    return json.dumps({
        "groups": [{"slug": slug, "title": title, "note": note,
                    "items": [index[n].to_json() for n in names]}
                   for slug, title, note, names in GROUPS],
        "builtins": BUILTIN_DOCS})
