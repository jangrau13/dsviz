---
title: Datasets
description: Values built from other values, and what is remembered about how.
---

# Datasets

Values built from other values, and what is remembered about how.

### `textFile(input)`

Create an RDD from input.

The start of every pipeline.

```python
rows = textFile(readings)
```

### `.flatMap(expr)`

One record in, many out.

Narrow, so it needs no shuffle and pipelines inside the current stage.

```python
rows.flatMap(split(value))
```

### `.mapToPair(key, value)`

Turn records into pairs.

Narrow. It produces a pair RDD, which is what the byKey operations need.

```python
rows.mapToPair(value, 1)
```

### `.reduceByKey(a + b)`

Combine values per key.

Wide, so it forces a shuffle and begins a new stage. It combines on the map side first, which groupByKey does not.

```python
readings.reduceByKey(a + b)
```

### `.groupByKey()`

Gather all values per key.

Wide, and it ships everything. reduceByKey moves less data for the same answer.

```python
readings.groupByKey()
```

### `.filter(expr)`

Keep records matching a condition.

Narrow.

```python
totals.filter(value > 1)
```

### `.cache()`

Keep this RDD in memory.

Without it, every action recomputes the whole lineage.

```python
totals.cache()
```

### `.collect()`

An action: bring results back.

Spark is lazy, so nothing runs until an action asks for a result.

```python
totals.collect()
```

### `lose RDD`

Lose a cached partition.

Shows recomputation from lineage, which is what Spark does and MapReduce cannot.

```python
lose totals
```
