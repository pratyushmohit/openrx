"""Python features model code might use, with the result each should give.

Used by tests/integration/test_python_features.py, which runs every snippet in the
deployed runner. Each entry is (name, code, expected result). Measured on
pydantic-monty 1.4.0 (M3, D-015); NOT_SUPPORTED lists what doesn't work.
"""

from textwrap import dedent


def code(text: str) -> str:
    """A snippet written as an indented block, without its common indentation."""
    return dedent(text).strip()


LANGUAGE = [
    ("arithmetic", "(7 // 2, 7 % 2, 2 ** 10, round(2 / 3, 3))", [3, 1, 1024, 0.667]),
    ("f-strings", "n = 3\nf'{n} items, {n / 4:.2f}, {n!r:>4}'", "3 items, 0.75,    3"),
    ("list comprehension", "[x * x for x in range(5) if x % 2 == 0]", [0, 4, 16]),
    ("dict comprehension", "{k: len(k) for k in ['ab', 'c']}", {"ab": 2, "c": 1}),
    ("set comprehension", "sorted({c for c in 'banana'})", ["a", "b", "n"]),
    ("generator expression", "sum(x for x in range(101))", 5050),
    (
        "lambda and sorted key",
        "sorted(['bb', 'a', 'ccc'], key=lambda s: -len(s))",
        ["ccc", "bb", "a"],
    ),
    ("tuple unpacking", "a, *rest = [1, 2, 3]\n(a, rest)", [1, [2, 3]]),
    ("walrus", "(n := 5) + n", 10),
    (
        "match statement",
        code("""
            match {'kind': 'trial', 'n': 3}:
                case {'kind': 'trial', 'n': n}:
                    r = n
                case _:
                    r = 0
            r
        """),
        3,
    ),
    (
        "try / except / finally",
        code("""
            out = []
            try:
                1 / 0
            except ZeroDivisionError:
                out.append('caught')
            finally:
                out.append('done')
            out
        """),
        ["caught", "done"],
    ),
    (
        "raise and catch ValueError",
        code("""
            try:
                raise ValueError('bad')
            except ValueError as e:
                r = str(e)
            r
        """),
        "bad",
    ),
    (
        "functions with defaults and kwargs",
        code("""
            def f(a, b=2, *args, **kw):
                return [a, b, list(args), kw]
            f(1, 3, 4, x=5)
        """),
        [1, 3, [4], {"x": 5}],
    ),
    (
        "closures and nonlocal",
        code("""
            def counter():
                n = 0
                def inc():
                    nonlocal n
                    n += 1
                    return n
                return inc
            c = counter()
            c()
            c()
        """),
        2,
    ),
    (
        "classes",
        code("""
            class P:
                def __init__(self, x):
                    self.x = x
                def double(self):
                    return self.x * 2
            P(4).double()
        """),
        8,
    ),
    (
        "inheritance and super",
        code("""
            class A:
                def hi(self):
                    return 'a'
            class B(A):
                def hi(self):
                    return super().hi() + 'b'
            B().hi()
        """),
        "ab",
    ),
    (
        "dunder methods",
        code("""
            class V:
                def __init__(self, n):
                    self.n = n
                def __add__(self, o):
                    return V(self.n + o.n)
                def __repr__(self):
                    return f'V({self.n})'
            repr(V(1) + V(2))
        """),
        "V(3)",
    ),
    (
        "properties",
        code("""
            class C:
                @property
                def v(self):
                    return 7
            C().v
        """),
        7,
    ),
    (
        "dataclasses",
        code("""
            from dataclasses import dataclass
            @dataclass
            class Hit:
                name: str
                n: int = 0
            h = Hit('x', 2)
            (h.name, h.n)
        """),
        ["x", 2],
    ),
    (
        "generators with yield",
        code("""
            def gen():
                yield 1
                yield 2
            list(gen())
        """),
        [1, 2],
    ),
    (
        "while, break, continue",
        code("""
            i, out = 0, []
            while True:
                i += 1
                if i == 2:
                    continue
                if i > 4:
                    break
                out.append(i)
            out
        """),
        [1, 3, 4],
    ),
    (
        "enumerate, zip, reversed",
        "[(i, a, b) for i, (a, b) in enumerate(zip('ab', reversed('xy')))]",
        [[0, "a", "y"], [1, "b", "x"]],
    ),
    ("min / max with key", "max(['a', 'abc', 'ab'], key=len)", "abc"),
    ("any / all", "(any(x > 2 for x in [1, 3]), all([]))", [True, True]),
    ("string methods", "' A,b , C '.strip().lower().replace(' ', '').split(',')", ["a", "b", "c"]),
    ("str.join and str.format", "'-'.join(['a', 'b']) + ' {}'.format(1)", "a-b 1"),
    ("slicing", "('abcdef'[1:4], [1, 2, 3, 4][::-1])", ["bcd", [4, 3, 2, 1]]),
    (
        "dict methods",
        code("""
            d = {}
            d.setdefault('a', []).append(1)
            (d.get('a'), d.get('z', 0), sorted(d.items()))
        """),
        [[1], 0, [["a", [1]]]],
    ),
    ("set operations", "sorted({1, 2, 3} & {2, 3, 4})", [2, 3]),
    (
        "isinstance and type conversion",
        "(isinstance(1, int), int('12'), float('1.5'), str(3))",
        [True, 12, 1.5, "3"],
    ),
    ("type hints in code", "def f(x: int) -> list[int]:\n    return [x]\nf(1)", [1]),
    ("global", "g = 1\ndef f():\n    global g\n    g = 2\nf()\ng", 2),
    (
        "with statement",
        code("""
            class CM:
                def __enter__(self):
                    return 'in'
                def __exit__(self, *a):
                    return False
            with CM() as v:
                r = v
            r
        """),
        "in",
    ),
    (
        # Monty adds the failed expression to the message, which helps the model.
        "assert",
        code("""
            try:
                assert 1 == 2, 'nope'
            except AssertionError as e:
                r = str(e)
            r
        """),
        "nope\nassert 1 == 2",
    ),
    ("async def and await", "async def f():\n    return 3\nawait f()", 3),
    (
        "asyncio.gather",
        code("""
            import asyncio
            async def f(x):
                return x * 2
            await asyncio.gather(f(1), f(2))
        """),
        [2, 4],
    ),
]

