"""Record example responses for catalog endpoints (`openrx catalog capture-examples`).

This runs on a developer's machine as trusted code: it calls each endpoint's
`example_params` request on the live API, trims the response to a small sample, and
writes it to examples/<source>/<endpoint>.json (D-009). Model-written code never runs
here (D-005).

No API key is sent, so keys can never end up in an example file.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from openrx.catalog.loader import CATALOG_DIR, example_path
from openrx.catalog.schema import Endpoint, Source

# Small enough to read at a glance, big enough to show the shape.
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


def example_request(source: Source, endpoint: Endpoint) -> tuple[str, dict[str, Any]]:
    """Return the URL and query parameters for an endpoint's example request."""
    path_names = {p.name for p in endpoint.params if p.location == "path"}
    path = endpoint.path
    for name in path_names:
        path = path.replace(f"{{{name}}}", quote(str(endpoint.example_params[name]), safe=""))
    query = {k: v for k, v in endpoint.example_params.items() if k not in path_names}
    return source.base_url + path, query


@dataclass(frozen=True)
class Captured:
    """Result of capturing one endpoint."""

    endpoint_id: str
    file: Path | None
    error: str | None = None


def capture_source(
    source: Source,
    client: httpx.Client,
    root: Path = CATALOG_DIR,
    only: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Captured]:
    """Capture examples for every endpoint of `source` (or just `only`).

    Endpoints without `example_params` are skipped with an error, and so are failed
    requests: an example must be real data, so nothing is written for them.
    """
    endpoints = [e for e in source.endpoints if only is None or e.id == only]
    if only is not None and not endpoints:
        return [Captured(only, None, f"no endpoint {only!r} in {source.id}")]

    results = []
    for index, endpoint in enumerate(endpoints):
        if index:
            sleep(1 / source.rate_limit.per_second)  # stay under the upstream limit
        results.append(_capture_one(source, endpoint, client, root))
    return results


def _capture_one(source: Source, endpoint: Endpoint, client: httpx.Client, root: Path) -> Captured:
    if not endpoint.example_params:
        return Captured(endpoint.id, None, "no example_params in the YAML")

    url, query = example_request(source, endpoint)
    try:
        response = client.get(url, params=query)
    except httpx.HTTPError as e:
        return Captured(endpoint.id, None, f"request failed: {e}")
    if response.status_code != 200:
        return Captured(endpoint.id, None, f"HTTP {response.status_code}: {response.text[:200]}")
    try:
        body = response.json()
    except ValueError:
        return Captured(endpoint.id, None, "response is not JSON")

    file = example_path(root, source.id, endpoint.id)
    file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "request": {"url": url, "params": query},
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "trimmed": {"max_items": MAX_ITEMS, "max_string": MAX_STRING},
        "response": trim(body),
    }
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    # newline="\n": LF on every OS, matching .gitattributes (Windows would write CRLF).
    file.write_text(text, encoding="utf-8", newline="\n")
    return Captured(endpoint.id, file)
