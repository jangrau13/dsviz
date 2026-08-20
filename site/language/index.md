# Writing a program

One statement per line. `#` starts a comment. Indentation delimits the body of a function, a loop, a conditional or a parallel block, as in Python.

Every name carries a written type. Nothing is inferred: a parameter, a local, a loop variable and a return type are all stated, and the checker holds the program to what was written. That is what lets a job verify a function fits the position it was passed to.

```
def hottest(city: string, readings: [int]) -> int:
    top: int = 0
    for reading: int in readings:
        if reading > top:
            top: int = reading
    return top
```

Names are yours. A function fits a position because its types fit, and you say which function goes where when you build the job. What you called it never enters into it.

## Types

| Type       | Is                                          |
| ---------- | ------------------------------------------- |
| `int`      | a whole number                              |
| `string`   | text                                        |
| `[int]`    | a list of numbers                           |
| `[string]` | a list of text                              |
| `pair`     | a (key, value) pair, written `(key, value)` |
| `[pair]`   | a list of pairs — what a map answers with   |
| `void`     | nothing                                     |

`[int]` and `[string]` are deliberately distinct: the mistakes worth catching are the ones that confuse a list of counts with a list of words.

A function that produces one thing returns it. A function that produces an unknown number of them returns a list, and says so: a map is handed one record and answers `[pair]`, because how many pairs it makes is not known until it has made them. A reduce collapses many values into exactly one, so it answers with that one value.

## Several files

A program can span files. `use` brings another file's definitions into scope; files are combined in dependency order, and a mistake is reported against the file and line it is in. Circular `use` and missing files are errors rather than run-time surprises.
