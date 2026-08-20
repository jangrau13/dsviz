---
title: Machines you can buy
description: The catalogue, and what each type comes with.
---

# Machines you can buy

A machine is not built to order. There is a catalogue, you pick a
type off it, and the machine arrives with the processor and the room
that type comes with — so making something faster is a purchase
rather than a number typed into the program.

| Type | Processor | Room | For |
|---|---|---|---|
| `t1.small` | 0.3x | 8 items | cheap and slow — a straggler, and what a job looks like when it is run on whatever was lying around |
| `m1.small` | 1x | 16 items | the ordinary machine, and what to reach for first |
| `m1.large` | 2x | 32 items | twice the processor and twice the room |
| `c1.large` | 4x | 16 items | four times the processor, ordinary room — for work that is slow rather than large |
| `r1.large` | 1x | 96 items | ordinary processor, six times the room — for a partition that will not fit |

The letter says what the machine is built for. `c` has the
processor, for work that is slow because there is a lot of it to do.
`r` has the room, for a machine handed more than it can hold — that
one does not get better on a quicker processor. `m` is the middle of
both, and `t` is the cheap one, which is how you are given a
straggler.

```python
@machine
class Worker:
    pass

fast = Worker(type="c1.large")
roomy = Worker(type="r1.large")
ordinary = Worker()          # m1.small, if you say nothing
```

Each type is drawn in its own colour, so a fleet of mixed machines
reads as mixed at a glance.

A job that will not fit on one machine can be moved to a bigger one,
or made smaller before it is sent. Both are answers. Which is
cheaper is the question the catalogue exists to ask, and the panel
under the diagram is where the answer shows.
