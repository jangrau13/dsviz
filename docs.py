#!/usr/bin/env python3
"""
Generate the documentation site.

    python docs.py --site docs && mkdocs build
    python docs.py --single ../BCS-DS-Assignment-Solution/LANGUAGE.md

The pages come from the same tables the editor reads for hovers and
completions, so the written reference cannot drift from what the tool
actually implements.

INVARIANT: this documents the LANGUAGE, and nothing else.

No page here describes an exercise, and no example is a step towards one.
Two consequences, both deliberate:

  * `dsviz.assignment` is not imported. That module is the only place
    held-out data has ever lived, so the documentation simply cannot reach
    it. Do not add the import back "just for the titles".
  * Examples are written in a domain no exercise uses. A reference that
    demonstrates a comprehension with the exercise's own mapper is an answer
    sheet with a table of contents.

`tests/docs_test.py` enforces both.
"""

import sys

sys.path.insert(0, ".")

from dsviz.expr import BUILTINS  # noqa: E402
from dsviz.langserver import DOCS, GROUPS  # noqa: E402

def _front(title: str, description: str) -> list[str]:
    return ["---", f"title: {title}", f"description: {description}", "---", ""]


def _entry(d) -> list[str]:
    out = [f"### `{d.signature}`", "", d.summary]
    if d.detail:
        out += ["", d.detail]
    if d.example:
        out += ["", "```python", d.example, "```"]
    return out + [""]


def write_site(root: str = "docs") -> list[str]:
    """Write the markdown the site is built from. Returns the paths."""
    import pathlib

    base = pathlib.Path(root)
    (base / "language").mkdir(parents=True, exist_ok=True)
    written = []

    def put(rel: str, lines: list[str]) -> None:
        path = base / rel
        path.write_text("\n".join(lines).rstrip() + "\n")
        written.append(str(path))

    put("index.md", _front(
        "dsviz", "How to write a program in this language.") + [
        "# dsviz",
        "",
        "This is the reference for the language: how to write a program, and",
        "what each part of one means. What to build is in the task open in",
        "the editor, not here.",
        "",
        "A program has three parts.",
        "",
        "```python",
        "@machine",
        "class Ledger:",
        "    held: int = 120",
        "",
        "    @duration(0.4)",
        "    def deposit(amount: int) -> int:",
        "        held: int = held + amount",
        "        return held",
        "",
        "bank = Ledger(speed=1.0)",
        "",
        "world = World(machines=[bank])",
        "",
        "def story() -> void:",
        "    now: int = bank.deposit(30)",
        "",
        "job = Calls(run=story)",
        "world.run(job)",
        "```",
        "",
        "**The machines** are decorated classes. A class is a *kind* of machine;",
        "what runs is an instance you make from it, carrying its own speed, its",
        "own failure behaviour, and whatever it remembers. A field written in",
        "the class body — `held: int = 120` — is state: the next call can see",
        "what the last one did, and a crash takes it away again.",
        "",
        "**The world** says which machines exist together. Nothing runs outside",
        "one, because a machine on its own is a description rather than a",
        "system.",
        "",
        "**The job** is the computation, handed to the world to run. The same job",
        "can be run in a fast world and a broken one, which is the comparison",
        "most of the course is about.",
        "",
        "Everything else is a variation on those three: more machines, a",
        "different job, a world that breaks.",
    ])

    put("language/index.md", _front(
        "Writing a program", "Statements, types, and what is written down.") + [
        "# Writing a program",
        "",
        "One statement per line. `#` starts a comment. Indentation delimits the",
        "body of a function, a loop, a conditional or a parallel block, as in",
        "Python.",
        "",
        "Every name carries a written type. Nothing is inferred: a parameter, a",
        "local, a loop variable and a return type are all stated, and the checker",
        "holds the program to what was written. That is what lets a job verify a",
        "function fits the position it was passed to.",
        "",
        "```python",
        "def hottest(city: string, readings: [int]) -> int:",
        "    top: int = 0",
        "    for reading: int in readings:",
        "        if reading > top:",
        "            top: int = reading",
        "    return top",
        "```",
        "",
        "Names are yours. A function fits a position because its types fit,",
        "and you say which function goes where when you build the job. What",
        "you called it never enters into it.",
        "",
        "## Types",
        "",
        "| Type | Is |",
        "|---|---|",
        "| `int` | a whole number |",
        "| `string` | text |",
        "| `[int]` | a list of numbers |",
        "| `[string]` | a list of text |",
        "| `pair` | a (key, value) pair, written `(key, value)` |",
        "| `[pair]` | a list of pairs — what a map answers with |",
        "| `void` | nothing |",
        "",
        "`[int]` and `[string]` are deliberately distinct: the mistakes worth",
        "catching are the ones that confuse a list of counts with a list of words.",
        "",
        "A function that produces one thing returns it. A function that",
        "produces an unknown number of them returns a list, and says so: a map",
        "is handed one record and answers `[pair]`, because how many pairs it",
        "makes is not known until it has made them. A reduce collapses many",
        "values into exactly one, so it answers with that one value.",
        "",
        "## Several files",
        "",
        "A program can span files. `use` brings another file's definitions into",
        "scope; files are combined in dependency order, and a mistake is reported",
        "against the file and line it is in. Circular `use` and missing files are",
        "errors rather than run-time surprises.",
    ])

    # Every documented symbol belongs to exactly one page. A symbol added to
    # `langserver.DOCS` and forgotten here fails the build rather than going
    # quietly undocumented.
    placed = [n for _, _, _, names in GROUPS for n in names]
    known = {d.name: d for d in DOCS}
    missing = [n for n in known if n not in placed]
    unknown = [n for n in placed if n not in known]
    if missing or unknown:
        raise SystemExit(
            "docs.py GROUPS is out of step with langserver.DOCS: "
            f"undocumented: {missing}; no such symbol: {unknown}")

    for slug, title, note, names in GROUPS:
        lines = _front(title, note) + [f"# {title}", "", note, ""]
        for name in names:
            lines += _entry(known[name])
        put(f"language/{slug}.md", lines)

    # The catalogue, straight off the language's own table so the page cannot
    # drift from what a program is actually allowed to buy.
    from dsviz.machine_types import CATALOGUE  # noqa: E402
    put("language/machines-you-can-buy.md", _front(
        "Machines you can buy",
        "The catalogue, and what each type comes with.") + [
        "# Machines you can buy",
        "",
        "A machine is not built to order. There is a catalogue, you pick a",
        "type off it, and the machine arrives with the processor and the room",
        "that type comes with — so making something faster is a purchase",
        "rather than a number typed into the program.",
        "",
        "| Type | Processor | Room | For |",
        "|---|---|---|---|",
    ] + [
        f"| `{t.name}` | {t.speed:g}x | {t.capacity} items | {t.why} |"
        for t in CATALOGUE.values()
    ] + [
        "",
        "The letter says what the machine is built for. `c` has the",
        "processor, for work that is slow because there is a lot of it to do.",
        "`r` has the room, for a machine handed more than it can hold — that",
        "one does not get better on a quicker processor. `m` is the middle of",
        "both, and `t` is the cheap one, which is how you are given a",
        "straggler.",
        "",
        "```python",
        '@machine',
        'class Worker:',
        '    pass',
        '',
        'fast = Worker(type="c1.large")',
        'roomy = Worker(type="r1.large")',
        'ordinary = Worker()          # m1.small, if you say nothing',
        "```",
        "",
        "Each type is drawn in its own colour, so a fleet of mixed machines",
        "reads as mixed at a glance.",
        "",
        "A job that will not fit on one machine can be moved to a bigger one,",
        "or made smaller before it is sent. Both are answers. Which is",
        "cheaper is the question the catalogue exists to ask, and the panel",
        "under the diagram is where the answer shows.",
    ])

    lines = _front("Built-in functions",
                   "The small library every program can call.") + [
        "# Built-in functions", "",
        "Deliberately general: `split` and `lower` are string operations and",
        "`sum` is arithmetic. Nothing here solves part of a task, because",
        "anything problem-shaped is a function you write.", "",
        "| Function | Type |", "|---|---|"]
    for name, (params, ret, _, doc) in sorted(BUILTINS.items()):
        lines.append(f"| `{name}` | `{doc}` |")
    put("language/builtins.md", lines)

    # Material binds `s` and `/` to its search box. ctrl/cmd + K is what the
    # editor uses, so the same key opens the same thing in both places.
    (base / "assets" / "js").mkdir(parents=True, exist_ok=True)
    put("assets/js/search-shortcut.js", [
        "/* Search on ctrl/cmd + K, the shortcut the editor uses. */",
        "document.addEventListener('keydown', function (e) {",
        "  if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return;",
        "  var box = document.querySelector('.md-search__input');",
        "  if (!box) return;",
        "  e.preventDefault();",
        "  var toggle = document.getElementById('__search');",
        "  if (toggle) toggle.checked = true;   // opens it on a narrow screen",
        "  box.focus();",
        "  box.select();",
        "});",
        "document.addEventListener('DOMContentLoaded', function () {",
        "  var box = document.querySelector('.md-search__input');",
        "  if (!box) return;",
        "  var mac = /Mac|iPhone|iPad/.test(navigator.platform);",
        "  box.placeholder = mac ? 'Search  \u2318K' : 'Search  Ctrl+K';",
        "});",
    ])

    return written


