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
            return json.loads(store.read_text()).get("files", {})
        except ValueError:
            pass
    return seed(root)


def save_workspace(root: pathlib.Path, files: dict) -> None:
    store = _store(root)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"version": 1, "files": files}, indent=1) + "\n")


def seed(root: pathlib.Path) -> dict:
    """
    What a brand-new workspace starts with.

    A checkout with a `src/` directory is being used for authoring — that is
    where an exercise's own starters are written and tested as ordinary files —
    so its contents win. A student checkout has none, and starts from the
    starters that ship with the installed package.
    """
    files = {}
    loose = root / "src"
    if loose.is_dir():
        for path in sorted(loose.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files[path.name] = path.read_text()
        return files

    tasks = assets.tasks_dir()
    if not tasks.is_dir():
        return files

    # This exercise's starters, and nothing else. The package ships every task
    # the course has; opening the Spark exercise on a set of tabs that includes
    # a word-count MapReduce would undo the scoping the dropdown does.
    wanted = set(exercise.task_names(root))
    for path in sorted(tasks.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        # Data files come regardless of which task reads them: a task that
        # opens chunk001.txt needs chunk001.txt to be something the student
        # has, and nothing records which task that is.
        if path.suffix == ".ds" and path.stem not in wanted:
            continue
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
            self._json(200, {"dsviz": True,
                             "title": exercise.title(self.root),
                             "root": str(self.root),
                             "tasks": exercise.task_names(self.root)})
            return
        if self.path in ("/", "/index.html"):
            self._send_page()
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
        for prefix, where in (("/dsviz/", assets.modules_dir()),
                              ("/tasks/", assets.tasks_dir())):
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

        The list is injected as it is served rather than written into the
        packaged page: one page serves every exercise, and a page edited on
        disk would be a per-exercise copy of the editor again.
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
        names = ",".join(exercise.task_names(self.root))
        renamed = html.escape(json.dumps(exercise.titles(self.root)), quote=True)
        tag = (f'<meta name="dsviz-tasks" content="{names}">\n'
               f'<meta name="dsviz-titles" content=\'{renamed}\'>\n')
        page = page.replace("</head>", tag + "</head>", 1)
        body = page.encode()
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
    from .assignment import ASSIGNMENTS

    root = project_root()
    for name in exercise.task_names(root):
        print(f"{name:<16} "
              f"{exercise.title_for(root, name, ASSIGNMENTS[name].title)}")
    missing = exercise.unknown_tasks(root)
    if missing:
        # A name that no longer exists is dropped from the editor silently, so
        # this is the one place it can be noticed before a student does.
        print(f"\nnot in this dsviz: {', '.join(missing)}", file=sys.stderr)
        print("upgrade with: uv lock --upgrade-package dsviz", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dsviz", description="The editor and grader for a dsviz exercise.")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="open the editor on this exercise")
    p_serve.add_argument("port", nargs="?", type=int, default=8000)
    sub.add_parser("grade", help="score what is in result/")
    sub.add_parser("tasks", help="list the tasks this version ships")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
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
