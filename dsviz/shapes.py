"""
Shapes: the renderer-agnostic middle layer.

A trace says *what happened*; shapes say *what to draw*. Both the Manim
renderer and the browser consume this, so a diagram looks the same in a lecture
video and in the student's editor.

Deliberately dumb: primitives with positions, times and colours. No Manim
imports, no DOM — it serialises to JSON and crosses any boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .core import Trace
from .values import default_visual

# Stable colour per key/process, so the same thing is the same colour in every
# view. Chosen to stay legible on dark backgrounds.
PALETTE = ["#4C9BE8", "#E8B44C", "#63C77A", "#D96A9E", "#9B7BE8",
           "#4CD4C4", "#E87A4C", "#B4E84C"]

# RPC outcomes get their own colours: a failure must not look like a success.
STATUS_COLORS = {
    "ok": "#63C77A",
    "unavailable": "#E05252",
    "deadline_exceeded": "#E8B44C",
    "unimplemented": "#D96A9E",
}


def payload_label(payload, limit: int = 22) -> str:
    """A short, readable label — from the value's own `to_manim()`."""
    return default_visual(payload, limit=limit).text


def color_for(token) -> str:
    """Deterministic colour for a key or process name.

    Uses an explicit checksum, not `hash()`: Python randomises string hashing
    per process, so `hash()` would give the same key a different colour in the
    lecture video than in the student's browser.
    """
    s = str(token)
    n = 0
    for ch in s:
        n = (n * 31 + ord(ch)) & 0xFFFFFFFF
    return PALETTE[n % len(PALETTE)]


@dataclass
class Shape:
    kind: str                       # box | chip | arrow | lane | label | marker
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    x2: float = 0.0                 # arrows only
    y2: float = 0.0
    text: str = ""
    color: str = "#9AA0A6"
    t_in: float = 0.0               # when it appears
    t_out: float | None = None      # when it leaves, None = stays
    meta: dict = field(default_factory=dict)


# Manim's default frame, in its own units. A diagram is measured against this
# whatever renders it, so the browser and the video agree on what fits.
FRAME_W, FRAME_H = 14.2, 8.0

# Below this, a box is too small to hold a legible label at 1080p. Shrinking
# past it does not make a crowded diagram readable — it makes it unreadable at
# a smaller size, which is worse than admitting the run is too big to draw.
MIN_LEGIBLE = 0.42

# How far a request and its reply are pushed apart, so a round trip reads as
# two directions rather than one line drawn twice.
ARROW_OFFSET = 0.22

# A machine's box, and how its contents sit inside it.
#
# Chips used to be placed by hand-tuned offsets from the box centre, which meant
# nothing kept them inside it: the input chip landed on the machine's own name,
# and a mapper holding six pairs spilled its last one past the bottom edge and
# into the row below. These constants make the box the authority — the label
# owns the top strip, the contents own what is left, and `held_positions` never
# returns a point outside.
BOX_H = 1.5
# Two lines of text sit at the top of a box: the machine's name, then its role
# and speed. The renderers draw them at +0.32 and +0.58 below the top edge, so
# the strip they occupy reaches roughly +0.70 once the second line's own height
# is counted. Chips start below that. Getting this wrong is not subtle — the
# contents land on the name.
LABEL_STRIP = 0.72
CHIP_H = 0.30
CHIP_GAP = 0.04


def box_height(most_held: int) -> float:
    """
    How tall a machine's box has to be to hold what it holds.

    A fixed height was the reason chips overlapped: two lines of label take the
    top 0.72 of a 1.5-unit box, leaving less room for six items than six items
    need. The box grows instead, so the contents are laid out at a readable
    spacing rather than crushed into whatever was left over.
    """
    if most_held <= 0:
        return BOX_H
    needed = LABEL_STRIP + most_held * (CHIP_H + CHIP_GAP) + CHIP_GAP
    return max(BOX_H, needed)


