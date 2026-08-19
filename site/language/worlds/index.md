# Worlds

The machines that exist together, and running in them.

### `world = World(machines=[...])`

The system to run in.

Everything about the setting lives here and nothing about the computation, so you can run one job in a fast world and then in a broken one. Without a world, a job has nowhere to run.

```
world = World(machines=[m1, m2, r1])
```

### `world.run(job [, on=[...]])`

Run a job in the world.

`on` gives the job a subsystem instead of the whole world, so you can run one job on three machines and then on ten.

```
world.run(job)
world.run(job, on=[m1, r1])
```
