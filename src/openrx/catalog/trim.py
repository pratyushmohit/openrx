"""Shrink a JSON value while keeping its shape.

Used for catalog examples, catalog_endpoint() previews and oversized sandbox results.
No dependencies, so the runner Lambdas can use it without the capture tool's httpx.
"""

from typing import Any

# Defaults sized for catalog examples: small enough to read at a glance, big enough to
# show the shape.
MAX_ITEMS = 2
MAX_STRING = 200


def trim(value: Any, max_items: int = MAX_ITEMS, max_string: int = MAX_STRING) -> Any:
    """Shrink a JSON value but keep its shape.

    Lists keep their first `max_items` entries, strings are cut to `max_string`
    characters (ending in "…"), and objects keep every key: the field names are the
    most useful part of an example.
    """
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "…"
    if isinstance(value, list):
        return [trim(v, max_items, max_string) for v in value[:max_items]]
    if isinstance(value, dict):
        return {k: trim(v, max_items, max_string) for k, v in value.items()}
    return value
