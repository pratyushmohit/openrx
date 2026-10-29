"""The read-only catalog functions that model-written code calls (docs/design.md FR-2).

These run as trusted code; the runner Lambdas expose them to Monty as external
functions (M3). Everything they return is plain JSON-shaped data (dict, list, str,
int, float, bool, None), because that is what can cross into the sandbox.

Errors are ValueErrors whose message says what was wrong and lists the valid choices,
so the model can fix its next call (docs/design.md FR-9).
"""

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from openrx.catalog.loader import Catalog
from openrx.catalog.schema import Endpoint, Source
from openrx.catalog.trim import trim

# Default size of the example in catalog_endpoint(): enough to show every field name,
# small enough not to fill the model's context (docs/design.md goal G2).
SHORT_EXAMPLE_ITEMS = 1
SHORT_EXAMPLE_STRING = 80
SHORT_EXAMPLE_TEXT = 600

FIND_LIMIT = 10

# How much a matching word counts, by where it matches.
_WEIGHTS = {"id": 3.0, "description": 2.0, "source": 2.0, "params": 1.0, "notes": 1.0}
_STOPWORDS = frozenset(
    "a an and are by can do does for from get how i in is it list me of on or show "
    "the to what which with".split()
)


def catalog_sources(catalog: Catalog) -> list[dict[str, Any]]:
    """Every data source: its id, name and endpoint ids."""
    return [
        {
            "id": source.id,
            "name": source.name,
            "endpoints": [e.id for e in source.endpoints],
            "has_guide": bool(source.guide),
        }
        for source in catalog
    ]


def catalog_endpoints(catalog: Catalog, source: str | None = None) -> list[dict[str, Any]]:
    """Endpoints of one source, or of all sources, with path and description."""
    sources = [_source(catalog, source)] if source is not None else list(catalog)
    return [_summary(s, e) for s in sources for e in s.endpoints]


def catalog_endpoint(
    catalog: Catalog, source: str, id: str, full_example: bool = False
) -> dict[str, Any]:
    """Everything about one endpoint: parameters, notes and an example response.

    The example is shortened (one item per list, short strings) unless `full_example`
    is true; it still shows every field name.
    """
    _require("id", id, str)
    _require("full_example", full_example, bool)
    src = _source(catalog, source)
    endpoint = src.endpoint(id)
    if endpoint is None:
        known = ", ".join(e.id for e in src.endpoints)
        raise ValueError(f"no endpoint {id!r} in {source!r}; endpoints: {known}")

    example = endpoint.example_response
    shortened = False
    if example is not None and not full_example:
        short = _shorten(example, endpoint.response_format)
        shortened = short != example
        example = short
    return {
        **_summary(src, endpoint),
        "method": endpoint.method,
        "params": [
            {
                "name": p.name,
                "in": p.location,
                "type": p.type,
                "required": p.required,
                "description": p.description,
                "example": p.example,
            }
            for p in endpoint.params
        ],
        "response_format": endpoint.response_format,
        "response_notes": endpoint.response_notes,
        "example_params": dict(endpoint.example_params),
        "example_response": example,
        "example_shortened": shortened,
    }


def catalog_guide(catalog: Catalog, source: str) -> str:
    """A source's query-syntax guide (empty string if it has none)."""
    return _source(catalog, source).guide


def catalog_find(catalog: Catalog, text: str, limit: int = FIND_LIMIT) -> list[dict[str, Any]]:
    """Endpoints matching the words in `text`, best first.

    Keyword scoring, not semantic search: each word counts by where it matches (id,
    description, source, parameter names, response notes) and by how rare it is
    across the catalog. Ties are broken by source and endpoint id, so the same query
    always gives the same order.
    """
    _require("text", text, str)
    _require("limit", limit, int)
    if not 1 <= limit <= 100:
        raise ValueError(f"limit must be between 1 and 100, got {limit}")
    words = _words(text)
    if not words:
        raise ValueError("catalog_find needs at least one search word, e.g. 'adverse events'")

    vocabs = {(s.id, e.id): _vocab(s, e) for s in catalog for e in s.endpoints}
    rarity = _rarity(vocabs.values())
    scored = []
    for source in catalog:
        for endpoint in source.endpoints:
            score = _score(words, vocabs[source.id, endpoint.id], rarity)
            if score > 0:
                scored.append((-score, source.id, endpoint.id, source, endpoint))
    scored.sort(key=lambda row: row[:3])
    return [
        {**_summary(source, endpoint), "score": round(-neg_score, 2)}
        for neg_score, _, _, source, endpoint in scored[:limit]
    ]


