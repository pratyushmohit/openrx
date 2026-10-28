"""Type-check stubs: the signatures of the functions model code can call (FR-14).

Monty type-checks code against these before running it, so a wrong call
(catalog_find(limit="ten"), request() from search code, r["dat"]) comes back as a
type error the model can fix without running anything. They are written out by hand
to stay readable; tests/runner/test_stubs.py checks them against the real functions.

request() raises on a rejected call rather than returning an error value: in code, a
failed call should stop with a traceback pointing at the line, which is what the model
needs to fix it.
"""

from typing import Literal

Runner = Literal["search", "execute"]

CATALOG_STUBS = '''\
from typing import Any


def catalog_sources() -> list[dict[str, Any]]:
    """Every data source: id, name, endpoint ids, has_guide."""
    ...


def catalog_endpoints(source: str | None = None) -> list[dict[str, Any]]:
    """Endpoints (source, id, path, description) of one source, or all."""
    ...


def catalog_endpoint(source: str, id: str, full_example: bool = False) -> dict[str, Any]:
    """One endpoint in full: params, response notes, example request and response."""
    ...


def catalog_find(text: str, limit: int = 10) -> list[dict[str, Any]]:
    """Endpoints matching the words in text, best first."""
    ...


def catalog_guide(source: str) -> str:
    """A source's query-syntax guide."""
    ...
'''

REQUEST_STUBS = '''\
from typing import TypedDict


class Response(TypedDict):
    status: int
    data: Any
    truncated: bool
    notes: list[str]


async def request(
    source: str, path: str, params: dict[str, str | int | float | bool] | None = None
) -> Response:
    """Call a catalogued endpoint through the bridge. Raises if the call is rejected."""
    ...
'''


def stubs_for(runner: Runner) -> str:
    """The stub text for a runner: search gets the catalog functions only."""
    if runner == "search":
        return CATALOG_STUBS
    return CATALOG_STUBS + "\n\n" + REQUEST_STUBS
