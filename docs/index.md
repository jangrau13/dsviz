---
title: dsviz
description: How to write a program in this language.
---

# dsviz

This is the reference for the language: how to write a program, and
what each part of one means. What to build is in the task open in
the editor, not here.

A program has three parts.

```python
@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120

bank = Ledger(speed=1.0)

world = World(machines=[bank])

def story() -> void:
    owed: int = bank.balance("savings")

job = Calls(run=story)
world.run(job)
```

**The machines** are decorated classes. A class is a *kind* of machine;
what runs is an instance you make from it, carrying its own speed and
failure behaviour.

**The world** says which machines exist together. Nothing runs outside
one, because a machine on its own is a description rather than a
system.

**The job** is the computation, handed to the world to run. The same job
can be run in a fast world and a broken one, which is the comparison
most of the course is about.

Everything else is a variation on those three: more machines, a
different job, a world that breaks.
