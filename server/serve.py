#!/usr/bin/env python3
"""
The editor's server: the page, the workspace, and the hand-in.

`python -m http.server` only reads, so "save" used to mean downloading a copy
and moving it into place by hand. This is the same static server with two
additions, and the second one is the point.

**The workspace.** A student's `.ds` files do not sit in the checkout as loose
files. They live in `.dsviz/workspace.json`, which this server owns; the editor
reads and writes them over `/api/workspace`. Files can still be created — from
the editor — and the work now survives a reload, which loose files in `src/`
never did unless the student remembered to press save.

**The hand-in.** `solutions/<task>.ds` is written by this server and by nothing
else. It runs the submitted code first and stamps the result into the file, so
what lands in `solutions/` is code that demonstrably ran. Grading recomputes
that stamp; a file put there by hand has none and is refused.

None of this is a lock. It is the student's own checkout, and the engine is
sitting in `app/` next to it — anyone determined can run Python themselves.
What it removes is the accidental path: there is no longer a `src/t1.ds` to
copy into `solutions/` without ever opening the editor. The gate that cannot
be forged is the viva.

    python .devcontainer/serve.py [port]
"""

from __future__ import annotations

import json
import pathlib
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "app"
STORE = ROOT / ".dsviz" / "workspace.json"
SOLUTIONS = ROOT / "solutions"

sys.path.insert(0, str(APP))


# --- the workspace ------------------------------------------------------

def load_workspace() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text()).get("files", {})
        except ValueError:
            pass
    return seed()


def save_workspace(files: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({"version": 1, "files": files}, indent=1) + "\n")


def seed() -> dict:
    """
    What a brand-new workspace starts with.

    A checkout that still has a `src/` directory is being used for authoring —
    that is where the tasks are written and tested as ordinary files — so its
    contents win. A student checkout has no `src/`, and starts from the task
    starters shipped with the editor.
    """
    files = {}
    loose = ROOT / "src"
    if loose.is_dir():
        for path in sorted(loose.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files[path.name] = path.read_text()
        return files
    tasks = APP / "tasks"
    if tasks.is_dir():
        # Every shipped file, not only the .ds ones: a task that reads
        # chunk001.txt needs chunk001.txt to be something the student has.
        for path in sorted(tasks.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files[path.name] = path.read_text()
    return files


# --- the hand-in --------------------------------------------------------

def hand_in(task: str, code: str) -> tuple[int, dict]:
    """
    Run the submission, stamp it, and write it where grading will look.

    Running it here is what makes the stamp mean something: the record cannot
    be written without the code having executed, so `solutions/` cannot fill up
    with code nobody ever ran.
    """
    try:
        from dsviz import attest
        from dsviz.assignment import ASSIGNMENTS
    except ImportError as err:
        return 500, {"error": f"the engine is not installed: {err}"}

    if task not in ASSIGNMENTS:
        return 400, {"error": f"no task called {task!r}"}
    try:
        stamped = attest.stamp(task, code, at=_now(), runs=1)
    except Exception as err:                              # noqa: BLE001
        return 400, {"error": f"this code does not run: {err}"}

    SOLUTIONS.mkdir(parents=True, exist_ok=True)
    target = SOLUTIONS / f"{task}.ds"
    target.write_text(stamped)
    return 200, {"handed_in": str(target.relative_to(ROOT)),
                 "bytes": len(stamped)}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- the server ---------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP), **kwargs)

    # -- reads
    def do_GET(self):                                     # noqa: N802
        if self.path == "/api/workspace":
            self._json(200, {"files": load_workspace()})
            return
        super().do_GET()

    # -- writes
    def do_PUT(self):                                     # noqa: N802
        name = self._named("/api/workspace/")
        if name is None:
            self.send_error(404, "nothing to PUT here")
            return
        files = load_workspace()
        files[name] = self._body()
        save_workspace(files)
        self._json(200, {"saved": name, "bytes": len(files[name])})

    def do_DELETE(self):                                  # noqa: N802
        name = self._named("/api/workspace/")
        if name is None:
            self.send_error(404, "nothing to DELETE here")
            return
        files = load_workspace()
        files.pop(name, None)
        save_workspace(files)
        self._json(200, {"deleted": name})

    def do_POST(self):                                    # noqa: N802
        task = self._named("/api/handin/")
        if task is None:
            self.send_error(404, "nothing to POST here")
            return
        status, payload = hand_in(task, self._body())
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


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not APP.is_dir():
        print(f"no editor at {APP} — run this from the assignment checkout")
        return 1
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Editor on http://localhost:{port}")
    print(f"  workspace: {STORE.relative_to(ROOT)}  ({len(load_workspace())} file(s))")
    print(f"  hand-ins written to: {SOLUTIONS.relative_to(ROOT)}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
