---
title: Checks
description: Statements that assert something about a run.
---

# Checks

Statements that assert something about a run.

### `budget METRIC < N`

A non-functional limit.

Correctness is the floor. Budgets are what separate a good design from one that merely works. The metrics are network, makespan, imbalance, tail, memory and faults.

```python
budget network < 40
```

### `expect KEY = N`

Assert a final count.

The correctness check.

```python
expect zurich = 3
```

### `assert P.clock == [..]`

Check a claimed clock.

Write 'assert A || B' for concurrency and 'assert A ->> B' for happens-before. A wrong claim is reported causally.

```python
assert P3.clock == [2, 3, 1]
```

### `note TEXT`

A caption on the diagram.

Shown at this point in the run. Useful for narrating a video.

```python
note the shuffle starts here
```
