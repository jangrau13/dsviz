"""
The server is the file system, so it is the thing under test.

Two properties matter, and neither is visible from reading app.js. The
workspace has to survive — files go in, the same files come back, a reload is
not a loss. And `solutions/` has to be reachable only through a hand-in that
ran the code, because that is the whole reason the loose `src/` files went
away.

The server is started for real, against a throwaway checkout, and talked to
over HTTP. Importing its functions would prove the functions work; it would
not prove the routes are wired to them.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixture                                              # noqa: E402

SOLUTION = fixture.SOLUTION
TASK = "fx-takings"


def request(url, *, method="GET", body=None):
    req = urllib.request.Request(url, method=method,
                                 data=body.encode() if body is not None else None)
    def payload(raw):
        # A refusal comes back as send_error's HTML page, not as JSON; the test
        # cares about the status either way.
        try:
            return json.loads(raw.decode() or "{}")
        except ValueError:
            return {"error": raw.decode()[:120]}

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, payload(res.read())
    except urllib.error.HTTPError as err:
        return err.code, payload(err.read())


# A checkout with the shape the server expects: an exercise, which is a
# directory with a `tasks.py` and the starters it names. Nothing else — the
# server is `dsviz serve`, run against it the way a student runs it.
root = pathlib.Path(tempfile.mkdtemp(prefix="dsviz-server-"))
shutil.copytree(fixture.EXERCISE / "tasks", root / "tasks")
shutil.copy(fixture.EXERCISE / "tasks.py", root / "tasks.py")
(root / "pyproject.toml").write_text(
    '[project]\nname = "fixture"\nversion = "0"\n')


def free_port() -> int:
    """
    A port nothing else is on.

    A fixed port made this suite lie: a server left over from an earlier run
    was still holding it, the new one failed to bind, and the test talked to
    the old server — which was rooted at a temporary directory that no longer
    existed. It failed on the second assertion rather than the first, which is
    the worst way for a test to be wrong.
    """
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()
# The real entry point, run from the exercise the way a student runs it.
proc = subprocess.Popen([sys.executable, "-m", "dsviz.cli", "serve", str(PORT)],
                        cwd=str(root), env={**os.environ, "PYTHONPATH": str(HERE)},
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{PORT}"
for _ in range(80):
    if proc.poll() is not None:
        raise SystemExit(f"the server exited: {proc.stdout.read().decode()}")
    try:
        request(f"{base}/api/workspace")
        break
    except Exception:
        time.sleep(0.25)
else:
    proc.kill()
    raise SystemExit("the server never came up")

try:
    print("=== a fresh workspace starts from the task starters ===")
    status, body = request(f"{base}/api/workspace")
    assert status == 200, status
    assert f"{TASK}.ds" in body["files"], sorted(body["files"])
    print(f"ok — {len(body['files'])} starter(s) served, no src/ needed")

    print("\n=== work put in comes back out ===")
    status, _ = request(f"{base}/api/workspace/{TASK}.ds",
                        method="PUT", body=SOLUTION)
    assert status == 200, status
    status, body = request(f"{base}/api/workspace")
    assert body["files"][f"{TASK}.ds"] == SOLUTION, "the workspace lost the edit"
    assert (root / ".dsviz" / "workspace.json").exists(), "nothing was persisted"
    print("ok — saved, persisted to .dsviz/workspace.json, and served back")

    print("\n=== the workspace is not a way into the rest of the disk ===")
    for path in ("/api/workspace/../../escape.ds", "/api/workspace/.ssh"):
        status, _ = request(f"{base}{path}", method="PUT", body="x")
        assert status == 404, f"{path} was accepted with {status}"
        print(f"refused: PUT {path}")
    assert not (root.parent / "escape.ds").exists()

    print("\n=== handing in writes result/, and only the server does ===")
    assert not (root / "result").exists(), "result/ exists before any hand-in"
    status, body = request(f"{base}/api/handin/{TASK}",
                           method="POST", body=SOLUTION)
    assert status == 200, body
    written = root / "result" / f"{TASK}.ds"
    assert written.exists(), "the hand-in wrote nothing"
    print(f"ok — wrote {body['handed_in']}")

    print("\n=== what it wrote is evidence the code ran ===")
    from dsviz import attest
    reasons = attest.verify(TASK, written.read_text())
    assert reasons == [], reasons
    # And the same code without going through the server is not.
    assert attest.verify(TASK, SOLUTION), "an unstamped copy verified"
    print("ok — the written file verifies; the same code copied by hand does not")

    print("\n=== a hand-in of code that does not run is refused ===")
    status, body = request(f"{base}/api/handin/{TASK}",
                           method="POST", body="def broken(")
    assert status == 400, status
    assert written.read_text() != "def broken(", "the bad hand-in overwrote the good one"
    print(f"ok — refused: {body['error'][:60]}…")

    status, body = request(f"{base}/api/handin/not-a-task", method="POST", body=SOLUTION)
    assert status == 400, status
    print(f"ok — refused an unknown task: {body['error']}")

    print("\nALL SERVER TESTS PASSED")
finally:
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(root, ignore_errors=True)