def edge_points(a, b, *, box_a, box_b, offset: float = 0.0):
    """
    A line between two boxes, ending at their edges rather than their centres.

    An arrow drawn centre-to-centre runs *through* both boxes and buries its
    own arrowhead inside the target, which is what made the RPC arrows
    unreadable. `offset` pushes the line sideways so a call and its reply are
    two visible lines rather than one line drawn twice.

    Returns (x1, y1, x2, y2).
    """
    import math

    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    dist = math.hypot(dx, dy)
    if dist < 1e-9:
        return ax, ay, bx, by
    ux, uy = dx / dist, dy / dist
    # Perpendicular, so the two directions of a round trip do not overlap.
    px, py = -uy * offset, ux * offset

    def leave(cx, cy, w, h, ox, oy, sx, sy):
        """
        Where a ray from an offset start-point leaves the box centred at (cx, cy).

        The offset has to be part of the calculation, not applied afterwards:
        clipping the centre line and then shifting it sideways puts the endpoint
        back inside the box, one edge over.
        """
        # The first edge the ray crosses, which is the nearest crossing ahead
        # of it — taking the furthest walks straight out the far side.
        hits = []
        for half, o, d in ((w / 2, ox, sx), (h / 2, oy, sy)):
            if abs(d) < 1e-9:
                continue
            t = ((half if d > 0 else -half) - o) / d
            if t > 1e-9:
                hits.append(t)
        t = min(hits) if hits else 0.0
        return cx + ox + sx * t, cy + oy + sy * t

    x1, y1 = leave(ax, ay, box_a[0], box_a[1], px, py, ux, uy)
    x2, y2 = leave(bx, by, box_b[0], box_b[1], px, py, -ux, -uy)
    return x1, y1, x2, y2


def held_positions(py: float, count: int, *, box_h: float = BOX_H) -> list[float]:
    """
    Where a machine's held items sit, top to bottom, inside its own box.

    Returns one y per item, all within the box's interior and below the label
    strip. If the box is too small for the count at full spacing — which
    `box_height` exists to prevent — the spacing tightens rather than the
    column growing, because a diagram that overflows is worse than one that is
    merely dense.
    """
    if count <= 0:
        return []
    top = py + box_h / 2 - LABEL_STRIP - CHIP_H / 2
    bottom = py - box_h / 2 + CHIP_H / 2
    room = top - bottom
    step = CHIP_H + CHIP_GAP
    if count > 1 and (count - 1) * step > room:
        step = room / (count - 1)
    return [top - i * step for i in range(count)]



class Frame(list):
    """A drawable scene: shapes plus the timeline they live on."""

    def __init__(self, shapes=(), *, duration: float = 0.0, title: str = ""):
        super().__init__(shapes)
        self.duration = duration
        self.title = title

    # -- fitting -------------------------------------------------------
    def extent(self) -> tuple[float, float]:
        """Width and height actually occupied, arrow endpoints included."""
        if not self:
            return 0.0, 0.0
        xs, ys = [], []
        for s in self:
            xs += [s.x - s.w / 2, s.x + s.w / 2]
            ys += [s.y - s.h / 2, s.y + s.h / 2]
            if s.kind == "arrow":
                xs += [s.x2]
                ys += [s.y2]
        return max(xs) - min(xs), max(ys) - min(ys)

    def fit(self, *, width: float = FRAME_W, height: float = FRAME_H) -> Frame:
        """
        Scale the whole scene down until it fits, keeping its proportions.

        Layouts already compress their own gaps, but only along the axis they
        were written to manage — `dataflow` clamps width and lets rows run off
        the bottom. Scaling here catches whatever is left, uniformly, so a
        diagram never silently extends past the frame.
        """
        w, h = self.extent()
        if w <= width and h <= height:
            return self
        k = min(width / w if w else 1.0, height / h if h else 1.0)
        for s in self:
            s.x *= k; s.y *= k; s.w *= k; s.h *= k
            s.x2 *= k; s.y2 *= k
        self.scaled = k
        return self

    def warnings(self) -> list[str]:
        """
        What a reader would struggle with, in plain words.

        Fitting can always make a scene *fit*; it cannot make it *readable*.
        This reports the cases where the result is too small or too crowded to
        follow, so a caller can say so rather than render something no one can
        use.
        """
        out = []
        w, h = self.extent()
        if w > FRAME_W or h > FRAME_H:
            out.append(f"the diagram is {w:.1f}x{h:.1f} units and the frame is "
                       f"{FRAME_W}x{FRAME_H} — call fit() or draw fewer machines")
        boxes = [s for s in self if s.kind == "box" and s.h]
        small = [s for s in boxes if s.h < MIN_LEGIBLE]
        if small:
            out.append(f"{len(small)} of {len(boxes)} boxes are below "
                       f"{MIN_LEGIBLE} units and their labels will not be "
                       f"readable — fewer machines, or split the diagram")
        chips = [s for s in self if s.kind == "chip"]
        if len(chips) > 120:
            out.append(f"{len(chips)} items move in this diagram — past about "
                       f"120 they overlap into noise; use a smaller input to "
                       f"show the idea")
        return out

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "duration": self.duration,
            "shapes": [asdict(s) for s in self],
            "warnings": self.warnings(),
        }


