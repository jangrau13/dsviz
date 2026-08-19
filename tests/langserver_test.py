"""
What the editor tells a student as they type.

The editor is the only thing most students will ever see, and it is the one
component the Python suites do not exercise: they call the builders directly,
which is a different entry point from the one the page uses. Both bugs pinned
here were invisible that way and obvious on screen.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dsviz.assignment import ASSIGNMENTS                        # noqa: E402
from dsviz.langserver import analyse                            # noqa: E402

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def diagnostics(src, task=""):
    return json.loads(analyse(src, task))["diagnostics"]


def errors(src, task=""):
    return [d for d in diagnostics(src, task) if d["severity"] == "error"]


# --- a shipped task must look clean when it is opened -------------------
# Both ways the page can ask: with the task selected, which is what an
# exercise does, and without, which is what free play does. A starter that
# reports errors on open teaches the student to ignore the error panel.
for name, spec in ASSIGNMENTS.items():
    got = errors(spec.starter, name)
    if spec.expects or spec.requires or spec.budgets:
        # A graded task ships without its wiring, so it opens with exactly one
        # complaint: that the job has not been written yet. That is the task,
        # stated. Anything else on top of it is noise on first open.
        ok(f"{name} opens saying only that it is not wired up yet",
           len(got) == 1 and "which functions do the work" in got[0]["message"],
           "; ".join(d["message"] for d in got[:3]) or "(nothing)")
        ok(f"{name} says it at the end of the code, not at line 1",
           bool(got) and got[0]["line"] > 1,
           f"line {got[0]['line']}" if got else "n/a")
    else:
        ok(f"{name} opens clean in its task",
           not got, "; ".join(d["message"] for d in got[:3]))

# Free play has no task to supply input, so a MapReduce starter is correctly
# short of its splits. Every other dialect must be clean.
for name, spec in ASSIGNMENTS.items():
    if spec.dialect == "mapreduce":
        continue
    got = errors(spec.starter)
    ok(f"{name} opens clean in free play",
       not got, f"{len(got)} error(s): "
       + "; ".join(d["message"] for d in got[:3]))

# --- a call to something that does not exist is reported ----------------
# `def balance` renamed to `def balanc`, the call left alone. This was silent:
# no diagnostic, and at runtime the call came back `unimplemented` with no
# reply, so the caller bound nothing and passed nothing on. A typo produced a
# run that looked like a run.
t0 = ASSIGNMENTS["t0-rpc"].starter
typo = errors(t0.replace("def balance(", "def balanc("), "t0-rpc")
ok("a call to a method that does not exist is reported",
   any("does not answer" in d["message"] for d in typo),
   "; ".join(d["message"] for d in typo[:2]) or "(nothing reported)")
ok("and it is reported at the call, not at the declaration",
   bool(typo) and "bank.balance" in
   t0.replace("def balance(", "def balanc(").split("\n")[typo[0]["line"] - 1],
   f"line {typo[0]['line']}" if typo else "n/a")
ok("and it names what the machine does answer",
   bool(typo) and "balanc" in (typo[0].get("hint") or ""),
   (typo[0].get("hint") if typo else "") or "(no hint)")

missing = errors(t0.replace('bank.balance("savings")',
                            'nosuch.balance("savings")'), "t0-rpc")
ok("a call to a machine that does not exist is reported",
   any("no machine called" in d["message"] for d in missing),
   "; ".join(d["message"] for d in missing[:2]) or "(nothing reported)")

# --- the runtime's own verbs are not mistaken for missing methods -------
# `send` and `broadcast` are answered by the runtime on any process's behalf,
# so a class does not declare them. Checking calls inside plain functions —
# which is what fixed the typo case — reported all four sends in the clocks
# task as methods `Node` fails to answer.
for name in ("t4-clocks", "t8-lamport", "t9-buffering"):
    got = errors(ASSIGNMENTS[name].starter, name)
    ok(f"{name}: send and broadcast are not reported as missing methods",
       not any("does not answer" in d["message"] for d in got),
       "; ".join(d["message"] for d in got[:2]))

# `crash` and `restart` likewise.
crashing = errors(t0.replace("    # bank.crash()", "    bank.crash()"), "t0-rpc")
ok("crash and restart are not reported as missing methods",
   not any("does not answer" in d["message"] for d in crashing),
   "; ".join(d["message"] for d in crashing[:2]))

# --- the page gets a diagram when the program is sound ------------------
# A clean program with nothing to draw is a blank right-hand panel, which
# reads as a broken page rather than as a program that did nothing.
for name in ("t0-rpc", "t4-clocks", "t3-spark"):
    payload = json.loads(analyse(ASSIGNMENTS[name].starter, name))
    ok(f"{name} produces a diagram", bool(payload.get("frame")),
       "no frame" if not payload.get("frame") else "")

print("ALL LANGUAGE-SERVER TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
