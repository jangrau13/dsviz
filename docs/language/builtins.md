---
title: Built-in functions
description: The small library every program can call.
---

# Built-in functions

Deliberately general: `split` and `lower` are string operations and
`sum` is arithmetic. Nothing here solves part of a task, because
anything problem-shaped is a function you write.

| Function | Type |
|---|---|
| `abs` | `abs(n: int) -> int` |
| `hash` | `hash(key) -> int, a stable 31-hash` |
| `join` | `join(values: [string], separator: string) -> string` |
| `len` | `len(x) -> int` |
| `lower` | `lower(text: string) -> string` |
| `max` | `max(values: [int]) -> int` |
| `min` | `min(values: [int]) -> int` |
| `sort` | `sort(values: [string]) -> [string]` |
| `split` | `split(text: string) -> [string]` |
| `strip` | `strip(text: string) -> string` |
| `sum` | `sum(values: [int]) -> int` |
| `unique` | `unique(values: [string]) -> [string]` |
| `upper` | `upper(text: string) -> string` |
