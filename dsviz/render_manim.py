"""
Manim renderer: shapes -> video.

Consumes a `Frame` from `shapes.py`, so it never touches the simulator. The
browser renders the same Frame to SVG, which is what keeps the lecture video
and the student's editor showing the same picture.

    manim -pql -  # see examples/render_*.py
"""

from __future__ import annotations

from manim import *

from .shapes import Frame


class FrameScene(Scene):
    """
    Renders a Frame. Subclass and set `frame`, or pass one via `with_frame`.

        class MyScene(FrameScene):
            frame = dataflow(trace, title="MapReduce")
    """

    frame: Frame | None = None

    @classmethod
    def with_frame(cls, frame: Frame, name: str = "RenderedScene",
                   module: str | None = None):
        """Build a Scene subclass bound to `frame`, for `manim` to discover.

        Manim only picks up scenes whose `__module__` is the file it was given,
        so the generated class is stamped with the caller's module.
        """
        if module is None:
            import inspect
            caller = inspect.stack()[1].frame
            module = caller.f_globals.get("__name__", cls.__module__)
        return type(name, (cls,), {"frame": frame, "__module__": module})

    def construct(self):
        f = self.frame
        if f is None:
            raise ValueError("FrameScene needs a frame — use with_frame(...)")

        # Whatever is off-camera might as well not have been drawn, so fit the
        # scene to the frame before rendering a second of it. What fitting
        # cannot fix — too many machines to label, too many items to follow —
        # is said out loud rather than quietly rendered.
        f.fit()
        for warning in f.warnings():
            print(f"dsviz: {warning}")

        if f.title:
            t = Text(f.title, font_size=44, weight=BOLD)
            self.play(Write(t))
            self.wait(0.6)
            self.play(FadeOut(t))

        static, timed = [], []
        for s in f:
            (timed if s.t_in > 0 else static).append(s)

        built = {id(s): self._build(s) for s in f}

        # Everything present at t=0 appears together.
        base = VGroup(*[built[id(s)] for s in static if built[id(s)]])
        if len(base):
            self.play(LaggedStart(*[FadeIn(m) for m in base], lag_ratio=0.08))

        # Then the timed shapes, grouped by instant so simultaneous things
        # animate together rather than queueing up.
        for t in sorted({s.t_in for s in timed}):
            group = [built[id(s)] for s in timed
                     if s.t_in == t and built[id(s)] is not None]
            if not group:
                continue
            self.play(LaggedStart(*[FadeIn(m, shift=UP * 0.15) for m in group],
                                  lag_ratio=0.12), run_time=0.7)

        self.wait(1.5)

    # -- shape -> mobject ---------------------------------------------
    def _build(self, s):
        if s.kind == "box":
            box = Rectangle(width=max(s.w, 0.3), height=max(s.h, 0.3),
                            stroke_color=s.color, stroke_width=2,
                            fill_color=s.color, fill_opacity=0.12)
            box.move_to([s.x, s.y, 0])
            if s.text:
                # Fit the label to the box it sits in. A scene that has been
                # scaled down to fit the frame hands us smaller boxes, and a
                # fixed font size would then spill out of every one of them —
                # making a crowded diagram unreadable rather than merely small.
                label = Text(s.text, font_size=18)
                room = max(s.w - 0.2, 0.1)
                if label.width > room:
                    label.scale(room / label.width)
                label.move_to(box)
                return VGroup(box, label)
            return box

        if s.kind == "chip":
            label = Text(s.text, font_size=18, font="Menlo")
            bg = RoundedRectangle(corner_radius=0.08,
                                  width=label.width + 0.25,
                                  height=label.height + 0.18,
                                  stroke_color=s.color, stroke_width=2,
                                  fill_color=s.color, fill_opacity=0.18)
            return VGroup(bg, label).scale(0.55).move_to([s.x, s.y, 0])

        if s.kind == "lane":
            line = Line([s.x, s.y, 0], [s.x2, s.y2, 0],
                        stroke_color=GREY_B, stroke_width=2)
            tag = Text(s.text, font_size=20, color=s.color)
            tag.next_to(line, LEFT, buff=0.2)
            return VGroup(line, tag)

        if s.kind == "arrow":
            arrow = Arrow([s.x, s.y, 0], [s.x2, s.y2, 0], buff=0.05,
                          stroke_width=2.5, color=s.color,
                          max_tip_length_to_length_ratio=0.12)
            if s.text:
                tag = Text(s.text, font_size=14, color=GREY_B)
                tag.next_to(arrow.get_center(), UP, buff=0.08)
                return VGroup(arrow, tag)
            return arrow

        if s.kind == "marker":
            return Text(s.text, font_size=16, color=s.color).move_to([s.x, s.y, 0])

        if s.kind == "label":
            return Text(s.text, font_size=22, color=GREY_B).move_to([s.x, s.y, 0])

        return None
