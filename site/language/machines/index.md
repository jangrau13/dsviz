# Machines

Declaring a kind of machine, and making ones that exist.

### \`@kind

class Name:\`

A kind of machine.

The decorator says what the class is to the simulator. @machine both answers calls and makes them, @mapper and @reducer are the two halves of a job, and @process carries a clock. A class is only a kind of machine. What runs is an instance of it.

```
@machine
class Ledger:
    @duration(0.4)
    def balance(account: string) -> int:
        return 120
```

### `name = Kind(speed=N)`

A machine that exists.

Declaring a class runs nothing. Each instance carries its own settings, so two machines of one kind can differ. That is how you make a straggler.

```
fast = Worker(speed=1.0)
slow = Worker(speed=0.3)
```

### `field: type = value`

What a machine remembers.

Written in the class body, above the methods: a name, its type, and the value it starts at. A machine without state answers the same thing however often it is asked; with it, the second call can see what the first one did. Every machine of that kind has its own — two of them never share a value — and an instance may start somewhere else by naming the field when it is made. It is drawn along the bottom of the machine on the diagram, and it changes there as the run goes on.

```
@machine
class Ledger:
    balance: int = 120

    @duration(0.4)
    def deposit(amount: int) -> int:
        balance: int = balance + amount
        return balance
```

### `field: type = expression`

Change what a machine remembers.

Inside a method the field is an ordinary name: it reads as what the machine currently holds, and writing to it changes the machine rather than a local that is thrown away when the call returns. Which of the two you get is decided by the class declaration and nothing else, so a parameter may not carry a field's name.

```
@duration(0.4)
def deposit(amount: int) -> int:
    balance: int = balance + amount
    return balance
```

### `name = Kind(field=value)`

Start one machine somewhere else.

The class says which fields exist, because that is what makes it this kind of machine; each instance may say what its own start at. That is how two machines of one kind differ in what they hold rather than only in how fast they are.

```
vault = Ledger(balance=5000)
petty = Ledger(balance=40)
```

### `Kind(speed=N)`

Relative speed of one machine.

1.0 is nominal and 0.25 takes four times as long. This is how you make a straggler.

```
slow = Worker(speed=0.25)
```

### `@duration(T)`

How long a method takes.

Seconds of work at speed 1.0. A machine with speed 0.5 takes twice as long over the same method.

```
@duration(0.4)
def balance(account: string) -> int:
    return 120
```

### `Kind(error_rate=P)`

How likely a machine is to break.

0.25 means each piece of work it does has one chance in four of breaking it. The draw is random, so no two runs are alike and one run never settles a question. Every machine runs this risk: a mapper grinding through its split is as exposed as a service answering a request. What happens after it breaks is said separately, with on_crash.

```
flaky = Ledger(speed=1.0, error_rate=0.25)
```

### `Kind(on_crash="stay_dead" | "restart")`

What this machine does after it breaks.

How often a machine breaks is only half of how it behaves. A machine that stays down and one that is back in two seconds fail at the same rate and behave nothing alike, because retries help the second and are wasted on the first. The default is "stay_dead", which leaves it down until something restarts it by hand. Coming back does not bring back what it was holding, so a restarted mapper runs its splits again.

```
flaky = Ledger(error_rate=0.25, on_crash="restart", restart_after=1.5)
```

### `Kind(restart_after=T)`

How long a restarting machine is down.

Seconds between breaking and answering again. It means something only alongside on_crash="restart". The gap shows on the timeline as the machine sitting idle, and that idle time is what you weigh against losing the work outright.

```
slow_to_recover = Worker(error_rate=0.2, on_crash="restart", restart_after=3.0)
```

### `machine.crash()`

Take a machine down.

Everything it remembers goes back to the value it started at, whatever it had been counted up to since, and messages already in flight to it are dropped. On the diagram the machine's own values drop back at the moment it breaks, which is the cost of losing it.

```
bank.crash()
```

### `machine.restart()`

Bring a machine back.

It comes back as it was declared, not as it was a moment before it broke, so anything it had worked out has to be worked out again.

```
bank.restart()
```

### `machine.method(arg [, deadline=T] [, retries=N])`

Make a synchronous call.

The caller waits for the round trip, so a slow server shows up as caller idle time. Statuses follow gRPC: ok, unavailable, unimplemented, deadline_exceeded.

```
chf: int = bank.balance("savings", deadline=0.5, retries=2)
```
