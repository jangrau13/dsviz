"""
The command an exercise repository runs: `dsviz serve` and `dsviz grade`.

An exercise used to carry its own copy of the editor, the engine and the
grader, kept in step by a sync script. The copy is what made that fragile:
three repositories could drift from the package and from each other, and the
only way to notice was for a student to hit the difference.

Here the exercise carries none of it. It declares `dsviz` as a dependency, and
these two commands read the editor and the engine out of the installed package.
Upgrading an exercise is `uv lock --upgrade-package dsviz`; there is nothing to
copy and so nothing to fall behind.

    dsviz serve [port]     the editor, on the current directory
    dsviz grade            score what is in result/
    dsviz tasks            list the tasks this version ships
"""

from __future__ import annotations

import argparse
import errno
import html
import json
import os
import pathlib
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import assets, exercise

# The exercise checkout: the workspace, the hand-ins and the starters are all
# relative to where the student ran the command, never to where the package
# happens to be installed.
def project_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("DSVIZ_PROJECT", ".")).resolve()


def repo_slug(root: pathlib.Path) -> str:
    """`owner/name` for this checkout's origin, or empty if it has none."""
    import re
    import subprocess

    try:
        url = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return re.sub(r"^(git@github\.com:|https://github\.com/)|\.git$", "", url)


# --- the workspace ------------------------------------------------------

def _store(root: pathlib.Path) -> pathlib.Path:
    return root / ".dsviz" / "workspace.json"


def load_workspace(root: pathlib.Path) -> dict:
    store = _store(root)
    if store.exists():
        try:
            saved = json.loads(store.read_text()).get("files", {})
            return _reconcile(root, saved) if isinstance(saved, dict) else seed(root)
        except ValueError:
            pass
    return seed(root)


def _reconcile(root: pathlib.Path, saved: dict) -> dict:
    """
    Bring a saved workspace up to date with the exercise it belongs to.

    Tasks get renamed and exercises get rescoped, and a workspace saved before
    either keeps its tabs: a student opens Assignment 1 and finds the six
    tasks it has plus three from the shape it used to have. Every one of those
    is a file they are invited to work in and none of them is theirs.

    Two rules, and the second is the important one. A task this exercise now
    has is added if it is missing. A file this exercise does not have is
    removed *only* when its text is one the exercise still ships — that is,
    when it is a starter nobody has touched. Anything else was written by the
    student and stays, whatever it is called, because the cost of being wrong
    in that direction is their work.
    """
    shipped = set(seed(root).values())

    files = {name: text for name, text in saved.items()
             if name in seed(root) or text not in shipped}
    for name, text in seed(root).items():
        files.setdefault(name, text)
    return files


def save_workspace(root: pathlib.Path, files: dict) -> None:
    store = _store(root)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"version": 1, "files": files}, indent=1) + "\n")