# --- layouts ------------------------------------------------------------
# Each turns a Trace into a Frame. Adding a diagram type means adding a
# function here; neither the simulator nor the renderers change.

def spacetime(trace: Trace, *, title: str = "", lane_gap: float = 1.6,
              seconds_per_unit: float = 1.4, max_height: float = 6.5,
              max_width: float = 12.0) -> Frame:
    """
    Processes as horizontal lanes, messages as diagonal arrows.

    The standard teaching diagram for vector clocks and any message-passing
    protocol: time runs left to right, each process owns a lane, and a message
    is a line sloping forward in time.
    """
    names = trace.machines()
    # Compress lanes and the time axis so any number of processes fits.
    if len(names) > 1:
        lane_gap = min(lane_gap, max_height / (len(names) - 1))
    span = trace.duration * seconds_per_unit
    if span > max_width:
        seconds_per_unit = max_width / trace.duration
    lane_y = {n: -i * lane_gap + (len(names) - 1) * lane_gap / 2
              for i, n in enumerate(names)}
    shapes: list[Shape] = []

    def x(t):
        return t * seconds_per_unit

    for n in names:
        shapes.append(Shape("lane", x=0, y=lane_y[n],
                            x2=x(trace.duration) + 1.0, y2=lane_y[n],
                            text=n, color=color_for(n)))

    for e in trace:
        y = lane_y.get(e.machine, 0)
        if e.kind == "send":
            shapes.append(Shape("arrow", x=x(e.t), y=y,
                                x2=x(e.detail["arrive"]),
                                y2=lane_y.get(e.detail["to"], 0),
                                text=str(e.detail.get("payload", "")),
                                color=color_for(e.machine), t_in=e.t,
                                meta={"to": e.detail["to"]}))
        elif e.kind == "clock":
            shapes.append(Shape("marker", x=x(e.t), y=y,
                                text=str(e.detail["clock"]),
                                color=color_for(e.machine), t_in=e.t,
                                meta={"label": e.detail.get("label", "")}))
        elif e.kind == "crash":
            shapes.append(Shape("marker", x=x(e.t), y=y, text="✕",
                                color="#E05252", t_in=e.t))
        elif e.kind == "note":
            shapes.append(Shape("label", x=x(e.t), y=lane_gap * 0.8,
                                text=e.detail["text"], t_in=e.t))

    # Fit last, once every shape is placed: a layout only manages the axis
    # it was written for, and this catches whatever is left over.
    return Frame(shapes, duration=trace.duration, title=title).fit()


