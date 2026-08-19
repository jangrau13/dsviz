# dsviz

This is the reference for the language: how to write a program, and what each part of one means. What to build is in the task open in the editor, not here.

A program has three parts.

```
@machine
class Ledger:
    held: int = 120

    @duration(0.4)
    def deposit(amount: int) -> int:
        held: int = held + amount
        return held

bank = Ledger(speed=1.0)

world = World(machines=[bank])

def story() -> void:
    now: int = bank.deposit(30)

job = Calls(run=story)
world.run(job)
```

**The machines** are decorated classes. A class is a *kind* of machine; what runs is an instance you make from it, carrying its own speed, its own failure behaviour, and whatever it remembers. A field written in the class body — `held: int = 120` — is state: the next call can see what the last one did, and a crash takes it away again.

**The world** says which machines exist together. Nothing runs outside one, because a machine on its own is a description rather than a system.

**The job** is the computation, handed to the world to run. The same job can be run in a fast world and a broken one, which is the comparison most of the course is about.

Everything else is a variation on those three: more machines, a different job, a world that breaks.
