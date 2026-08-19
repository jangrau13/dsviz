"""
The package is installable, and what it ships is enough to run an exercise.

An exercise repository declares `dsviz` as a dependency and vendors none of
it — no engine, no editor, no starters. That only holds if the wheel actually
contains those things and the code finds them once there is no repository
around the package. Both halves are checked here, against a real build rather
than against the checkout, because the checkout is the layout that works by
accident: `tasks/` happens to sit next to `dsviz/` here and nowhere else.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


# --- the checkout layout ------------------------------------------------

from dsviz import assets                                        # noqa: E402

ok("tasks resolve in a checkout", assets.tasks_dir().is_dir(), str(assets.tasks_dir()))
ok("the editor resolves in a checkout", (assets.web_dir() / "index.html").is_file())
ok("the engine is the package itself",
   (assets.modules_dir() / "core.py").is_file())

# Every task the catalogue offers must have a starter to open with, or the
# editor shows an empty tab and the student has nothing to edit.
from dsviz.assignment import ASSIGNMENTS                        # noqa: E402

missing = [n for n, a in ASSIGNMENTS.items() if not a.starter.strip()]
ok("every task has a starter", not missing, ", ".join(missing))

# Every task must survive the hand-in, which runs it under the dialect the
# detector picks rather than the one the assignment declares. Those two
# disagreed for `@process` programs, and the result was that no clocks task
# could be handed in at all — a break invisible to every test that only ran
# starters directly.
#
# The two kinds of task pull in opposite directions here, and both matter. An
# exploration task ships complete and must hand in as it stands. An
# implementation task ships with the wiring missing, and its starter must
# NOT run: a scaffold that hands in successfully is a task that asks for
# nothing.
from dsviz import attest                                        # noqa: E402

unstampable, too_easy = [], []
for name, spec in ASSIGNMENTS.items():
    asks_for_work = bool(spec.expects or spec.requires or spec.budgets)
    try:
        attest.stamp(name, spec.starter)
        if asks_for_work:
            too_easy.append(name)
    except Exception as err:                                    # noqa: BLE001
        if not asks_for_work:
            unstampable.append(f"{name}: {str(err).splitlines()[0]}")
ok("every exploration task can be handed in as it ships",
   not unstampable, "; ".join(unstampable))
ok("no graded task's scaffold runs on its own", not too_easy,
   ", ".join(too_easy) + " ran without the student writing anything"
   if too_easy else "")


# --- the built wheel ----------------------------------------------------
# `uv build` is what a student's `uv add` ultimately consumes. If it is not
# available this is reported as skipped rather than passing on nothing.

def build_wheel(into: pathlib.Path) -> pathlib.Path | None:
    for builder in (["uv", "build", "--wheel", "--out-dir", str(into)],
                    [sys.executable, "-m", "build", "--wheel", "--outdir", str(into)]):
        try:
            r = subprocess.run(builder, cwd=ROOT, capture_output=True, text=True)
        except FileNotFoundError:
            continue
        if r.returncode == 0:
            wheels = list(into.glob("*.whl"))
            return wheels[0] if wheels else None
    return None


with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    wheel = build_wheel(tmp / "dist")
    if wheel is None:
        print("SKIP no wheel builder available (need uv or python -m build)")
    else:
        names = set(zipfile.ZipFile(wheel).namelist())

        # The starters and their data files, inside the package.
        shipped = {n.split("/")[-1] for n in names if "/_tasks/" in n}
        wanted = {p.name for p in assets.tasks_dir().iterdir()
                  if p.is_file() and not p.name.startswith(".")}
        ok("the wheel ships every task file", wanted <= shipped,
           f"missing {sorted(wanted - shipped)}")

        # The editor.
        ok("the wheel ships the editor", "dsviz/_web/index.html" in names)
        ok("the wheel ships the editor's script", "dsviz/_web/app.js" in names)

        # The engine the browser fetches, module for module. app.js names them;
        # a module added to that list and not to the package is a page that
        # loads to a broken import, which no Python suite would catch.
        app_js = (assets.web_dir() / "app.js").read_text()
        block = app_js.split("const modules = [", 1)[-1].split("]", 1)[0]
        needed = [w.strip().strip('"\'') for w in block.split(",") if w.strip()]
        absent = [m for m in needed if f"dsviz/{m}.py" not in names]
        ok("the wheel ships every module the editor loads", not absent,
           ", ".join(absent))

        # The symlink `web/dsviz -> ../dsviz` must not be followed into the
        # wheel: it would be a second copy of the engine, free to fall behind
        # the first.
        ok("the engine is not duplicated under _web",
           not any(n.startswith("dsviz/_web/dsviz/") for n in names))

        # Installing it and running a task is the claim that matters.
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                       capture_output=True)
        scheme = "nt" if sys.platform == "win32" else "posix_prefix"
        bindir = "Scripts" if sys.platform == "win32" else "bin"
        python = venv / bindir / ("python.exe" if sys.platform == "win32" else "python")
        pip = subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                             str(wheel)], capture_output=True, text=True)
        if pip.returncode:
            print("SKIP could not install the wheel (no network?):",
                  pip.stderr.strip()[-200:])
        else:
            probe = '''
import json, sys
from dsviz import assets
from dsviz.assignment import ASSIGNMENTS, judge_assignment
# Installed, there is no repository to fall back to: these must be the copies
# that ride inside the package.
assert assets.tasks_dir().name == "_tasks", assets.tasks_dir()
assert assets.web_dir().name == "_web", assets.web_dir()
code = ASSIGNMENTS["t1-wordcount"].starter + """
def tokenize(key: string, value: string) -> void:
    for word: string in split(lower(value)):
        emit(word, 1)

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n

job = MapReduce(map=tokenize, reduce=total, partition=byKey)
world.run(job)
"""
print(json.dumps(json.loads(judge_assignment("t1-wordcount", code))["verdict"]))
'''
            # Run from anywhere but the checkout. The current directory is
            # on `sys.path`, so running this here would import the source tree
            # and prove nothing about what was installed.
            r = subprocess.run([str(python), "-c", probe], cwd=tmp,
                               capture_output=True, text=True)
            ok("an installed dsviz runs and grades a submission",
               r.returncode == 0 and '"AC"' in r.stdout,
               (r.stderr.strip()[-300:] or r.stdout.strip()[-120:]))

            cli = subprocess.run([str(venv / bindir / "dsviz"), "tasks"],
                                 cwd=tmp, capture_output=True, text=True)
            ok("the `dsviz` command is installed",
               cli.returncode == 0 and "t1-wordcount" in cli.stdout,
               cli.stderr.strip()[-200:])

print("ALL PACKAGE TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
