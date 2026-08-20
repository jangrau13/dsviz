import sys,json,subprocess,pathlib; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from dsviz import map_reduce, VectorClockRun
from dsviz.shapes import dataflow, spacetime, gantt, color_for

c = map_reduce({"doc1":"the cat sat","doc2":"the dog ran","doc3":"the cat ran"},
               speeds={"machine-2":0.35})
tr = c.sorted_trace()
df, gt = dataflow(tr,title="MapReduce"), gantt(tr,title="Stragglers")
print("dataflow shapes:", len(df), "| kinds:", sorted({s.kind for s in df}))
print("gantt shapes:", len(gt))
bars = sorted([(s.text.split('(')[0], round(s.w,2)) for s in gt if s.kind=="box"])
print("gantt bars:", bars[:4])

r = VectorClockRun("P1","P2","P3")
r.event("P1","a").send("P1","P2","m1").event("P2","b").send("P2","P3","m2")
st = spacetime(r.cluster.sorted_trace(), title="Vector clocks")
print("spacetime shapes:", len(st), "| lanes:", len([s for s in st if s.kind=="lane"]),
      "| arrows:", len([s for s in st if s.kind=="arrow"]))
print("clock markers:", [s.text for s in st if s.kind=="marker"])

j = json.dumps(df.to_json())
print("\nJSON serialises:", len(j), "bytes")
# colour must be stable across processes
root = str(pathlib.Path(__file__).resolve().parents[1])
out = subprocess.run(
  [sys.executable, "-c",
   f"import sys; sys.path.insert(0, {root!r}); "
   "from dsviz.shapes import color_for; print(color_for('the'))"],
  capture_output=True, text=True).stdout.strip()
print("color_for('the') here:", color_for('the'), "| in subprocess:", out)
assert color_for('the')==out, "colour must be stable across processes"
print("\nALL SHAPE TESTS PASSED")

# --- fitting and legibility ---------------------------------------------
# A diagram that runs off the frame is worse than a smaller one: the part you
# cannot see is the part you needed. Layouts fit by default; what fitting
# cannot fix, they say out loud.
from dsviz.shapes import FRAME_W, FRAME_H, MIN_LEGIBLE

small = dataflow(map_reduce({f"doc{i}": f"w{i} the cat" for i in range(3)},
                            partitions=4).sorted_trace(), title="small")
w, h = small.extent()
assert w <= FRAME_W + 1e-6 and h <= FRAME_H + 1e-6, f"small run overflows: {w}x{h}"
assert not small.warnings(), f"a 3-doc run should draw cleanly: {small.warnings()}"
print("small run fits, no warnings:", f"{w:.1f}x{h:.1f}")

big = dataflow(map_reduce({f"doc{i}": f"w{i} the cat" for i in range(40)},
                          partitions=4).sorted_trace(), title="big")
w, h = big.extent()
assert w <= FRAME_W + 1e-6 and h <= FRAME_H + 1e-6, f"big run overflows: {w}x{h}"
assert big.warnings(), "a 40-doc run should warn that it is too crowded to read"
assert any("120" in m or "readable" in m for m in big.warnings()), big.warnings()
print("big run scaled to fit:", f"{w:.1f}x{h:.1f}", "| warns:", len(big.warnings()))

# Every box that survives fitting must still be large enough to label.
boxes = [s for s in big if s.kind == "box" and s.h]
assert all(s.h >= MIN_LEGIBLE for s in boxes), \
    f"fitting made boxes illegible: min {min(s.h for s in boxes):.2f}"
print("boxes stay legible after fitting: min h =",
      f"{min(s.h for s in boxes):.2f}")

# The warning travels with the JSON, so the browser can show it too.
assert "warnings" in big.to_json(), "warnings must reach the renderer"
print("warnings serialise to JSON")

# --- fitting must not make things unreadable ----------------------------
# A review found fit() was called by nothing, and that calling it would have
# been worse than not: the renderer drew labels at a fixed font size, so
# shrinking the boxes spilled text out of every one. Fitting now happens in
# FrameScene.construct, and labels are scaled to the box they sit in.
import re as _re
_renderer = (pathlib.Path(__file__).resolve().parents[1]
             / "dsviz" / "render_manim.py").read_text()