def dataflow(trace: Trace, *, title: str = "", col_gap: float = 3.2,
             row_gap: float = 2.4, max_width: float = 12.5,
             max_per_row: int = 8) -> Frame:
    """
    Machines as boxes grouped by role, items as chips that move between them.

    The MapReduce/Spark view: mappers on one row, reducers on the next, and
    every pair visibly crossing the gap during the shuffle.
    """
    spawns = [e for e in trace.of_kind("spawn")]
    roles: dict[str, list[str]] = {}
    for e in spawns:
        roles.setdefault(e.detail.get("role") or "node", []).append(e.machine)

    # How much each machine ever holds at once decides how tall its box has to
    # be. Measured up front: a box drawn too small is what made the contents
    # overlap, and that cannot be repaired once the row spacing is fixed.
    most_held: dict[str, int] = {}
    for e in trace.of_kind("hold"):
        n = len(e.detail.get("items", [])[:6])
        most_held[e.machine] = max(most_held.get(e.machine, 0), n)
    # A machine also fills up with what arrives during a shuffle, and those
    # chips used to be stacked from the box centre by the browser with no idea
    # where the edge was — twelve of them walked straight out of the bottom.
    receives: dict[str, int] = {}
    for e in trace.of_kind("send"):
        to = e.detail.get("to")
        if to:
            receives[to] = receives.get(to, 0) + 1

    def capacity(name: str) -> int:
        return max(most_held.get(name, 0), min(receives.get(name, 0), 6))

    tallest = box_height(max((capacity(n) for n in
                              set(most_held) | set(receives)), default=0))
    row_gap = max(row_gap, tallest + 0.9)

    pos: dict[str, tuple[float, float]] = {}
    size: dict[str, tuple[float, float]] = {}     # the box actually drawn
    shapes: list[Shape] = []

    # A role with many machines wraps onto several sub-rows, and everything
    # shrinks to fit the frame. Without this a cluster of 20 nodes would run
    # straight off the side of the screen.
    row_index = 0
    for role, members in roles.items():
        chunks = [members[i:i + max_per_row]
                  for i in range(0, len(members), max_per_row)] or [[]]
        for chunk in chunks:
            span = max(len(chunk) - 1, 1) * col_gap
            scale = min(1.0, max_width / span) if span else 1.0
            gap = col_gap * scale
            for col, name in enumerate(chunk):
                px = (col - (len(chunk) - 1) / 2) * gap
                py = -row_index * row_gap
                pos[name] = (px, py)
                spawn = next(e for e in spawns if e.machine == name)
                slow = (spawn.detail.get("speed") or 1.0) < 1.0
                box_w = min(2.6, gap * 0.85)
                box_h = box_height(capacity(name))
                size[name] = (box_w, box_h)
                shapes.append(Shape("box", x=px, y=py, w=box_w, h=box_h,
                                    text=name,
                                    color="#E8B44C" if slow else "#9AA0A6",
                                    meta={"role": role, **spawn.detail}))
            row_index += 1

    for e in trace:
        if e.kind == "send":
            fx, fy = pos.get(e.machine, (0, 0))
            tx, ty = pos.get(e.detail["to"], (0, 0))
            payload = e.detail["payload"]
            key = payload[0] if isinstance(payload, tuple) else payload
            vis = default_visual(payload)
            shapes.append(Shape("chip", x=fx, y=fy, x2=tx, y2=ty,
                                text=vis.text,
                                color=color_for(vis.color_key or key), t_in=e.t,
                                t_out=e.detail["arrive"],
                                meta={"detail": vis.detail, "kind": vis.kind,
                                      "from": e.machine, "to": e.detail["to"],
                                      "in_flight": True,
                                      # Where the first arrival sits, and how
                                      # far apart the rest may be stacked.
                                      "land_top": held_positions(
                                          ty, 1,
                                          box_h=box_height(
                                              capacity(e.detail["to"])))[0],
                                      "land_step": CHIP_H + CHIP_GAP,
                                      "land_room": box_height(
                                          capacity(e.detail["to"]))
                                      - LABEL_STRIP - CHIP_H}))
        elif e.kind == "rpc":
            # A call is a round trip, so it is two arrows: the request out and
            # the answer back. One line drawn centre-to-centre could show
            # neither direction — it passed through both boxes and buried its
            # arrowhead in the target — and a reply that failed looked exactly
            # like one that arrived.
            frm, to = e.machine, e.detail["to"]
            a, b = pos.get(frm, (0, 0)), pos.get(to, (0, 0))
            box_a = size.get(frm, (2.6, box_height(capacity(frm))))
            box_b = size.get(to, (2.6, box_height(capacity(to))))
            status = e.detail.get("status", "ok")
            method = e.detail["method"]
            started = e.detail.get("started", e.t)
            attempts = e.detail.get("attempts", 1)

            x1, y1, x2, y2 = edge_points(a, b, box_a=box_a, box_b=box_b,
                                         offset=ARROW_OFFSET)
            shapes.append(Shape("arrow", x=x1, y=y1, x2=x2, y2=y2,
                                text=f"{method}()"
                                     + (f" ×{attempts}" if attempts > 1 else ""),
                                color=color_for(method),
                                t_in=started, t_out=e.t,
                                meta={"status": status, "attempts": attempts,
                                      "method": method, "leg": "request"}))

            # The answer only exists when the call actually got one. A crashed
            # server or a blown deadline produces no return arrow, so the
            # picture shows what did not come back.
            if status == "ok":
                rx1, ry1, rx2, ry2 = edge_points(b, a, box_a=box_b, box_b=box_a,
                                                 offset=ARROW_OFFSET)
                reply = e.detail.get("reply")
                shapes.append(Shape("arrow", x=rx1, y=ry1, x2=rx2, y2=ry2,
                                    text=(payload_label(reply) if reply is not None
                                          else "reply"),
                                    color=STATUS_COLORS["ok"],
                                    t_in=e.t, t_out=None,
                                    meta={"status": status, "method": method,
                                          "leg": "reply", "value": reply}))
            else:
                # Say what went wrong where the reply would have been.
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                shapes.append(Shape("label", x=mx, y=my, text=status,
                                    color=STATUS_COLORS.get(status, "#E05252"),
                                    t_in=e.t,
                                    meta={"method": method, "leg": "failed"}))
        elif e.kind == "hold":
            px, py = pos.get(e.machine, (0, 0))
            items = e.detail.get("items", [])[:6]
            ys = held_positions(py, len(items),
                                box_h=box_height(capacity(e.machine)))
            for i, item in enumerate(items):
                vis = default_visual(item)
                shapes.append(Shape("chip", x=px, y=ys[i], h=CHIP_H,
                                    text=vis.text,
                                    color=color_for(vis.color_key or vis.text),
                                    t_in=e.t,
                                    meta={"detail": vis.detail, "kind": vis.kind,
                                          "held_by": e.machine}))
        elif e.kind == "input":
            px, py = pos.get(e.machine, (0, 0))
            vis = default_visual(e.detail.get("value"))
            top = box_height(capacity(e.machine)) / 2
            shapes.append(Shape("chip", x=px, y=py + top + CHIP_H,
                                h=CHIP_H, text=vis.text,
                                color=color_for(vis.color_key or vis.text),
                                t_in=e.t,
                                meta={"detail": vis.detail, "kind": vis.kind,
                                      "role": "input"}))
        elif e.kind == "output":
            px, py = pos.get(e.machine, (0, 0))
            shapes.append(Shape("chip", x=px, y=py - 1.2,
                                text=f"({e.detail['key']}, {e.detail['value']})",
                                color=color_for(e.detail["key"]), t_in=e.t))
        elif e.kind == "crash":
            px, py = pos.get(e.machine, (0, 0))
            shapes.append(Shape("marker", x=px, y=py, text="✕",
                                color="#E05252", t_in=e.t))
        elif e.kind == "note":
            shapes.append(Shape("label", x=0, y=row_gap * 0.9,
                                text=e.detail["text"], t_in=e.t))

    # Fit last, once every shape is placed: a layout only manages the axis
    # it was written for, and this catches whatever is left over.
    return Frame(shapes, duration=trace.duration, title=title).fit()


