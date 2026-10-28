# The Python that runs in the sandbox

Model-written code runs in [Monty](https://github.com/pydantic/monty) (pydantic-monty
1.4.0), a Python interpreter with a subset of the language and standard library. This
page lists what works, measured in the deployed runner (M3; the snippets are in
`tests/python_features.py`, and `make test-integration` checks both lists against the
live sandbox). The tool descriptions (M4) carry a short version of the "not available"
list. Decision: D-015.

## Works

**Language:** arithmetic, f-strings (with format specs), list / dict / set
comprehensions, generator expressions, lambdas and `sorted(key=...)`, tuple unpacking
(including `*rest`), the walrus operator, `match`, `try` / `except` / `finally`,
`raise`, functions with defaults, `*args` and `**kwargs`, closures and `nonlocal`,
`global`, simple classes with `__init__` and dunder methods, `@dataclass`, `with`,
`while` / `break` / `continue`, `enumerate` / `zip` / `reversed`, `min` / `max` with
`key`, `any` / `all`, string methods, slicing, dict and set methods, `isinstance` and
type conversions, type hints, `assert` (the message includes the failed expression),
`async def`, `await` (also at the top level) and `asyncio.gather`.

**Standard library:** `json`, `datetime` (`date`, `timedelta`, `strptime`,
`strftime`), `re`, `collections` (`Counter`, `defaultdict`, `namedtuple`),
`itertools` (`groupby`, `chain`, `islice`), `math`, `functools` (`reduce`,
`lru_cache`), `bisect`, `copy`, `decimal`, `typing`, `random`, `time`, `dataclasses`,
`asyncio`.

## Not available, and what to write instead

| Not available | Instead |
|---|---|
| Class inheritance, `super()` | Plain classes, or dicts |
| `@property` and other method decorators | Plain methods |
| Generators (`yield`) | Build and return a list |
| `enum` (needs inheritance) | String constants |
| `statistics` | `sum(xs) / len(xs)`; sort for the median |
| `operator.itemgetter` | `key=lambda r: r["n"]` |
| `heapq` | `sorted(xs, reverse=True)[:n]` |
| `collections.OrderedDict` | `dict` (keeps insertion order) |
| `io`, `csv` | Split lines and commas |
| `string`, `textwrap`, `fractions` | Write the characters out; slice strings |
| `urllib.parse` | Not needed: `request()` encodes parameters |

Importing a missing module is a type error before anything runs ("Cannot resolve
imported module"), or a `ModuleNotFoundError` with type checking off.
