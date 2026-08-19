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

SOLUTION = '''def tokenize(key: string, value: string) -> void:
    for word: string in split(lower(value)):
        emit(word, 1)

def total(key: string, values: [int]) -> int:
    return sum(values)

def byKey(key: string, n: int) -> int:
    return hash(key) mod n


@mapper
class Worker:
    pass

@reducer
class Collector:
    pass

m1 = Worker(speed=1.0)
m2 = Worker(speed=1.0)
r1 = Collector(speed=1.0)
r2 = Collector(speed=1.0)

world = World(machines=[m1, m2, r1, r2])

job = MapReduce(map=tokenize, reduce=total, partition=byKey)
world.run(job)
'''


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


# A checkout with the shape the server expects: an app/ holding the package
# and the task sources, and nothing else.
root = pathlib.Path(tempfile.mkdtemp(prefix="dsviz-server-"))
(root / "app").mkdir()
shutil.copytree(HERE / "dsviz", root / "app" / "dsviz")
shutil.copytree(HERE / "tasks", root / "app" / "tasks")
(root / ".devcontainer").mkdir()
shutil.copy(HERE / "server" / "serve.py", root / ".devcontainer" / "serve.py")

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
proc = subprocess.Popen([sys.executable, str(root / ".devcontainer" / "serve.py"),
                         str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
    assert "a1-wordcount.ds" in body["files"], sorted(body["files"])
    print(f"ok — {len(body['files'])} starter(s) served, no src/ needed")

    print("\n=== work put in comes back out ===")
    status, _ = request(f"{base}/api/workspace/a1-wordcount.ds",
                        method="PUT", body=SOLUTION)
    assert status == 200, status
    status, body = request(f"{base}/api/workspace")
    assert body["files"]["a1-wordcount.ds"] == SOLUTION, "the workspace lost the edit"
    assert (root / ".dsviz" / "workspace.json").exists(), "nothing was persisted"
    print("ok — saved, persisted to .dsviz/workspace.json, and served back")

    print("\n=== the workspace is not a way into the rest of the disk ===")
    for path in ("/api/workspace/../../escape.ds", "/api/workspace/.ssh"):
        status, _ = request(f"{base}{path}", method="PUT", body="x")
        assert status == 404, f"{path} was accepted with {status}"
        print(f"refused: PUT {path}")
    assert not (root.parent / "escape.ds").exists()

    print("\n=== handing in writes solutions/, and only the server does ===")
    assert not (root / "solutions").exists(), "solutions/ exists before any hand-in"
    status, body = request(f"{base}/api/handin/a1-wordcount",
                           method="POST", body=SOLUTION)
    assert status == 200, body
    written = root / "solutions" / "a1-wordcount.ds"
    assert written.exists(), "the hand-in wrote nothing"
    print(f"ok — wrote {body['handed_in']}")

    print("\n=== what it wrote is evidence the code ran ===")
    from dsviz import attest
    reasons = attest.verify("a1-wordcount", written.read_text())
    assert reasons == [], reasons
    # And the same code without going through the server is not.
    assert attest.verify("a1-wordcount", SOLUTION), "an unstamped copy verified"
    print("ok — the written file verifies; the same code copied by hand does not")

    print("\n=== a hand-in of code that does not run is refused ===")
    status, body = request(f"{base}/api/handin/a1-wordcount",
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