def seed(root: pathlib.Path) -> dict:
    """
    What a brand-new workspace starts with.

    One tab per task, opened on that task's starter — which is a string on
    the task, not a file in the checkout, so there is nothing here for a
    student to edit around the editor. Plus the data files those tasks read,
    which are ordinary files because they are input rather than program text.

    A checkout with a `src/` directory is being used for authoring, and its
    contents win.
    """
    from .assignment import ASSIGNMENTS

    files = {}
    loose = root / "src"
    if loose.is_dir():
        for path in sorted(loose.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files[path.name] = path.read_text()
        return files

    for name, task in ASSIGNMENTS.items():
        files[f"{name}.ds"] = task.starter
        for extra, text in task.extra_files.items():
            files[extra] = text

    tasks = root / "tasks"
    if not tasks.is_dir():
        return files

    # This exercise's starters, and nothing else. A `tasks/` directory can
    # hold a file the exercise's `tasks.py` does not list — a draft, or one
    # withdrawn for a term — and the dropdown is the list, not the directory.
    # A data file ships when one of this exercise's starters names it — the
    # task that opens chunk001.txt says so, in the line that opens it. Without
    # this the Spark exercise's CSVs turned up in a MapReduce workspace.
    named = "\n".join(task.starter for task in ASSIGNMENTS.values())

    for path in sorted(tasks.iterdir()):
        if (path.is_file() and not path.name.startswith(".")
                and path.suffix != ".ds" and path.name in named):
            files[path.name] = path.read_text()
    return files


# --- the hand-in --------------------------------------------------------

def hand_in(root: pathlib.Path, task: str, code: str) -> tuple[int, dict]:
    """
    Run the submission, stamp it, and write it where grading will look.

    Running it here is what makes the stamp mean something: the record cannot
    be written without the code having executed, so `result/` cannot fill up
    with code nobody ever ran.
    """
    from . import attest
    from .assignment import ASSIGNMENTS

    if task not in ASSIGNMENTS:
        return 400, {"error": f"no task called {task!r}"}
    try:
        stamped = attest.stamp(task, code, at=_now(), runs=1)
    except Exception as err:                              # noqa: BLE001
        return 400, {"error": f"this code does not run: {err}"}

    # Created by the first hand-in and not before. An exercise that shipped
    # an empty `solutions/` was inviting a student to put a file in it, which
    # is the one route into it that does not work.
    results = root / "result"
    results.mkdir(parents=True, exist_ok=True)
    target = results / f"{task}.ds"
    target.write_text(stamped)
    return 200, {"handed_in": str(target.relative_to(root)),
                 "bytes": len(stamped)}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- the server ---------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    """
    The editor's page, the engine it loads, and the two writes it needs.

    Three directories are served as one tree. The page comes from the packaged
    editor; `/dsviz/*.py` comes from the installed package itself, so the
    browser runs exactly the modules CPython would; `/tasks/*` comes from the
    packaged starters. Nothing is copied into the exercise to make this work.
    """

    root = pathlib.Path(".")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(assets.web_dir()), **kwargs)

    # -- reads
    def do_GET(self):                                     # noqa: N802
        if self.path == "/api/workspace":
            self._json(200, {"files": load_workspace(self.root)})
            return
        if self.path == "/api/exercise":
            # So that a server starting up can say who already has the port,
            # rather than reporting a number the student then has to hunt for.
            here = self.root / "tasks"
            self._json(200, {"dsviz": True,
                             "title": exercise.title(self.root),
                             "root": str(self.root),
                             "tasks": exercise.task_names(self.root),
                             # The data a task reads. Starters are not here:
                             # they ride on the task, in the manifest.
                             "data": sorted(
                                 p.name for p in here.iterdir()
                                 if p.is_file() and p.suffix != ".ds"
                                 and not p.name.startswith("."))
                             if here.is_dir() else []})
            return
        if self.path in ("/", "/index.html"):
            self._send_page()
            return
        if self.path.split("?")[0].rstrip("/") == "/docs":
            self._send_docs()
            return
        served = self._from_package()
        if served is not None:
            self._send_file(served)
            return
        super().do_GET()

    def _from_package(self) -> pathlib.Path | None:
        """
        The engine and the starters, which live in the package, not the page.

        Both are addressed by a flat name under a known prefix. Anything with a
        separator in it is not one of these files and falls through to the
        static handler rather than being resolved — a path that escapes the
        directory is refused before it is a path at all.
        """
        # The exercise's manifest, which the page fetches and runs to find
        # out which tasks exist. Served by name so nothing else in the
        # checkout becomes reachable by asking for it.
        if self.path.split("?")[0] == "/tasks.py":
            candidate = self.root / "tasks.py"
            return candidate if candidate.is_file() else None

        for prefix, where in (("/dsviz/", assets.modules_dir()),
                              ("/tasks/", self.root / "tasks")):
            # Starters are not served: they are strings on the task, and the
            # page gets them with the manifest. Only the data a task reads.
            if prefix == "/tasks/" and self.path.endswith(".ds"):
                return None
            if not self.path.startswith(prefix):
                continue
            name = self.path[len(prefix):].split("?")[0]
            if not name or "/" in name or "\\" in name or name.startswith("."):
                return None
            candidate = where / name
            if candidate.is_file():
                return candidate
        return None

    def _send_page(self) -> None:
        """
        The editor, told which tasks this exercise consists of.

        Which tasks exist is not injected here any more — the page fetches
        the exercise's own `tasks.py` and runs it, so there is one list
        rather than a list and a copy of it in a meta tag.
        """
        page = (assets.web_dir() / "index.html").read_text()
        # Which repository the commit button pushes back to. Read from git
        # rather than configured: the exercise is a fork, so its own remote is
        # the only thing that knows the answer, and a student who forked it
        # should not have to edit a file to say so.
        slug = repo_slug(self.root)
        if slug:
            page = page.replace('<meta name="dsviz-repo" content="">',
                                f'<meta name="dsviz-repo" content="{slug}">', 1)
        body = page.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_docs(self) -> None:
        """
        The language reference, as a page of its own.

        The editor links to `docs/` from two places, and until now that was a
        directory of built HTML which the old vendoring server copied in beside
        the page. That server is gone, the built site is a gitignored artefact
        that cannot ride inside a wheel, and there is no Pages deployment — so
        both links 404'd.

        Rendered here instead, from `langserver.reference()`: the same table
        the panel and the search read. A generated page cannot fall behind the
        tool the way a built one can, and it needs no mkdocs on the machine.
        """
        from .langserver import reference

        data = json.loads(reference())
        out = [
            "<!doctype html><meta charset=utf-8>",
            "<title>dsviz — the language</title>",
            "<meta name=viewport content='width=device-width,initial-scale=1'>",
            "<style>",
            ":root{color-scheme:light dark}",
            "body{margin:0 auto;padding:2rem 1.25rem 6rem;max-width:46rem;",
            " font:16px/1.6 ui-sans-serif,system-ui,sans-serif}",
            "h1{font-size:1.6rem;margin:0 0 .25rem}",
            "h2{font-size:1.15rem;margin:2.5rem 0 .25rem;",
            " border-bottom:1px solid color-mix(in srgb,currentColor 20%,transparent);",
            " padding-bottom:.3rem}",
            "h3{font-size:1rem;margin:1.5rem 0 .2rem;font-family:ui-monospace,monospace}",
            "p{margin:.35rem 0}",
            ".note,.sum{opacity:.75}",
            "pre{overflow-x:auto;padding:.7rem .9rem;border-radius:6px;",
            " background:color-mix(in srgb,currentColor 8%,transparent)}",
            "code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.92em}",
            "table{border-collapse:collapse;width:100%;margin:.6rem 0}",
            "td{padding:.25rem .5rem;vertical-align:top;",
            " border-top:1px solid color-mix(in srgb,currentColor 15%,transparent)}",
            "</style>",
            "<h1>dsviz — the language</h1>",
            "<p class=note>Generated from the same table the editor reads for "
            "hovers, completions and <code>ctrl/cmd + K</code>, so it cannot "
            "drift from what the tool says.</p>",
        ]
        for group in data.get("groups", []):
            out.append(f"<h2>{html.escape(group['title'])}</h2>")
            if group.get("note"):
                out.append(f"<p class=note>{html.escape(group['note'])}</p>")
            for item in group.get("items", []):
                out.append(f"<h3>{html.escape(item.get('signature') or item['name'])}</h3>")
                for key in ("summary", "detail"):
                    if item.get(key):
                        out.append(f"<p>{html.escape(item[key])}</p>")
                if item.get("example"):
                    out.append(f"<pre><code>{html.escape(item['example'])}</code></pre>")
        # `builtins` is {name: "signature — summary"}, the one line the search
        # shows for each. Split on the dash so the table has two columns.
        builtins = data.get("builtins") or {}
        if builtins:
            out.append("<h2>Built-in functions</h2>")
            out.append("<p class=note>Deliberately general: anything "
                       "problem-shaped is a function you write.</p><table>")
            for name in sorted(builtins):
                sig, _, summary = str(builtins[name]).partition(" — ")
                out.append(f"<tr><td><code>{html.escape(sig)}</code></td>"
                           f"<td>{html.escape(summary)}</td></tr>")
            out.append("</table>")

        body = "\n".join(out).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: pathlib.Path) -> None:
        body = path.read_bytes()
        kind = ("text/x-python" if path.suffix == ".py"
                else "text/plain; charset=utf-8")
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- writes
    def do_PUT(self):                                     # noqa: N802
        name = self._named("/api/workspace/")
        if name is None:
            self.send_error(404, "nothing to PUT here")
            return
        files = load_workspace(self.root)
        files[name] = self._body()
        save_workspace(self.root, files)
        self._json(200, {"saved": name, "bytes": len(files[name])})

    def do_DELETE(self):                                  # noqa: N802
        name = self._named("/api/workspace/")
        if name is None:
            self.send_error(404, "nothing to DELETE here")
            return
        files = load_workspace(self.root)
        files.pop(name, None)
        save_workspace(self.root, files)
        self._json(200, {"deleted": name})

    def do_POST(self):                                    # noqa: N802
        task = self._named("/api/handin/")
        if task is None:
            self.send_error(404, "nothing to POST here")
            return
        status, payload = hand_in(self.root, task, self._body())
        self._json(status, payload)
        print(f"hand-in {task}: {payload.get('handed_in') or payload.get('error')}")

    # -- helpers
    def _named(self, prefix: str) -> str | None:
        """
        The file name in a request path, or None if it is not one.

        A name is a name: no directories, no dots leading anywhere. The
        workspace is a flat set of files, so anything with a separator in it is
        not a workspace file and is refused rather than resolved.
        """
        if not self.path.startswith(prefix):
            return None
        name = self.path[len(prefix):].strip("/")
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        return name

    def _body(self) -> str:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length).decode("utf-8")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # One line per write is useful; one per asset fetch is not.
        if self.command != "GET":
            super().log_message(fmt, *args)


