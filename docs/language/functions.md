---
title: Functions
description: What you write, and how it is written.
---

# Functions

What you write, and how it is written.

### `def name(param: type) -> type:`

A function you write.

Every parameter and the return type is written down, and nothing is inferred. Writing them is what lets a job check that a function fits the position it is passed to.

```python
def hottest(city: string, readings: [int]) -> int:
    top: int = 0
    for reading: int in readings:
        if reading > top:
            top: int = reading
    return top
```

### `emit(key, value)`

Produce one intermediate pair.

Only the function passed as the job's map may emit. The key chooses a reducer by its hash, and the value is whatever the reducer takes.

```python
emit(city, reading)
```
