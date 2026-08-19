# Functions

What you write, and how it is written.

### `def name(param: type) -> type:`

A function you write.

Every parameter and the return type is written down, and nothing is inferred. Writing them is what lets a job check that a function fits the position it is passed to.

```
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

```
emit(city, reading)
```

### `with parallel():`

Calls that all leave at the same time.

Calls are one after another: each moves the caller past the whole round trip, so the next one leaves from where the last finished. Everything written inside this block leaves at the moment the block begins instead, and the block ends when the last reply is back — so asking three machines costs one round trip rather than three. The far end is unchanged, which is the half worth watching: a machine answers one request at a time, so three calls sent at once to the same machine still queue behind each other. What the block saves is the waiting on the wire, never the work — it pays because the machines are different, not because the calls were written together. No call in the block can be given what another call in it answers, because none of them has answered yet; use the value below the block.

```
def story() -> void:
    with parallel():
        here: int = bank.balance("savings")
        there: int = mirror.balance("savings")
```
