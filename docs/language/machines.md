---
title: Machines
description: Declaring a kind of machine, and making ones that exist.
---

# Machines

Declaring a kind of machine, and making ones that exist.

### `@kind
class Name:`

A kind of machine.

The decorator says what the class is to the simulator. @machine both answers calls and makes them, @mapper and @reducer are the two halves of a job, and @process carries a clock. A class is only a kind of machine. What runs is an instance of it.

```python
@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120
```

### `name = Kind(speed=N)`

A machine that exists.

Declaring a class runs nothing. Each instance carries its own settings, so two machines of one kind can differ. That is how you make a straggler.

```python
fast = Worker(speed=1.0)
slow = Worker(speed=0.3)
```

### `Kind(speed=N)`

Relative speed of one machine.

1.0 is nominal and 0.25 takes four times as long. This is how you make a straggler.

```python
slow = Worker(speed=0.25)
```

### `@duration(T)`

How long a method takes.

Seconds of work at speed 1.0. A machine with speed 0.5 takes twice as long over the same method.

```python
@duration(0.4)
def balance(account: string) -> int:
    return 120
```

### `Kind(error_rate=P)`

How likely a machine is to break.

0.25 means each piece of work it does has one chance in four of breaking it. The draw is random, so no two runs are alike and one run never settles a question. Every machine runs this risk: a mapper grinding through its split is as exposed as a service answering a request. What happens after it breaks is said separately, with on_crash.

```python
flaky = Ledger(speed=1.0, error_rate=0.25)
```

### `Kind(on_crash="stay_dead" | "restart")`

What this machine does after it breaks.

How often a machine breaks is only half of how it behaves. A machine that stays down and one that is back in two seconds fail at the same rate and behave nothing alike, because retries help the second and are wasted on the first. The default is "stay_dead", which leaves it down until something restarts it by hand. Coming back does not bring back what it was holding, so a restarted mapper runs its splits again.

```python
flaky = Ledger(error_rate=0.25, on_crash="restart", restart_after=1.5)
```

### `Kind(restart_after=T)`

How long a restarting machine is down.

Seconds between breaking and answering again. It means something only alongside on_crash="restart". The gap shows on the timeline as the machine sitting idle, and that idle time is what you weigh against losing the work outright.

```python
slow_to_recover = Worker(error_rate=0.2, on_crash="restart", restart_after=3.0)
```

### `machine.crash()`

Take a machine down.

The machine loses its in-memory state, and messages already in flight to it are dropped.

```python
bank.crash()
```

### `machine.restart()`

Bring a machine back.

It comes back with no state, so anything it held has to be recomputed.

```python
bank.restart()
```

### `machine.method(arg [, deadline=T] [, retries=N])`

Make a synchronous call.

The caller waits for the round trip, so a slow server shows up as caller idle time. Statuses follow gRPC: ok, unavailable, unimplemented, deadline_exceeded.

```python
chf: int = bank.balance("savings", deadline=0.5, retries=2)
```
