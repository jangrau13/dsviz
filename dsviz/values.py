"""
Values that know how to draw themselves.

A payload in the simulation is not just data — it is something the student
should be able to *see*. Every value carries a `to_manim()` describing how it
should appear, so the picture follows the program: change what a mapper emits
and the chip in the diagram changes with it.

`to_manim()` has a sensible default for every built-in type, and is
overrideable — define it on your own value type and both the video and the
browser pick it up, because both render from the same description.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Visual:
    """
    How one value should be drawn. Renderer-agnostic on purpose: Manim reads
    it to build mobjects, the browser reads the same fields to build SVG.
    """
    text: str                      # what is written on the chip
    kind: str = "chip"             # chip | card | stack | badge
    color_key: str = ""            # what to colour by; "" means the text
    detail: str = ""               # shown on hover, or under the label
    parts: list = field(default_factory=list)   # for stacks: nested Visuals

    def to_json(self) -> dict:
        return {"text": self.text, "kind": self.kind,
                "color_key": self.color_key, "detail": self.detail,
                "parts": [p.to_json() for p in self.parts]}


class Drawable:
    """
    Mix in to give a value its own appearance.

        class Sensor(Drawable):
            def to_manim(self):
                return Visual(f"{self.id}: {self.reading}°", kind="badge")

    Anything that defines `to_manim` is used as-is; everything else falls back
    to `default_visual` below.
    """

    def to_manim(self) -> Visual:      # pragma: no cover - overridden
        raise NotImplementedError


def default_visual(value: Any, *, limit: int = 24) -> Visual:
    """
    The built-in appearance for any value.

    Chosen so the common cases read the way a student thinks about them: a
    (key, value) pair is `key: value`, a document is its text, a list is its
    first few items with a count.
    """
    # A value that defines its own appearance always wins.
    hook = getattr(value, "to_manim", None)
    if callable(hook):
        got = hook()
        return got if isinstance(got, Visual) else Visual(str(got))

    if isinstance(value, tuple) and len(value) == 2:
        key, val = value
        return Visual(_clip(f"{key}: {val}", limit), color_key=str(key),
                      detail=f"pair — key {key!r}, value {val!r}")

    if isinstance(value, dict):
        body = ", ".join(f"{k}={v}" for k, v in list(value.items())[:3])
        more = "" if len(value) <= 3 else f" +{len(value) - 3}"
        return Visual(_clip(body + more, limit), kind="card",
                      detail=f"{len(value)} field(s)")

    if isinstance(value, (list, tuple)):
        head = ", ".join(str(v) for v in value[:3])
        more = "" if len(value) <= 3 else f" +{len(value) - 3}"
        return Visual(_clip(f"[{head}{more}]", limit), kind="stack",
                      detail=f"{len(value)} item(s)",
                      parts=[default_visual(v) for v in value[:6]])

    if isinstance(value, str):
        # A document split reads better as its own text than as a quoted blob.
        return Visual(_clip(value, limit), kind="card",
                      detail=value if len(value) > limit else "")

    return Visual(_clip(str(value), limit))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- named values -------------------------------------------------------
# The simulation uses these so a chip can say what it *is*, not merely what it
# prints as.

class Pair(tuple, Drawable):
    """An intermediate (key, value) pair."""

    def __new__(cls, key, value):
        return super().__new__(cls, (key, value))

    @property
    def key(self):
        return self[0]

    @property
    def value(self):
        return self[1]

    def to_manim(self) -> Visual:
        return Visual(_clip(f"{self[0]}: {self[1]}", 24), color_key=str(self[0]),
                      detail=f"emitted pair — key {self[0]!r}, value {self[1]!r}")


class Split(str, Drawable):
    """One unit of input, with the name it was declared under."""

    def __new__(cls, name: str, text: str):
        obj = super().__new__(cls, text)
        obj.name = name
        return obj

    def to_manim(self) -> Visual:
        words = len(self.split())
        return Visual(_clip(str(self), 26), kind="card", color_key=self.name,
                      detail=f"{self.name} — {words} word(s): {self}")


class Partition(list, Drawable):
    """Everything destined for one reducer."""

    def __init__(self, index: int, items=()):
        super().__init__(items)
        self.index = index

    def to_manim(self) -> Visual:
        keys = sorted({p[0] for p in self if isinstance(p, tuple)})
        return Visual(f"partition {self.index} ({len(self)})", kind="stack",
                      color_key=f"p{self.index}",
                      detail=f"{len(self)} pair(s), keys: {', '.join(keys[:6])}",
                      parts=[default_visual(p) for p in self[:8]])