STDLIB = [
    ("json", "import json\njson.loads(json.dumps({'a': [1, None]}))", {"a": [1, None]}),
    (
        "datetime: dates and timedelta",
        "from datetime import date, timedelta\n(date(2024, 1, 31) + timedelta(days=1)).isoformat()",
        "2024-02-01",
    ),
    (
        "datetime: strptime",
        "from datetime import datetime\ndatetime.strptime('20240115', '%Y%m%d').year",
        2024,
    ),
    (
        "datetime: strftime",
        "from datetime import date\ndate(2024, 1, 5).strftime('%Y-%m')",
        "2024-01",
    ),
    (
        "re: findall",
        "import re\nre.findall(r'NCT\\d{8}', 'see NCT01234567 and NCT07654321')",
        ["NCT01234567", "NCT07654321"],
    ),
    ("re: sub", "import re\nre.sub(r'\\s+', ' ', 'a   b\\tc')", "a b c"),
    (
        "collections.Counter",
        "from collections import Counter\nCounter('abca').most_common(1)",
        [["a", 2]],
    ),
    (
        "collections.defaultdict",
        "from collections import defaultdict\nd = defaultdict(list)\nd['x'].append(1)\ndict(d)",
        {"x": [1]},
    ),
    (
        "collections.namedtuple",
        "from collections import namedtuple\nP = namedtuple('P', 'x y')\nP(1, 2).y",
        2,
    ),
    (
        "collections.OrderedDict",
        "from collections import OrderedDict\nlist(OrderedDict([('b', 1), ('a', 2)]))",
        ["b", "a"],
    ),
    (
        "itertools.groupby",
        "from itertools import groupby\n[(k, len(list(g))) for k, g in groupby('aabccc')]",
        [["a", 2], ["b", 1], ["c", 3]],
    ),
    (
        "itertools.chain and islice",
        "from itertools import chain, islice\nlist(islice(chain([1, 2], [3, 4]), 3))",
        [1, 2, 3],
    ),
    (
        "math",
        "import math\n(math.sqrt(16), math.ceil(1.2), round(math.log10(1000)))",
        [4.0, 2, 3],
    ),
    (
        "statistics",
        "import statistics\n(statistics.mean([1, 2, 3]), statistics.median([3, 1, 2]))",
        [2, 2],
    ),
    (
        "functools.reduce",
        "from functools import reduce\nreduce(lambda a, b: a * b, [1, 2, 3, 4])",
        24,
    ),
    (
        "functools.lru_cache",
        code("""
            from functools import lru_cache
            @lru_cache(maxsize=None)
            def fib(n):
                return n if n < 2 else fib(n - 1) + fib(n - 2)
            fib(20)
        """),
        6765,
    ),
    (
        "operator.itemgetter",
        "from operator import itemgetter\nsorted([{'n': 2}, {'n': 1}], key=itemgetter('n'))",
        [{"n": 1}, {"n": 2}],
    ),
    ("heapq", "import heapq\nheapq.nlargest(2, [5, 1, 9, 3])", [9, 5]),
    ("bisect", "import bisect\nbisect.bisect_left([1, 3, 5], 4)", 2),
    (
        "copy.deepcopy",
        "import copy\na = [[1]]\nb = copy.deepcopy(a)\nb[0].append(2)\na",
        [[1]],
    ),
    ("enum", "from enum import Enum\nclass C(Enum):\n    A = 1\nC.A.name", "A"),
    (
        "decimal",
        "from decimal import Decimal\nstr(Decimal('0.1') + Decimal('0.2'))",
        "0.3",
    ),
    (
        "fractions",
        "from fractions import Fraction\nstr(Fraction(1, 3) + Fraction(1, 6))",
        "1/2",
    ),
    ("string module", "import string\nstring.ascii_lowercase[:3]", "abc"),
    ("textwrap", "import textwrap\ntextwrap.shorten('a b c d e f', width=7)", "a [...]"),
    (
        "typing",
        code("""
            from typing import Any, Optional
            def f(x: Optional[Any] = None) -> int:
                return 1
            f()
        """),
        1,
    ),
    ("urllib.parse", "from urllib.parse import quote\nquote('a b')", "a%20b"),
    (
        "random (seeded)",
        "import random\nrandom.seed(1)\nlen([random.random() for _ in range(3)])",
        3,
    ),
    ("time", "import time\nisinstance(time.time(), float)", True),
    (
        "csv",
        "import csv, io\nlist(csv.reader(io.StringIO('a,b\\n1,2')))",
        [["a", "b"], ["1", "2"]],
    ),
]

FEATURES = [*LANGUAGE, *STDLIB]

# What doesn't work, and what model code can use instead (docs/python-subset.md).
NOT_SUPPORTED = {
    "inheritance and super": "no class inheritance: use plain classes or dicts",
    "properties": "no @property or other method decorators: use plain methods",
    "generators with yield": "no generators: build a list",
    "collections.OrderedDict": "use dict, which keeps insertion order",
    "statistics": "use sum(xs) / len(xs); sort for the median",
    "operator.itemgetter": "use a lambda, e.g. key=lambda r: r['n']",
    "heapq": "use sorted(xs, reverse=True)[:n]",
    "enum": "no enum (it needs inheritance): use string constants",
    "fractions": "not available",
    "string module": "not available: write the characters out",
    "textwrap": "not available: slice strings",
    "urllib.parse": "not needed: request() encodes parameters",
    "csv": "no io or csv: split lines and commas",
}
