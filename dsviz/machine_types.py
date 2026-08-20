"""
The machines you can buy, and what each one is.

A machine is not built to order. There is a catalogue, you pick a type off it,
and the machine arrives with the processor and the memory that type comes with
— so "make it faster" is a purchase rather than a number typed into a program.
Which is how a cloud works, and it is what makes speed something a design pays
for instead of something it declares about itself.

Two families, because a job can be short of two different things and the fix
is not the same:

  * `c` has the processor. Work that is slow because there is a lot of it to
    do gets faster on one of these.
  * `r` has the room. A machine that is handed more than it can hold does not
    go faster on a quicker processor — it needs somewhere to put the data.

`m` is the middle of both, and `t` is the cheap one that is slower than
everything else. A job that will not fit on an `m1.small` can be moved to an
`r1.large`, or it can be made smaller before it is sent. Both are answers, and
which is cheaper is the question the catalogue exists to ask.

The names, speeds and room are the language's. What each one *costs* is not:
that belongs to whoever is setting the exercise, because the price is what
makes the choice interesting and a different course may want it to land
differently.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MachineType:
    """One entry in the catalogue."""
    name: str
    speed: float
    capacity: int
    colour: str
    why: str

    def settings(self) -> dict:
        """What a machine of this type is, as the simulator's own arguments."""
        return {"speed": self.speed, "capacity": self.capacity,
                "type": self.name}


#: The catalogue, in the order it should be read: cheapest first, then the
#: ordinary one, then the three ways of buying more.
CATALOGUE: dict[str, MachineType] = {
    t.name: t for t in (
        MachineType("t1.small", 0.3, 8, "#E8B44C",
                    "cheap and slow — a straggler, and what a job looks like "
                    "when it is run on whatever was lying around"),
        MachineType("m1.small", 1.0, 16, "#9AA0A6",
                    "the ordinary machine, and what to reach for first"),
        MachineType("m1.large", 2.0, 32, "#7BA7D7",
                    "twice the processor and twice the room"),
        MachineType("c1.large", 4.0, 16, "#6FBF73",
                    "four times the processor, ordinary room — for work that "
                    "is slow rather than large"),
        MachineType("r1.large", 1.0, 96, "#A98BD0",
                    "ordinary processor, six times the room — for a partition "
                    "that will not fit"),
    )
}

#: What a machine is when it does not say. The ordinary one, so a program that
#: is not about money does not have to talk about money.
DEFAULT_TYPE = "m1.small"

#: What a machine of an unknown type is painted, so a program that has not
#: been checked yet still draws.
UNKNOWN_COLOUR = "#9AA0A6"


def get(name: str | None) -> MachineType:
    """The catalogue entry for `name`, falling back to the ordinary machine."""
    return CATALOGUE.get(str(name), CATALOGUE[DEFAULT_TYPE])


def colour(name: str | None) -> str:
    """The colour a machine of this type is drawn in."""
    entry = CATALOGUE.get(str(name))
    return entry.colour if entry else UNKNOWN_COLOUR


def names() -> list[str]:
    """Every type, in catalogue order."""
    return list(CATALOGUE)
