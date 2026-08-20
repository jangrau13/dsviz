"""
What students actually receive.

Every other suite checks `web/` and `dsviz/` — the source. Students run the
*wheel*: `uv add dsviz` puts `web/` inside the package as `dsviz/_web/` and the
built documentation as `dsviz/_site/`, and a bug introduced *by that copy* is
invisible to all of them. That has happened: a banner comment written with `//`
in the stylesheet made the browser discard the rule that closes the welcome
dialog, while the source was perfectly fine and every suite passed.

`package_test.py` asks whether the wheel *contains* the right files. This suite
asks whether what it contains is still *valid* — parseable CSS, parseable
JavaScript, compilable Python, and no held-out answers.

The published copy used to be a synced `spikey-dsl-1/app/` directory. That
repository is gone and the vendoring with it, so this reads the wheel instead;
pointing at a path that no longer exists made the whole suite skip, which reads
as green.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

failures = []


def ok(label, passed, detail=""):
    if not passed:
        failures.append(label)
    print(f"{'ok  ' if passed else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))


def build_wheel(into: pathlib.Path) -> pathlib.Path | None:
    """The same build a student's `uv add` ultimately consumes."""
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
        print("SKIP  no wheel builder available (need uv or python -m build)")
        raise SystemExit(0)

    zipfile.ZipFile(wheel).extractall(tmp / "unpacked")
    PKG = tmp / "unpacked" / "dsviz"
    WEB = PKG / "_web"

    # A skip here would hide exactly what this suite exists to catch, so the
    # published copy being absent is a failure, not a reason to stop.
    ok("the wheel has a published editor", WEB.is_dir(), f"no _web/ in {wheel.name}")
    if not WEB.is_dir():
        print(f"\n{len(failures)} PUBLISHED-COPY CHECK(S) FAILED")
        sys.exit(1)

    # --- each file must be valid in its own language --------------------
    css = (WEB / "style.css").read_text()
    ok("published css has no // comments", not re.search(r"^\s*//", css, re.M),
       "CSS has no // comment; a browser drops everything after one")
    ok("published css braces balance",
       css.count("{") == css.count("}"), f"off by {css.count('{') - css.count('}')}")
    ok("published css still closes dialogs",
       re.search(r"\[hidden\][^{]*\{[^}]*display:\s*none\s*!important", css) is not None,
       "without this a rule with its own display keeps a hidden element on screen")

    html = (WEB / "index.html").read_text()
    ok("published html starts with the doctype",
       html.lstrip().lower().startswith("<!doctype"),
       "anything before it puts the browser into quirks mode")

    # --- javascript must parse ------------------------------------------
    # A syntax error in a published file is a blank page, so it is worth the
    # subprocess. node is not guaranteed to be present; skip rather than fail.
    if shutil.which("node"):
        for name in ("app.js", "lang.js", "examples.js"):
            r = subprocess.run(["node", "--check", str(WEB / name)],
                               capture_output=True, text=True)
            ok(f"published {name} parses", r.returncode == 0,
               r.stderr.strip().splitlines()[0] if r.returncode else "")
    else:
        print("SKIP  node not installed — javascript not checked")

    # --- python modules must import -------------------------------------
    # These are fetched by the browser and run under Pyodide, so a syntax
    # error is the editor failing to start rather than a traceback anyone sees.
    modules = sorted(PKG.glob("*.py"))
    ok("the wheel has engine modules to check", bool(modules), f"{len(modules)} found")
    for path in modules:
        try:
            compile(path.read_text(), str(path), "exec")
            ok(f"published dsviz/{path.name} compiles", True)
        except SyntaxError as e:
            ok(f"published dsviz/{path.name} compiles", False, f"line {e.lineno}: {e.msg}")

    # --- and the answers must not be in it ------------------------------
    leaked = [p.name for p in modules
              if "holdout_expects=[" in p.read_text()
              or re.search(r"^    holdout='", p.read_text(), re.M)]
    ok("no held-out data in the published copy", not leaked, ", ".join(leaked))

print()
if failures:
    print(f"{len(failures)} PUBLISHED-COPY CHECK(S) FAILED")
    sys.exit(1)
print("ALL PUBLISHED-COPY TESTS PASSED")
