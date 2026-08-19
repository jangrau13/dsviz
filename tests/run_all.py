"""Run every test module. `python tests/run_all.py`

The Python suites cover the language and the simulator. `ui_test.mjs` covers
the page itself, which they cannot reach: the editor once shipped with every
control dead — a throw in the first line of the wiring block — while all ten
Python suites passed. It needs node and jsdom; if jsdom is absent the UI check
is reported as skipped rather than quietly dropped.
"""
import pathlib, shutil, subprocess, sys

here = pathlib.Path(__file__).parent
failed, skipped = [], []

for path in sorted(here.glob("*_test.py")):
    r = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {path.name:<18} {tail}")
    if r.returncode:
        failed.append(path.name)
        print(r.stderr.strip()[-500:])

for path in sorted(here.glob("*_test.mjs")):
    if not shutil.which("node"):
        print(f"SKIP  {path.name:<18} node not installed")
        skipped.append(path.name)
        continue
    r = subprocess.run(["node", str(path)], capture_output=True, text=True,
                       cwd=here.parent)
    if r.returncode and "Cannot find package 'jsdom'" in r.stderr:
        print(f"SKIP  {path.name:<18} jsdom not installed (npm install jsdom)")
        skipped.append(path.name)
        continue
    tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {path.name:<18} {tail}")
    if r.returncode:
        failed.append(path.name)
        print((r.stdout + r.stderr).strip()[-800:])

total = len(list(here.glob("*_test.py"))) + len(list(here.glob("*_test.mjs")))
print(f"\n{total - len(failed) - len(skipped)} passed, {len(failed)} failed"
      + (f", {len(skipped)} skipped" if skipped else ""))
sys.exit(1 if failed else 0)
