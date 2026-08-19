---
title: Messages
description: Processes talking to each other, and what order anyone can be sure of.
---

# Messages

Processes talking to each other, and what order anyone can be sure of.

### `sender.send(receiver, "label")`

One message, from one process to one other.

The send happens before the receive, and that is the only ordering either process can be sure of. Both carry a logical clock, which the message advances.

```python
depotA.send(depotB, "restock")
```

### `sender.broadcast("label")`

One message, to every other process.

One send, one stamp, and a copy on its way to each of the others. Delivery rules that talk about "the next message that process sent" are only defined over broadcast, because otherwise the next message may not have been addressed to you.

```python
depotA.broadcast("restock")
```

### `sender.broadcast("label", late=who)`

Send one copy the slow way.

Everyone else has the message at once; this process does not. Without it every arrival is in send order and nothing is ever out of place, so a delivery rule has nothing to do.

```python
depotA.broadcast("restock", late=depotC)
```

### `Calls(clock="vector" | "lamport")`

Which logical clock the processes keep.

A vector clock has one entry per process and can say that two events are concurrent. A Lamport clock is a single number: it guarantees that if a happened before b then L(a) < L(b), and nothing in the other direction.

```python
job = Calls(run=deliveries, clock="lamport")
```

### `Calls(delivery="causal")`

Hold a message until the ones it depends on have arrived.

Without this a message is shown when it arrives, however out of order that is. With it, one that arrives too early is held and offered again each time something is delivered. Nothing is dropped and nothing is reordered on the wire — only the moment each message is shown.

```python
job = Calls(run=deliveries, delivery="causal")
```
