"""
What students actually receive.

Every other suite checks `web/` and `dsviz/` — the source. Students run
`spikey-dsl-1/app/`, which sync.py generates, and a bug introduced *by the
generation step* is invisible to all of them. That has happened: a banner
comment written with `//` in the stylesheet made the browser discard the rule
that closes the welcome dialog, while the source was perfectly fine and every
suite passed.

This suite reads the published copy and asks whether it is still valid.
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
APP = HERE.parent / "spikey-dsl-1" / "app"

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


if not APP.exists():
    print(f"SKIP  no published copy at {APP}")
    raise SystemExit(0)

# --- each file must be valid in its own language ------------------------
css = (APP / "style.css").read_text()
ok("published css has no // comments", not re.search(r"^\s*//", css, re.M),
   "CSS has no // comment; a browser drops everything after one")
ok("published css braces balance",
   css.count("{") == css.count("}"), f"off by {css.count('{') - css.count('}')}")
ok("published css still closes dialogs",
   re.search(r"\[hidden\][^{]*\{[^}]*display:\s*none\s*!important", css) is not None,
   "without this a rule with its own display keeps a hidden element on screen")

html = (APP / "index.html").read_text()
ok("published html starts with the doctype",
   html.lstrip().lower().startswith("<!doctype"),
   "anything before it puts the browser into quirks mode")

# --- javascript must parse ----------------------------------------------
# A syntax error in a generated file is a blank page, so it is worth the
# subprocess. node is not guaranteed to be present; skip rather than fail.
import shutil
import subprocess

if shutil.which("node"):
    for name in ("app.js", "lang.js", "examples.js"):
        r = subprocess.run(["node", "--check", str(APP / name)],
                           capture_output=True, text=True)
        ok(f"published {name} parses", r.returncode == 0,
           r.stderr.strip().splitlines()[0] if r.returncode else "")
else:
    print("SKIP  node not installed — javascript not checked")

# --- python modules must import -----------------------------------------
for path in sorted((APP / "dsviz").glob("*.py")):
    try:
        compile(path.read_text(), str(path), "exec")
        ok(f"published dsviz/{path.name} compiles", True)
    except SyntaxError as e:
        ok(f"published dsviz/{path.name} compiles", False, f"line {e.lineno}: {e.msg}")

# --- and the answers must not be in it ----------------------------------
leaked = [p.name for p in (APP / "dsviz").glob("*.py")
          if "holdout_expects=[" in p.read_text()
          or re.search(r"^    holdout='", p.read_text(), re.M)]
ok("no held-out data in the published copy", not leaked, ", ".join(leaked))

print()
if failures:
    print(f"{len(failures)} PUBLISHED-COPY CHECK(S) FAILED")
    sys.exit(1)
print("ALL PUBLISHED-COPY TESTS PASSED")
