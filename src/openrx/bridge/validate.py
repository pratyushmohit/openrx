"""Check a `request(source, path, params)` call against the catalog (README FR-6).

This is a security boundary: the arguments come from model-written code, so nothing is
trusted. A request is accepted only if its source exists, its path matches one of that
source's endpoint templates, and every parameter is declared, allowed and of the right
type. Anything else is rejected with a code (for metrics) and a message the model can
act on (README FR-9).
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from openrx.catalog.loader import Catalog
from openrx.catalog.schema import Endpoint, Source

ParamValue = str | int | float | bool

MAX_PARAMS = 30
MAX_VALUE_LENGTH = 2000
# What a single path segment, or a value filling a {placeholder}, may contain.
_SEGMENT = r"[A-Za-z0-9._~-]+"


class RequestRejected(Exception):  # noqa: N818 - "rejected" reads better than "error"
    """The request was not sent. `code` names the kind of problem."""

    def __init__(self, code: str, message: str) -> None:
        """Create a rejection with a short machine-readable code and a message."""
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ValidRequest:
    """A request that passed every check, ready to be sent."""

    source: Source
    endpoint: Endpoint
    path: str
    path_params: dict[str, str] = field(default_factory=dict)
    params: dict[str, ParamValue] = field(default_factory=dict)


def validate_request(catalog: Catalog, source: Any, path: Any, params: Any = None) -> ValidRequest:
    """Return the checked request, or raise RequestRejected explaining what is wrong."""
    src = _check_source(catalog, source)
    _check_path_shape(path)
    endpoint, path_params = _match_endpoint(src, path)
    checked = _check_params(src, endpoint, params)
    return ValidRequest(src, endpoint, path, path_params, checked)


def _check_source(catalog: Catalog, source: Any) -> Source:
    if not isinstance(source, str):
        raise RequestRejected("bad_source", f"source must be a string, got {_kind(source)}")
    found = catalog.source(source)
    if found is None:
        known = [s.id for s in catalog]
        raise RequestRejected(
            "unknown_source",
            f"no source {source!r}{_did_you_mean(source, known)}; sources: {', '.join(known)}",
        )
    return found


def _check_path_shape(path: Any) -> None:
    if not isinstance(path, str):
        raise RequestRejected("bad_path", f"path must be a string, got {_kind(path)}")
    if "://" in path or path.startswith("//"):
        raise RequestRejected(
            "absolute_url",
            "pass the endpoint path from the catalog (e.g. /drug/event.json), not a URL",
        )
    if not path.startswith("/"):
        raise RequestRejected("bad_path", f"path must start with '/', got {path!r}")
    if "?" in path or "#" in path:
        raise RequestRejected("query_in_path", "put query parameters in params, not in the path")
    segments = path.split("/")[1:]
    if any(s in ("", ".", "..") for s in segments):
        raise RequestRejected("bad_path", f"path has an empty, '.' or '..' segment: {path!r}")
    if not all(re.fullmatch(_SEGMENT, s) for s in segments):
        raise RequestRejected(
            "bad_path",
            f"path may only contain letters, digits and '._~-' between slashes: {path!r}",
        )


def _match_endpoint(source: Source, path: str) -> tuple[Endpoint, dict[str, str]]:
    for endpoint in source.endpoints:
        match = _template_regex(endpoint.path).fullmatch(path)
        if match:
            return endpoint, match.groupdict()
    paths = [e.path for e in source.endpoints]
    raise RequestRejected(
        "unknown_path",
        f"{source.id} has no endpoint {path!r}{_did_you_mean(path, paths)}; "
        f"see catalog_endpoints({source.id!r})",
    )


def _template_regex(template: str) -> re.Pattern[str]:
    """Turn a path template into a regex; each {name} matches exactly one safe segment.

    For example "/rxcui/{rxcui}/related.json" matches "/rxcui/6809/related.json".
    """
    parts = re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", template)
    regex = "".join(
        f"(?P<{p[1:-1]}>{_SEGMENT})" if p.startswith("{") else re.escape(p) for p in parts
    )
    return re.compile(regex)


def _check_params(source: Source, endpoint: Endpoint, params: Any) -> dict[str, ParamValue]:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RequestRejected("bad_params", f"params must be a dict, got {_kind(params)}")
    if len(params) > MAX_PARAMS:
        raise RequestRejected("bad_params", f"at most {MAX_PARAMS} params per request")

    reserved = set(source.fixed_params_for(endpoint))
    if source.auth:
        reserved.add(source.auth.name)
    declared = {p.name: p for p in endpoint.params if p.location == "query"}
    in_path = {p.name for p in endpoint.params if p.location == "path"}

    checked: dict[str, ParamValue] = {}
    for name, value in params.items():
        if not isinstance(name, str):
            raise RequestRejected("bad_params", f"param names must be strings, got {_kind(name)}")
        if name in reserved:
            raise RequestRejected(
                "reserved_param", f"{name!r} is added automatically; leave it out"
            )
        if name in in_path:
            raise RequestRejected("path_param_in_params", f"{name!r} goes in the path, not params")
        if name not in declared:
            raise RequestRejected(
                "unknown_param",
                f"{source.id} {endpoint.path} has no param {name!r}"
                f"{_did_you_mean(name, list(declared))}; params: {', '.join(declared) or 'none'}",
            )
        checked[name] = _coerce(name, declared[name].type, value)

    if missing := [n for n, p in declared.items() if p.required and n not in checked]:
        raise RequestRejected("missing_param", f"required params missing: {', '.join(missing)}")
    return checked


def _coerce(name: str, kind: str, value: Any) -> ParamValue:
    """Accept the declared type, or a plain string spelling of it; nothing else."""
    if isinstance(value, list | tuple):
        raise RequestRejected(
            "bad_param_value",
            f"{name!r} takes one value; join several with '|' (or as the guide says)",
        )
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        raise RequestRejected(
            "bad_param_value", f"{name!r} is longer than {MAX_VALUE_LENGTH} chars"
        )

    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
    elif kind == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            return int(value)
    elif kind == "number":
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
            return float(value)
    elif isinstance(value, str):
        return value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)  # e.g. a PMID passed as a number
    raise RequestRejected("bad_param_value", f"{name!r} must be a {kind}, got {_kind(value)}")


def _did_you_mean(word: str, choices: list[str]) -> str:
    close = difflib.get_close_matches(word, choices, n=1, cutoff=0.6)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _kind(value: Any) -> str:
    return type(value).__name__
