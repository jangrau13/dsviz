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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixture  # noqa: E402,F401  fills the registry

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
    if spec.expects or spec.requires:
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
t0 = ASSIGNMENTS["fx-calls"].starter
typo = errors(t0.replace("def stock(", "def stok("), "fx-calls")
ok("a call to a method that does not exist is reported",
   any("does not answer" in d["message"] for d in typo),
   "; ".join(d["message"] for d in typo[:2]) or "(nothing reported)")
ok("and it is reported at the call, not at the declaration",
   bool(typo) and "depot.stock" in
   t0.replace("def stock(", "def stok(").split("\n")[typo[0]["line"] - 1],
   f"line {typo[0]['line']}" if typo else "n/a")
ok("and it names what the machine does answer",
   bool(typo) and "stok" in (typo[0].get("hint") or ""),
   (typo[0].get("hint") if typo else "") or "(no hint)")

missing = errors(t0.replace('depot.stock("ladders")',
                            'nosuch.stock("ladders")'), "fx-calls")
ok("a call to a machine that does not exist is reported",
   any("no machine called" in d["message"] for d in missing),
   "; ".join(d["message"] for d in missing[:2]) or "(nothing reported)")

# --- the runtime's own verbs are not mistaken for missing methods -------
# `send` and `broadcast` are answered by the runtime on any process's behalf,
# so a class does not declare them. Checking calls inside plain functions —
# which is what fixed the typo case — reported all four sends in the clocks
# task as methods `Node` fails to answer.
for name in ("fx-ticks",):
    got = errors(ASSIGNMENTS[name].starter, name)
    ok(f"{name}: send and broadcast are not reported as missing methods",
       not any("does not answer" in d["message"] for d in got),
       "; ".join(d["message"] for d in got[:2]))

# `crash` and `restart` likewise.
crashing = errors(t0.replace("    # depot.crash()", "    depot.crash()"), "fx-calls")
ok("crash and restart are not reported as missing methods",
   not any("does not answer" in d["message"] for d in crashing),
   "; ".join(d["message"] for d in crashing[:2]))

# --- the page gets a diagram when the program is sound ------------------
# A clean program with nothing to draw is a blank right-hand panel, which
# reads as a broken page rather than as a program that did nothing.
for name in ("fx-calls", "fx-ticks", "fx-stages"):
    payload = json.loads(analyse(ASSIGNMENTS[name].starter, name))
    ok(f"{name} produces a diagram", bool(payload.get("frame")),
       "no frame" if not payload.get("frame") else "")

# --- the editor colours the language that exists ------------------------
# The highlighter is the one part of the editor that cannot be generated from
# the Python tables, because Monarch is a JavaScript object. So it is checked
# instead: every word it paints must be one the grammar has, and every keyword
# the grammar has must be painted. This drifted through a whole change of
# notation once — the editor was still colouring `takes`, `calls` and
# `service` long after the language became Python-shaped, while `def`,
# `class` and `return` were left as plain identifiers.
import re  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANG_JS = (ROOT / "web" / "lang.js").read_text()
GRAMMAR = (ROOT / "dsviz" / "grammar.py").read_text()


def js_list(name: str) -> set:
    body = re.search(r"const " + name + r" = \[(.*?)\];", LANG_JS, re.S)
    return set(re.findall(r'"([^"]+)"', body.group(1))) if body else set()


painted = js_list("BLOCKS") | js_list("STATEMENTS") | js_list("TYPES")
declared = set(re.findall(r"^KW_\w+\.\d+: /(\w+)\\b/", GRAMMAR, re.M))
# `mappers|reducers|…` and `on|off` are alternations inside one token rather
# than a token each, so they are read out of the alternation.
for group in re.findall(r"^(?:CONFIG_KEY|ONOFF)\.\d+: /\(\?:([^)]*)\)", GRAMMAR, re.M):
    declared |= set(group.split("|"))
# Words the grammar spells out inside a rule rather than as a keyword token,
# plus the type names and the word operators. Painting them is right; there is
# no KW_ token for them to match.
BY_HAND = {"parallel", "and", "or", "not", "mod",
           "int", "string", "pair", "void"}
# `split` is both an input declaration and a builtin function, and inside a
# body it is the function a student means. It is left to the vocabulary, which
# comes from the same table the reference does.
UNPAINTED = {"split"}

ok("the editor paints nothing the grammar does not have",
   not (painted - declared - BY_HAND),
   ", ".join(sorted(painted - declared - BY_HAND)))
ok("the editor paints every keyword the grammar has",
   not (declared - painted - UNPAINTED),
   ", ".join(sorted(declared - painted - UNPAINTED)))
ok("the grammar's keywords were actually found",
   len(declared) > 10, f"{len(declared)} found")

# Nothing from the notation this one replaced may survive in the editor. Each
# of these was a completion that inserted syntax the parser refuses.
GONE = {"service", "client", "calls", "takes", "crashes", "restarts"}
left = GONE & set(re.findall(r'"([a-z_]+)"', LANG_JS))
ok("no statement from the old notation is left in the editor",
   not left, ", ".join(sorted(left)))

print("ALL LANGUAGE-SERVER TESTS PASSED" if not failures
      else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
