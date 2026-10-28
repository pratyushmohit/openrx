"""Record example responses for catalog endpoints (`openrx catalog capture-examples`).

This runs on a developer's machine as trusted code: it calls each endpoint's
`example_params` request on the live API, trims the response to a small sample, and
writes it to examples/<source>/<endpoint>.json (D-009). Model-written code never runs
here (D-005).

What reaches git: no API key is sent, so none can end up in an example. Fixed
parameters are sent with their ${NAME} placeholders filled from the environment, but
recorded with the placeholders unfilled, so values like a contact email never do.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from openrx.catalog.loader import CATALOG_DIR, example_path
from openrx.catalog.schema import Endpoint, Source, expand_placeholders
from openrx.catalog.trim import MAX_ITEMS, MAX_STRING, trim

# A text response (e.g. an abstract) is one string, so it may be longer.
MAX_TEXT = 2000


def example_request(source: Source, endpoint: Endpoint) -> tuple[str, dict[str, Any]]:
    """Return the URL and caller parameters for an endpoint's example request.

    Fixed parameters are not included; see `capture_source`.
    """
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
    warnings: tuple[str, ...] = ()


def capture_source(
    source: Source,
    client: httpx.Client,
    root: Path = CATALOG_DIR,
    only: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    env: Mapping[str, str] = os.environ,
) -> list[Captured]:
    """Capture examples for every endpoint of `source` (or just `only`).

    Endpoints that take parameters but have no `example_params` are skipped with an
    error, and so are failed requests: an example must be real data, so nothing is
    written for them. An endpoint without parameters is called as it is.
    """
    endpoints = [e for e in source.endpoints if only is None or e.id == only]
    if only is not None and not endpoints:
        return [Captured(only, None, f"no endpoint {only!r} in {source.id}")]

    results = []
    for index, endpoint in enumerate(endpoints):
        if index:
            sleep(1 / source.rate_limit.per_second)  # stay under the upstream limit
        results.append(_capture_one(source, endpoint, client, root, env))
    return results


def _capture_one(
    source: Source, endpoint: Endpoint, client: httpx.Client, root: Path, env: Mapping[str, str]
) -> Captured:
    if endpoint.params and not endpoint.example_params:
        return Captured(endpoint.id, None, "no example_params in the YAML")

    url, query = example_request(source, endpoint)
    fixed = source.fixed_params_for(endpoint)
    filled, missing = expand_placeholders(fixed, dict(env))
    warnings = tuple(f"${{{name}}} is not set; sent without it" for name in missing)
    try:
        response = client.get(url, params={**query, **filled})
    except httpx.HTTPError as e:
        return Captured(endpoint.id, None, f"request failed: {e}", warnings)
    if response.status_code != 200:
        error = f"HTTP {response.status_code}: {response.text[:200]}"
        return Captured(endpoint.id, None, error, warnings)

    if endpoint.response_format == "text":
        body: Any = trim(response.text, max_string=MAX_TEXT)
    else:
        try:
            body = trim(response.json())
        except ValueError:
            return Captured(endpoint.id, None, "response is not JSON", warnings)

    file = example_path(root, source.id, endpoint.id)
    file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        # Placeholders stay unfilled here: this file is committed.
        "request": {"url": url, "params": {**query, **fixed}},
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "trimmed": {"max_items": MAX_ITEMS, "max_string": MAX_STRING, "max_text": MAX_TEXT},
        "response": body,
    }
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    # newline="\n": LF on every OS, matching .gitattributes (Windows would write CRLF).
    file.write_text(text, encoding="utf-8", newline="\n")
    return Captured(endpoint.id, file, None, warnings)
