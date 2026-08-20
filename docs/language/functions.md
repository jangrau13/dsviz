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

### `[element for name: type in list]`

One element out for each element in.

How a function that has to produce many things produces them. A map is handed one record and answers with every pair it made from it, which may be none, one, or thousands — so it answers with a list, and this is how that list is built.

Read it right to left: take each `reading` out of `split(payload)`, and for each one put a pair into the list. The loop variable carries its type for the same reason it does in a `for` statement: nothing here is inferred.

```python
def perStation(station: string, payload: string) -> [pair]:
    return [(station, reading) for reading: string in split(payload)]
```

### `(key, value)`

The two halves of one intermediate result.

What a map answers with a list of. The key decides which partition it goes to, and the value is whatever the reducer takes — a count, a document name, anything the job is about. A list of them is written `[pair]`, which is what a map declares as its return type.

```python
(station, reading)
```

### `with parallel():`

Calls that all leave at the same time.

Calls are one after another: each moves the caller past the whole round trip, so the next one leaves from where the last finished. Everything written inside this block leaves at the moment the block begins instead, and the block ends when the last reply is back — so asking three machines costs one round trip rather than three. The far end is unchanged, which is the half worth watching: a machine answers one request at a time, so three calls sent at once to the same machine still queue behind each other. What the block saves is the waiting on the wire, never the work — it pays because the machines are different, not because the calls were written together. No call in the block can be given what another call in it answers, because none of them has answered yet; use the value below the block.

```python
def story() -> void:
    with parallel():
        here: int = bank.balance("savings")
        there: int = mirror.balance("savings")
```
