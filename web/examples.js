/* Free-play demos for the exercises that are not graded tasks.
 *
 * The MapReduce demos are deliberately absent: they would hand a student the
 * very code Task 1 and Task 2 ask them to write. Graded tasks come from
 * `dsviz/assignment.py`, whose starters are scaffolds, not solutions. */

const EXAMPLES = {



  spark: `# A Spark pipeline. reduceByKey is WIDE — it starts a new stage.
executors 3
input lines: "the cat sat" | "the dog ran" | "the cat ran"

words  = textFile(lines).flatMap(split(value))
pairs  = words.mapToPair(value, 1)
counts = pairs.reduceByKey(a + b)
counts.cache()
counts.collect()`,

  lineage: `# Lose a cached RDD and Spark rebuilds it from lineage —
# the thing MapReduce cannot do.
executors 2
input lines: "the cat sat" | "the dog ran"

words  = textFile(lines).flatMap(split(value))
pairs  = words.mapToPair(value, 1)
counts = pairs.reduceByKey(a + b)
counts.collect()

lose counts`,

  grpc: `# A client calling map and reduce services.
service MrMapServer: Map takes 0.5
service MrReduceServer: Reduce takes 0.4

client MrClient

MrClient calls MrMapServer.Map with chunk001.txt
MrClient calls MrMapServer.Map with chunk002.txt
MrClient calls MrReduceServer.Reduce with partition0`,

  failure: `# The map server dies. The client retries, then it comes back.
service MrMapServer: Map takes 0.5
client MrClient

MrClient calls MrMapServer.Map with chunk001.txt
MrMapServer crashes
MrClient calls MrMapServer.Map with chunk002.txt retries 2
MrMapServer restarts
MrClient calls MrMapServer.Map with chunk002.txt`,

  clocks: `# Vector clocks. The assertion is checked causally:
# claim something a process cannot know and it says why.
process P1, P2, P3

P1: event a
P1 -> P2: m1
P2: event b
P2 -> P3: m2`,
};
