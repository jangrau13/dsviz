"""
Evidence that a submission was run before it was handed in.

The editor is where a student is meant to work: they write, watch the dataflow,
see the metrics move, and hand in from there. Nothing in a git repository can
*enforce* that — the files are plain text in the student's own checkout, and
the engine ships beside them, so anything the browser computes a shell can
compute too. Any token strong enough to prove the page was used would have to
be handed to the page, which means handing it to them.

What is achievable is that the shortcut stops working. `cp src/t1.ds
solutions/` is the bypass anyone finds in five seconds, and this makes it fail:
handing in stamps the submitted file with a footer recording *what the run
did*, and grading recomputes that from the committed source. A copied file has
no footer; an edited one has a footer that no longer matches. Getting past it
means running the engine deliberately, which is a different thing from not
bothering.

Treat it as a speed bump and say so. The gate that cannot be forged is the
viva, whose signing key never leaves the examiner.

The record rides in the file rather than beside it because a hand-in has three
routes — the folder picker, the dev server, and GitHub's web editor with the
clipboard — and only one artefact survives all three.
"""

from __future__ import annotations

import hashlib
import json
import re

MARKER = "# dsviz-run: "
BANNER = ("# --- run record ------------------------------------------------"
          "-------------\n"
          "# Written by the editor when you handed in. It says this code was\n"
          "# actually run, and grading checks it against what the code does.\n"
          "# Edit the code above and hand in again; editing this by hand only\n"
          "# breaks it.\n")


def canonical(code: str) -> str:
    """
    The bytes the record is about.

    Line endings and trailing spaces differ between a checkout on Windows, an
    editor that trims, and a clipboard round-trip through GitHub's web editor.
    None of those change what the program does, so none of them may change the
    hash — a student whose file went through a CRLF checkout has not tampered
    with anything.
    """
    lines = [line.rstrip() for line in code.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip() + "\n"


def split(text: str) -> tuple[str, dict | None]:
    """Separate a stamped file into its code and its record."""
    record = None
    kept = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith(MARKER):
            try:
                record = json.loads(line[len(MARKER):])
            except ValueError:
                record = {}
            continue
        if line.startswith("# --- run record") or line.startswith("# Written by the editor") \
           or line.startswith("# actually run") or line.startswith("# Edit the code above") \
           or line.startswith("# breaks it."):
            continue
        kept.append(line)
    return "\n".join(kept), record


def source_sha(code: str) -> str:
    return hashlib.sha256(canonical(code).encode()).hexdigest()


def seed_for(code: str) -> int:
    """
    The seed the record's run used.

    Derived from the code so that grading can reproduce the same run without
    the record being able to choose it — a record that picked its own seed
    could pick one whose trace happens to be cheap to guess.
    """
    return int(source_sha(code)[:8], 16)


def program_for(task: str, code: str) -> str:
    """
    The whole program, the way the editor runs it.

    A task supplies its own input — the splits, the chunk files — and the
    student writes only the functions. Hashing the student's half alone would
    hash something that does not run, so the task's visible half is put back
    first. The held-out half is never used here: the browser does not have it,
    and a record it could not produce would be no use as evidence.
    """
    from .assignment import ASSIGNMENTS

    spec = ASSIGNMENTS.get(task)
    return spec.program(code) if spec else code


def trace_sha(code: str, task: str = "") -> str:
    """
    A digest of what the program actually did.

    This is the part that cannot be produced by looking at the file: it is the
    ordered list of events the simulation emitted, which means running it. The
    seed is fixed from the source, so the same code gives the same digest here
    and in CI even though an unseeded run of a fallible system does not repeat.
    """
    from .assignment import build_cluster
    from .langserver import detect_dialect

    # Canonical, like the source hash: the two must describe the same bytes,
    # or a checkout that changed line endings would hash as untouched and run
    # as something else.
    whole = program_for(task, canonical(code))
    cluster = build_cluster(detect_dialect(whole), whole, seed=seed_for(code))
    events = [
        # Times are rounded before hashing: the last bits of a float are the
        # one thing that could differ between Pyodide and CPython, and no
        # student's work turns on the fifth decimal of a millisecond.
        {"t": round(e.t, 4), "kind": e.kind, "machine": e.machine,
         "detail": {k: str(v) for k, v in sorted(e.detail.items())}}
        for e in cluster.trace
    ]
    return hashlib.sha256(
        json.dumps(events, sort_keys=True).encode()).hexdigest()


def stamp(task: str, code: str, *, at: str = "", runs: int = 0) -> str:
    """Return the code with a fresh run record appended."""
    body, _ = split(code)
    body = canonical(body)
    record = {
        "task": task,
        "source": source_sha(body),
        "trace": trace_sha(body, task),
        "at": at,
        "runs": runs,
    }
    return body + "\n" + BANNER + MARKER + json.dumps(record,
                                                      separators=(",", ":")) + "\n"


def verify(task: str, text: str) -> list[str]:
    """
    Why this file is not evidence of a run. Empty means it is.

    Each reason says what to do about it, because the student reading it in a
    CI log has no other way to find out.
    """
    body, record = split(text)
    if record is None:
        return ["this file carries no run record — it was not handed in from "
                "the editor. Open the editor, run your code, and press "
                "hand in; copying a file into solutions/ is not a submission."]
    if not record:
        return ["the run record is not readable. Hand in again from the editor."]

    reasons = []
    if record.get("task") != task:
        reasons.append(
            f"the run record is for {record.get('task')!r}, but this file is "
            f"{task}.ds. Hand in each task from its own tab.")
    if record.get("source") != source_sha(body):
        reasons.append(
            "the code was edited after it was handed in, so the run record "
            "describes something else. Run it in the editor and hand in again.")
        return reasons                      # the trace cannot mean anything now
    try:
        actual = trace_sha(body, task)
    except Exception as err:                # noqa: BLE001 — reported, not raised
        return reasons + [f"this code could not be run at all: {err}"]
    if record.get("trace") != actual:
        reasons.append(
            "the run record does not match what this code does when it runs. "
            "Hand in from the editor rather than writing the record by hand.")
    return reasons