assert "f.fit()" in _renderer, \
    "the video renderer must fit the scene before drawing it"
assert "warnings()" in _renderer, \
    "what fitting cannot fix should be reported, not silently rendered"
print("the renderer fits the scene and reports what it cannot fix")

# The box label must be measured against its box, not drawn at a fixed size.
_box_branch = _renderer.split('if s.kind == "box"')[1].split('if s.kind ==')[0]
assert "label.width" in _box_branch and "scale(" in _box_branch, \
    "box labels must scale to fit their box"
print("box labels scale to the box they sit in")

# And the geometry must actually demand it: a crowded run produces boxes whose
# labels do not fit at full size, which is the case the scaling exists for.
crowded = dataflow(map_reduce({f"doc{i}": f"w{i} the cat" for i in range(40)},
                              partitions=4).sorted_trace(), title="crowded")
worst = max((len(s.text) * 0.11) / max(s.w - 0.2, 0.1)
            for s in crowded if s.kind == "box" and s.w)
assert worst > 1.0, \
    "expected a crowded run to need label scaling; if not, this test is vacuous"
print(f"a crowded run needs label scaling: widest is {worst:.2f}x its box")

# --- contents stay inside the machine that holds them --------------------
# Chips were positioned by hand-tuned offsets from a box centre, so nothing
# kept them in the box: the input chip landed on the machine's own name, and a
# mapper holding six pairs spilled past its bottom edge into the row below.
from dsviz.shapes import BOX_H, CHIP_H, LABEL_STRIP, held_positions

for n in (1, 3, 6, 12, 30):
    ys = held_positions(0.0, n)
    assert len(ys) == n, n
    assert max(ys) + CHIP_H / 2 <= BOX_H / 2 + 1e-9, f"{n} items overflow the top"
    assert min(ys) - CHIP_H / 2 >= -BOX_H / 2 - 1e-9, f"{n} items overflow the bottom"
    # The name label owns the top strip; nothing may sit on it.
    assert max(ys) + CHIP_H / 2 <= BOX_H / 2 - LABEL_STRIP + 1e-9, \
        f"{n} items collide with the machine's name"
print("held items stay inside their box at every count")

busy = map_reduce({"doc1": "the cat sat the dog the cat",
                   "doc2": "the dog ran the cat the sat",
                   "doc3": "the cat ran the sat the dog"}, partitions=2)
frame = dataflow(busy.sorted_trace(), title="busy")
boxes = {s.text: s for s in frame if s.kind == "box"}

escaped = []
for chip in (s for s in frame if s.kind == "chip"):
    owner = chip.meta.get("held_by")
    if not owner or owner not in boxes:
        continue
    b = boxes[owner]
    if not (b.y - b.h / 2 <= chip.y - CHIP_H / 2
            and chip.y + CHIP_H / 2 <= b.y + b.h / 2):
        escaped.append((owner, chip.text, round(chip.y, 2)))
assert not escaped, f"chips outside their box: {escaped[:3]}"
print("no held chip escapes its box in a real run")

# An input is what a machine was handed, so it sits above the box — the
# interior is for what the machine is holding now.
inputs = [s for s in frame if s.kind == "chip" and s.meta.get("role") == "input"]
assert inputs, "expected input chips in a MapReduce run"
for chip in inputs:
    owner = min(boxes.values(), key=lambda b: abs(b.x - chip.x))
    assert chip.y - CHIP_H / 2 >= owner.y + owner.h / 2 - 1e-9, \
        f"input chip {chip.text!r} overlaps {owner.text}"
print("input chips sit above the box, clear of its label")

# Contents must not overlap each other either. A fixed box height meant two
# lines of label took half of it, and six items were crushed into what was
# left; the box grows instead.
from dsviz.shapes import box_height, CHIP_GAP

held_by = {}
for s in frame:
    if s.kind == "chip" and s.meta.get("held_by"):
        held_by.setdefault(s.meta["held_by"], []).append(s.y)
