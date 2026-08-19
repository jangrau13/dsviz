"""
Learning objectives follow the HDZ-HSG guidance.

Each must open with an action verb from Krathwohl's revised taxonomy at the
level it claims, and state a criterion rather than only a topic. Checked
mechanically so an objective added later cannot quietly drift back to "students
will understand X".
"""
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dsviz.assignment import ASSIGNMENTS, GOALS

# From "Verbs for Learning objective" (Anderson & Krathwohl 2001), as issued.
VERBS = {
    "remember": {"choose", "define", "find", "label", "list", "match", "name",
                 "recall", "relate", "select", "show", "spell", "tell"},
    "understand": {"classify", "compare", "contrast", "demonstrate", "explain",
                   "extend", "illustrate", "infer", "interpret", "outline",
                   "rephrase", "summarize", "translate", "describe"},
    "apply": {"apply", "build", "construct", "develop", "identify", "model",
              "organize", "plan", "solve", "utilize", "choose", "select"},
    "analyse": {"analyze", "categorize", "classify", "compare", "contrast",
                "discover", "dissect", "distinguish", "divide", "examine",
                "inspect", "simplify", "survey", "test"},
    "evaluate": {"appraise", "assess", "award", "conclude", "criticize",
                 "decide", "defend", "determine", "disprove", "estimate",
                 "evaluate", "judge", "justify", "measure", "prioritize",
                 "prove", "rate", "recommend", "support", "value"},
    "create": {"adapt", "build", "change", "combine", "compile", "compose",
               "construct", "create", "design", "develop", "elaborate",
               "formulate", "improve", "invent", "modify", "originate",
               "plan", "predict", "propose", "test"},
}

for key, goal in GOALS.items():
    text, level = goal["text"], goal["level"]
    assert level in VERBS, f"{key}: unknown taxonomy level {level!r}"
    assert text.startswith("Students will "), f"{key}: {text!r}"

    verb = re.match(r"Students will (\w+)", text).group(1).lower()
    assert verb in VERBS[level], (
        f"{key}: {verb!r} is not a {level} verb in the HDZ-HSG list")

    # A criterion, not just a topic: the guidance asks for the standard
    # performance is judged against.
    assert len(text.split()) >= 12, f"{key}: too vague to be measurable — {text!r}"
    assert any(w in text for w in (",", " by ", " in terms of ", " demonstrated ")), (
        f"{key}: states no criterion — {text!r}")
print(f"ok all {len(GOALS)} objectives use a sanctioned verb and state a criterion")

# Vague verbs the guidance exists to prevent.
BANNED = {"understand", "know", "learn", "appreciate", "be", "grasp"}
for key, goal in GOALS.items():
    verb = re.match(r"Students will (\w+)", goal["text"]).group(1).lower()
    assert verb not in BANNED, f"{key}: {verb!r} is not observable"
print("ok no unobservable verbs")

# Every task carries objectives, and the graded ones reach beyond recall.
for name, spec in ASSIGNMENTS.items():
    assert spec.goals, f"{name} has no learning objectives"
    for g in spec.goals:
        assert g in GOALS, f"{name} references unknown objective {g!r}"
    levels = {GOALS[g]["level"] for g in spec.goals}
    if spec.expects or spec.budgets:          # a graded task
        assert levels & {"apply", "analyse", "evaluate", "create"}, (
            f"{name} only asks students to remember or understand: {levels}")
    print(f"ok {name}: {len(spec.goals)} objective(s), levels {sorted(levels)}")

print("\nALL OBJECTIVE TESTS PASSED")