def write_single(path: str) -> str:
    """
    The whole reference as one file, for somewhere that cannot host a site.

    Same pages, same order, concatenated with their front matter stripped and
    their headings pushed down one level. The grading repository keeps a copy
    so an examiner has the language to hand mid-viva; it said it was generated
    long before anything generated it, and drifted into documenting syntax the
    engine no longer accepts.
    """
    import pathlib
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        write_site(tmp)
        base = pathlib.Path(tmp)
        order = ["index.md", "language/index.md"] + \
                [f"language/{slug}.md" for slug, *_ in GROUPS] + \
                ["language/builtins.md"]
        out = ["# The language", "",
               "Generated by `docs.py --single` from the same table the editor",
               "reads for hovers and completions, so this cannot drift from",
               "what the tool says. Regenerate it after changing the language.",
               ""]
        for rel in order:
            body = (base / rel).read_text().split("---\n", 2)[-1].strip("\n")
            out += ["", "---", ""]
            out += ["#" + line if line.startswith("#") else line
                    for line in body.split("\n")]
        pathlib.Path(path).write_text("\n".join(out).strip("\n") + "\n")
        return path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--single" in sys.argv:
        at = sys.argv.index("--single")
        where = sys.argv[at + 1] if len(sys.argv) > at + 1 else "LANGUAGE.md"
        print(f"  wrote {write_single(where)}")
        raise SystemExit(0)
    if "--site" not in sys.argv:
        print(__doc__.strip())
        raise SystemExit(2)
    at = sys.argv.index("--site")
    where = sys.argv[at + 1] if len(sys.argv) > at + 1 else "docs"
    for p in write_site(where):
        print(f"  wrote {p}")