assert held_by, "expected a machine to be holding something"
for owner, ys in held_by.items():
    ys = sorted(ys, reverse=True)
    for a, b in zip(ys, ys[1:]):
        assert a - b >= CHIP_H - 1e-9, \
            f"{owner}: chips overlap, gap {a - b:.3f} < chip height {CHIP_H}"
print("held chips never overlap each other")

# The box is only as tall as it needs to be: a machine holding nothing keeps
# the default, so an RPC diagram does not inherit MapReduce's proportions.
assert box_height(0) == BOX_H and box_height(6) > BOX_H
assert box_height(6) >= LABEL_STRIP + 6 * (CHIP_H + CHIP_GAP)
print("boxes grow only for what they hold:",
      f"0 -> {box_height(0):.1f}, 6 -> {box_height(6):.1f}")

# Arrivals must land inside the box too. These are stacked by the browser, so
# the shape has to carry where the interior starts and how much room is in it —
# stacking blind from the box centre sent a busy reducer's arrivals out through
# the bottom edge, which no amount of held-item checking would have caught.
arrivals = [s for s in frame
            if s.kind == "chip" and s.meta.get("in_flight")]
assert arrivals, "expected shuffle chips in a MapReduce run"
for chip in arrivals:
    box = boxes.get(chip.meta.get("to"))
    if box is None:
        continue
    top = chip.meta["land_top"]
    room = chip.meta["land_room"]
    assert top + CHIP_H / 2 <= box.y + box.h / 2 + 1e-9, \
        f"arrival at {box.text} starts above the box"
    assert top - room - CHIP_H / 2 >= box.y - box.h / 2 - 1e-9, \
        f"arrivals at {box.text} can stack past the bottom edge"
print(f"{len(arrivals)} arriving chips all stack inside their destination")

# --- RPC arrows ----------------------------------------------------------
# A call is a round trip. One line centre-to-centre showed neither direction:
# it passed through both boxes and buried its own arrowhead in the target, and
# a reply that never came looked the same as one that did.
from dsviz.core import Cluster as _Cluster

rpc = _Cluster("grpc")
_srv = rpc.machine("bank", role="server")
_cli = rpc.machine("app", role="client")
_srv.serve("balance", duration=0.4, handler=lambda p: 120)
_cli.call(_srv, "balance", "savings")
_cli.call(_srv, "missing")                    # unimplemented: no reply
rpc_frame = dataflow(rpc.sorted_trace(), title="rpc")
rpc_boxes = [s for s in rpc_frame if s.kind == "box"]

def _inside(x, y):
    return [b.text for b in rpc_boxes
            if abs(x - b.x) < b.w / 2 - 1e-9 and abs(y - b.y) < b.h / 2 - 1e-9]

arrows = [s for s in rpc_frame if s.kind == "arrow"]
assert arrows, "expected arrows for an RPC call"
for a in arrows:
    assert not _inside(a.x, a.y), f"{a.text!r} starts inside {_inside(a.x, a.y)}"
    assert not _inside(a.x2, a.y2), f"{a.text!r} ends inside {_inside(a.x2, a.y2)}"
print(f"{len(arrows)} arrows all stop at the box edge")

legs = [a.meta.get("leg") for a in arrows]
assert legs.count("request") == 2, legs
assert legs.count("reply") == 1, \
    "a successful call gets a reply arrow; a failed one must not"
print("a failed call draws no reply:", legs)

# The reply carries what the handler returned, so changing the body changes
# the picture — the whole point of running the student's code.
reply = next(a for a in arrows if a.meta.get("leg") == "reply")
assert reply.text == "120", reply.text
print("the reply arrow shows the value the code produced:", reply.text)

# And the failure says what went wrong, where the answer would have been.
failed = [s for s in rpc_frame
          if s.kind == "label" and s.meta.get("leg") == "failed"]
assert failed and failed[0].text == "unimplemented", failed
print("a failed call is labelled:", failed[0].text)

# The two directions must not be drawn on top of each other.
req = next(a for a in arrows if a.meta.get("leg") == "request"
           and a.meta.get("method") == "balance")
assert abs(req.x - reply.x2) > 1e-6 or abs(req.y - reply.y2) > 1e-6, \
    "request and reply share a line — the round trip reads as one direction"
print("request and reply are offset from each other")
