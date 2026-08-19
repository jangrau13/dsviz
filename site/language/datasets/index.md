# Datasets

Values built from other values, and what is remembered about how.

### `textFile(name)`

Read input into an RDD.

The start of a pipeline. The name is an input the program declares or a file the task ships.

```
departures = textFile("departures.csv")
```

### `parallelize([...])`

Make an RDD from a list.

Useful when the data is short enough to write down.

```
stops = parallelize(["bern,4", "chur,0"])
```

### `.map(lambda x: ...)`

One record in, one out.

Narrow, so it needs no shuffle and pipelines inside the current stage.

```
rows.map(lambda row: row.split(","))
```

### `.flatMap(lambda x: [...])`

One record in, many out.

Narrow. The function returns a list, and every element of it becomes a record of its own.

```
rows.flatMap(lambda row: row.split(","))
```

### `.filter(lambda x: ...)`

Keep records that match.

Narrow, and the cheapest thing you can do before a wide step: every record it drops is one the shuffle does not carry.

```
delays.filter(lambda d: int(d) > 0)
```

### `.mapValues(lambda v: ...)`

Change values, keep keys.

Narrow. It leaves the key alone, so nothing has to move.

```
grouped.mapValues(lambda xs: sum(xs) / len(xs))
```

### `.reduceByKey(lambda a, b: ...)`

Combine values per key.

Wide: it forces a shuffle and begins a new stage. It combines on the map side first, so only one partial result per key crosses the network — which is what groupByKey does not do.

```
byStop.reduceByKey(lambda a, b: a + b)
```

### `.groupByKey()`

Gather every value per key.

Wide, and it ships every record. reduceByKey reaches the same answer while moving far less, so prefer it when you can.

```
byStop.groupByKey()
```

### `.sortByKey()`

Order the records by key.

Wide: an order across the whole RDD cannot be decided inside one partition.

```
totals.sortByKey()
```

### `.distinct()`

Drop repeated records.

Wide, because two equal records may sit on different machines.

```
stops.distinct()
```

### `.join(other)`

Match two pair RDDs on their keys.

Wide: both sides have to be brought together by key.

```
delays.join(platforms)
```

### `.partitionBy(lambda k: ...)`

Choose which partition a key goes to.

Wide. Deciding the split yourself is how you keep everything that must be compared together on one machine — and how you cause skew if the function is a poor one.

```
byStop.partitionBy(lambda k: hash(k))
```

### `.cache()`

Keep this RDD in memory.

Without it, an RDD read by two branches is computed twice: the lineage is replayed for each. That is what makes an iterative job expensive.

```
kept = rows.cache()
```

### `job = Spark(pipeline=rdd, lose=rdd)`

The job to run in the world.

`pipeline` names the last step, which is what forces the whole lineage to run. `lose` throws a step away so you can watch it rebuilt from lineage rather than reloaded.

```
job = Spark(pipeline=totals, lose=byStop)
```