def catalog_functions(catalog: Catalog) -> dict[str, Callable[..., Any]]:
    """The functions as model code sees them, bound to `catalog` (for the runner)."""

    def sources() -> list[dict[str, Any]]:
        return catalog_sources(catalog)

    def endpoints(source: str | None = None) -> list[dict[str, Any]]:
        return catalog_endpoints(catalog, source)

    def endpoint(source: str, id: str, full_example: bool = False) -> dict[str, Any]:
        return catalog_endpoint(catalog, source, id, full_example)

    def find(text: str, limit: int = FIND_LIMIT) -> list[dict[str, Any]]:
        return catalog_find(catalog, text, limit)

    def guide(source: str) -> str:
        return catalog_guide(catalog, source)

    return {
        "catalog_sources": sources,
        "catalog_endpoints": endpoints,
        "catalog_endpoint": endpoint,
        "catalog_find": find,
        "catalog_guide": guide,
    }


def _require(name: str, value: Any, kind: type) -> None:
    # Model code may run without the type checker, so check here too (FR-9).
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ValueError(f"{name} must be a {kind.__name__}, got {type(value).__name__}")


def _source(catalog: Catalog, source_id: str) -> Source:
    _require("source", source_id, str)
    source = catalog.source(source_id)
    if source is None:
        known = ", ".join(s.id for s in catalog)
        raise ValueError(f"no source {source_id!r}; sources: {known}")
    return source


def _summary(source: Source, endpoint: Endpoint) -> dict[str, Any]:
    return {
        "source": source.id,
        "id": endpoint.id,
        "path": endpoint.path,
        "description": endpoint.description,
    }


def _shorten(example: Any, response_format: str) -> Any:
    if response_format == "text" and isinstance(example, str):
        return trim(example, max_string=SHORT_EXAMPLE_TEXT)
    return trim(example, max_items=SHORT_EXAMPLE_ITEMS, max_string=SHORT_EXAMPLE_STRING)


def _words(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in raw if w not in _STOPWORDS]


def _stem(word: str) -> str:
    # Just enough to match "events" with "event" and "studies" with "study".
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


Vocab = dict[str, set[str]]


def _vocab(source: Source, endpoint: Endpoint) -> Vocab:
    """The words of each searchable field of an endpoint."""
    fields = {
        "id": endpoint.id.replace("_", " "),
        "description": endpoint.description,
        "source": f"{source.id} {source.name}",
        "params": " ".join(p.name.replace(".", " ") for p in endpoint.params),
        "notes": endpoint.response_notes,
    }
    return {name: set(_words(value)) for name, value in fields.items()}


def _rarity(vocabs: Iterable[Vocab]) -> dict[str, float]:
    """Inverse document frequency of each word across endpoints.

    A word in every endpoint ("drug") counts little; a word in one endpoint
    ("misspelled") counts a lot.
    """
    vocabs = list(vocabs)
    counts: Counter[str] = Counter()
    for vocab in vocabs:
        counts.update(set().union(*vocab.values()))
    return {word: math.log(1 + len(vocabs) / n) for word, n in counts.items()}


def _score(words: list[str], vocab: Vocab, rarity: dict[str, float]) -> float:
    return sum(
        _WEIGHTS[field] * rarity[word]
        for word in words
        for field, field_words in vocab.items()
        if word in field_words
    )