def gantt(trace: Trace, *, title: str = "", row_gap: float = 0.9,
          seconds_per_unit: float = 1.2, max_height: float = 6.0,
          max_width: float = 10.0) -> Frame:
    """
    One row per machine, one bar per unit of work.

    This is the straggler picture: a slow machine's bar visibly runs past
    everyone else's, which is why job completion time is not average task time.
    """
    names = trace.machines()
    # Fit any number of machines: rows compress, and the time axis is scaled
    # so the longest run still lands inside the frame.
    if len(names) > 1:
        row_gap = min(row_gap, max_height / (len(names) - 1))
    if trace.duration * seconds_per_unit > max_width:
        seconds_per_unit = max_width / max(trace.duration, 1e-9)
    row = {n: -i * row_gap + (len(names) - 1) * row_gap / 2
           for i, n in enumerate(names)}

    # Bars are laid out from a left margin so the row labels sit clear of them,
    # and each bar is anchored by its left edge rather than its centre —
    # otherwise a long bar would extend backwards through its own start time.
    margin = 1.6
    shapes = [Shape("label", x=-margin - 1.4, y=row[n], text=n, color=color_for(n))
              for n in names]

    for e in trace.of_kind("work"):
        dur = e.detail.get("duration", e.detail["until"] - e.t)
        w = max(dur * seconds_per_unit, 0.05)
        shapes.append(Shape("box", x=-margin + e.t * seconds_per_unit + w / 2,
                            y=row[e.machine],
                            w=w, h=row_gap * 0.6,
                            text=e.detail.get("label", ""),
                            color=color_for(e.machine), t_in=e.t,
                            meta={"duration": dur}))
    for e in trace.of_kind("crash"):
        shapes.append(Shape("marker", x=-margin + e.t * seconds_per_unit,
                            y=row[e.machine], text="✕", color="#E05252", t_in=e.t))

    # Fit last, once every shape is placed: a layout only manages the axis
    # it was written for, and this catches whatever is left over.
    return Frame(shapes, duration=trace.duration, title=title).fit()


