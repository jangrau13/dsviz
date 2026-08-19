/* Free-play demos for the exercises that are not graded tasks.
 *
 * The MapReduce demos are deliberately absent: they would hand a student the
 * very code Task 1 and Task 2 ask them to write. Graded tasks come from
 * `dsviz/assignment.py`, whose starters are scaffolds, not solutions. */

const EXAMPLES = {



  spark: `# A Spark pipeline. reduceByKey is WIDE — it starts a new stage.
# The functions are real PySpark lambdas; the executors you declare are
# the ones it runs on, so there is no SparkContext to write.
@machine
class Executor:
    pass

e1 = Executor(speed=1.0)
e2 = Executor(speed=1.0)
e3 = Executor(speed=0.5)

world = World(machines=[e1, e2, e3])

departures = parallelize(["bern,4", "chur,0", "bern,7", "chur,2", "sion,9"])
fields = departures.map(lambda row: row.split(","))
late   = fields.filter(lambda f: int(f[1]) > 0)
byStop = late.map(lambda f: (f[0], int(f[1])))
worst  = byStop.reduceByKey(lambda a, b: a if a > b else b)

job = Spark(pipeline=worst)

world.run(job)`,

  lineage: `# Lose a step and Spark rebuilds it from lineage —
# the thing MapReduce cannot do.
@machine
class Executor:
    pass

e1 = Executor(speed=1.0)
e2 = Executor(speed=1.0)

world = World(machines=[e1, e2])

departures = parallelize(["bern,4", "chur,0", "bern,7", "chur,2"])
fields = departures.map(lambda row: row.split(","))
byStop = fields.map(lambda f: (f[0], int(f[1])))
total  = byStop.reduceByKey(lambda a, b: a + b)

job = Spark(pipeline=total, lose=byStop)

world.run(job)`,

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
