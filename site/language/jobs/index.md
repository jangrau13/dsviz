# Jobs

Handing your functions to something that runs them.

### `job = Calls(run=f)`

A job that is a sequence of calls.

The work is one function, and the job is that function run in the world. Nothing has to be wrapped in a machine to hold it. A MapReduce job has the same shape and takes three functions instead of one.

```
def story() -> void:
    chf: int = bank.balance("savings")

job = Calls(run=story)
world.run(job)
```

### `job = MapReduce(map=f, reduce=g, partition=h)`

Wire your functions into a job.

A function is the mapper because it was passed as the mapper, and it is accepted there only if its signature fits. Its name has no say in it. `combine=` adds a combiner.

```
job = MapReduce(map=readSensor, reduce=hottest, partition=spread)
```

### `Job(..., times=N)`

How many rounds the job runs.

The job runs N times over. That is what you want in a video, and it is what makes an unreliable run worth watching, because the same call does not fail every time.

```
job = Calls(run=story, times=3)
```