def lineage(trace: Trace, *, title: str = "", col_gap: float = 3.6,
            row_gap: float = 1.15, box_w: float = 2.6, box_h: float = 0.72
            ) -> Frame:
    """
    The RDD graph, laid out in stages.

    This is the picture Spark is about and the one nothing else here drew:
    `dataflow` draws machines and the messages between them, which answers
    "who talked to whom" rather than "what was built from what". A student
    asked to say which line caused a barrier has to be able to see the
    barrier, and it is between two columns of this diagram.

    One column per stage, left to right, each column stacked top-down in the
    order the steps were built. Edges are routed by what they are: a step and
    its parent in the same stage are joined vertically, because nothing moved;
    an edge that crosses a column is a shuffle, drawn across and coloured,
    because that is the expensive one and the whole reason to look.
    """
    steps = [e for e in trace if e.kind == "rdd"]
    if not steps:
        return Frame([], duration=0.0, title=title)

    by_stage: dict = {}
    for e in steps:
        by_stage.setdefault(e.detail.get("stage", 0), []).append(e)

    stages = sorted(by_stage)
    tallest = max(len(members) for members in by_stage.values())
    # Every column hangs from the same line, so the stage labels agree and a
    # short column does not float in the middle of a tall one.
    ceiling = (tallest - 1) * row_gap / 2

    shapes, placed, rows_at = [], {}, {}
    for column, stage in enumerate(stages):
        x = column * col_gap
        for row, e in enumerate(by_stage[stage]):
            y = ceiling - row * row_gap
            name = e.detail.get("name", "")
            placed[name] = (x, y)
            rows_at[name] = row
            wide = e.detail.get("wide")
            shapes.append(Shape(
                kind="box", x=x, y=y, w=box_w, h=box_h,
                text=f"{name}\n{e.detail.get('op', '')} · "
                     f"{e.detail.get('records', 0)} rec",
                color="#E8710A" if wide else "#4285F4",
                meta={"stage": stage, "wide": bool(wide),
                      "records": e.detail.get("records", 0)}))
        shapes.append(Shape(
            kind="label", x=x, y=ceiling + row_gap * 0.85, w=box_w, h=0.4,
            text=f"stage {stage + 1}", color="#9AA0A6",
            meta={"stage": stage}))

    for e in steps:
        name = e.detail.get("name", "")
        if name not in placed:
            continue
        x2, y2 = placed[name]
        for parent in e.detail.get("parents", []):
            if parent not in placed:
                continue
            x1, y1 = placed[parent]
            if abs(x1 - x2) < 1e-6:
                # Same stage: nothing moved, so the edge stays in the column.
                # An edge that skips a row is routed beside the column rather
                # than through it — drawn straight, it crossed the box in
                # between and read as an edge to that box instead.
                skips = abs(rows_at.get(name, 0) - rows_at.get(parent, 0)) > 1
                rail = x1 - box_w / 2 - 0.35 if skips else x1
                shapes.append(Shape(
                    kind="arrow", x=rail, y=y1 - box_h / 2,
                    x2=rail if skips else x2, y2=y2 + box_h / 2,
                    color="#9AA0A6", meta={"shuffle": False, "skips": skips}))
            else:
                shapes.append(Shape(
                    kind="arrow", x=x1 + box_w / 2, y=y1,
                    x2=x2 - box_w / 2, y2=y2, text="shuffle",
                    color="#E8710A", meta={"shuffle": True}))

    return Frame(shapes, duration=0.0, title=title)
