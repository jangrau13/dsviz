# Checks

Statements that assert something about a run.

### `expect KEY = N`

Assert a final count.

The correctness check.

```
expect zurich = 3
```

### `assert P.clock == [..]`

Check a claimed clock.

Write 'assert A || B' for concurrency and 'assert A ->> B' for happens-before. A wrong claim is reported causally.

```
assert P3.clock == [2, 3, 1]
```

### `note TEXT`

A caption on the diagram.

Shown at this point in the run. Useful for narrating a video.

```
note the shuffle starts here
```