# How far to look for a free port before giving up. Far enough to step over a
# few forgotten servers, short enough that a machine with nothing free says so
# rather than scanning for a minute.
PORT_SEARCH = 20


def whoever_has(port: int) -> str:
    """
    Who is already on this port, said in terms the student can act on.

    A port number alone leaves them hunting through `lsof`. Another dsviz
    server can be asked directly — it will name its own exercise — and
    anything else at least gets identified as not being one of ours.
    """
    import json as _json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/exercise", timeout=0.5) as reply:
            info = _json.loads(reply.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return "something that is not a dsviz editor"
    if not isinstance(info, dict) or not info.get("dsviz"):
        return "something that is not a dsviz editor"
    name = info.get("title") or pathlib.Path(info.get("root", "")).name
    return f"the editor for {name}" if name else "another dsviz editor"


def open_server(port: int, handler) -> tuple:
    """
    Bind the first free port at or after `port`.

    A port left busy by a server someone forgot to stop is the most ordinary
    thing that happens here, and it used to end in a traceback about
    `socket.bind` — which says nothing about what to do. The next port along
    works just as well, so it is taken and said out loud.
    """
    last = None
    for candidate in range(port, port + PORT_SEARCH + 1):
        try:
            return ThreadingHTTPServer(("0.0.0.0", candidate), handler), candidate
        except OSError as err:
            if err.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last = err
    raise OSError(
        f"nothing free between {port} and {port + PORT_SEARCH}: {last}")


def serve(port: int) -> int:
    root = project_root()
    handler = type("BoundHandler", (Handler,), {"root": root})
    asked = port
    try:
        server, port = open_server(port, handler)
    except OSError as err:
        print(f"Could not start the editor: {err}", flush=True)
        print(f"Port {asked} is serving {whoever_has(asked)}, and the ports "
              f"after it are busy too. Stop one, or pick a port yourself: "
              f"dsviz serve 9000", flush=True)
        return 1
    # Flushed as they are written. Python buffers stdout when it is not a
    # terminal, and the devcontainer runs this with its output redirected — so
    # the line saying which port the editor ended up on sat in a buffer while
    # the student looked at an empty log and a page that would not load.
    def say(line: str) -> None:
        print(line, flush=True)

    if port != asked:
        say(f"Port {asked} is already serving {whoever_has(asked)}, "
            f"so this one is on {port} instead.")
    say(f"Editor on http://localhost:{port}")
    say(f"  exercise:  {root}")
    say(f"  workspace: .dsviz/workspace.json  "
        f"({len(load_workspace(root))} file(s))")
    say("  hand-ins written to: result/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# --- grading ------------------------------------------------------------

def grade() -> int:
    """
    Score what is in `result/`, on held-out input when it is available.

    Held-out data lives outside the published exercise, so it reaches CI but
    never the browser. Locally its absence is expected; in CI it means the
    private checkout failed, and grading the visible criteria would pass a
    submission on the very examples the student has been iterating against.
    That is a red build, not a warning nobody reads.
    """
    from . import attest
    from .assignment import ASSIGNMENTS, judge_assignment, load_holdout

    root = project_root()
    holdout = pathlib.Path(os.environ["DSVIZ_HOLDOUT"]) if os.environ.get(
        "DSVIZ_HOLDOUT") else root / ".grading/holdout.json"

    on_holdout = load_holdout(str(holdout))
    in_ci = os.environ.get("CI") == "true"
    if on_holdout:
        print("Grading on held-out input.\n")
    elif in_ci:
        print("No held-out data, but this is CI — refusing to grade.\n")
        print("The private grading checkout did not happen. This is a course "
              "infrastructure problem, not a problem with the submission: "
              "check that GRADING_REPO_TOKEN is set and unexpired.")
        return 1
    else:
        print("No held-out data — grading the visible criteria only "
              "(local run).\n")

    offered = exercise.task_names(root)
    files = sorted((root / "result").glob("*.ds"))
    if not files:
        print("Nothing handed in — result/ is empty or absent.")
        print("Commit one from the editor — an empty submission is not a pass.")
        return 1

    failed = []
    graded = 0
    for path in files:
        task = path.stem
        spec = ASSIGNMENTS.get(task) if task in offered else None
        if spec is None:
            # Not a task of this exercise. Say so loudly: a typo here used to
            # mean the file was skipped and the build went green on nothing.
            print(f"FAIL {path.name}: this exercise has no task called {task!r}")
            print(f"       · rename it to one of: {', '.join(offered)}")
            failed.append(path.name)
            continue
        graded += 1
        # The exercise's heading, not the package's: a student reading "Task 1"
        # in a CI log for what their exercise calls Task 2 has to translate.
        heading = exercise.title_for(root, task, spec.title)
        text = path.read_text()

        # Was this actually run, or did it appear here? `result/` is written
        # by the editor's server, which runs the code and stamps what it did
        # into the file. Recomputing that stamp is what separates a submission
        # from a file somebody copied in.
        if not os.environ.get("DSVIZ_NO_ATTEST"):
            reasons = attest.verify(task, text)
            if reasons:
                print(f"FAIL {heading} — this is not a hand-in")
                for reason in reasons:
                    print(f"       · {reason}")
                failed.append(heading)
                continue

        source, _ = attest.split(text)
        result = json.loads(judge_assignment(task, source, True))
        ok = result["verdict"] == "AC"
        print(f"{'ok' if ok else 'FAIL':<4} {heading} — {result['label']} "
              f"({result['score']:g}/{result['max_score']:g})")
        for case in result["cases"]:
            if case["verdict"] != "AC":
                # Held-out expectations stay hidden: the student learns which
                # case failed, never what it wanted.
                print(f"       · {case['name']}"
                      + (f" — {case['message']}" if case["message"] else ""))
        if not ok:
            failed.append(heading)

    basis = "held-out input" if on_holdout else "the visible criteria only"
    if failed:
        print(f"\n{len(failed)} task(s) did not pass on {basis}.")
        return 1
    print(f"\nAll {graded} submitted task(s) passed on {basis}.")
    return 0


def tasks() -> int:
    root = project_root()
    headings = exercise.titles(root)
    if not headings:
        print("this exercise declares no tasks — is there a tasks.py?",
              file=sys.stderr)
        return 1
    print(exercise.title(root) or str(root))
    for name, heading in headings.items():
        print(f"  {name:<16} {heading}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dsviz", description="The editor and grader for a dsviz exercise.")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="open the editor on this exercise")
    p_serve.add_argument("port", nargs="?", type=int, default=8000)
    sub.add_parser("grade", help="score what is in result/")
    sub.add_parser("tasks", help="list this exercise's tasks")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    # Every command works on the exercise in this checkout, and there are no
    # tasks until it says what they are.
    if args.command in ("serve", "grade", "tasks"):
        exercise.load(project_root())
    if args.command == "serve":
        return serve(args.port)
    if args.command == "grade":
        return grade()
    if args.command == "tasks":
        return tasks()
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
