"""Turn a validated request into the exact HTTP request, and its fixture fingerprint.

Encoding is left to httpx, which sends spaces as "+", booleans as "true"/"false" and
percent-encodes everything else. Callers write plain values (README guides say so):
a literal "+" is sent as %2B, which openFDA silently treats as no matches.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from openrx.bridge.validate import ParamValue, ValidRequest
from openrx.catalog.schema import expand_placeholders


@dataclass(frozen=True)
class OutgoingRequest:
    """Everything needed to send one request upstream.

    `params` already holds the fixed parameters with ${NAME} placeholders filled, and
    the API key if one was given. `fingerprint` names its replay fixture.
    """

    source_id: str
    endpoint_id: str
    url: str
    params: dict[str, ParamValue]
    fingerprint: str
    auth_param: str | None = None
    notes: tuple[str, ...] = ()

    def url_for_log(self) -> str:
        """The full URL with the API key left out, safe to log (README NFR-3)."""
        shown = {k: v for k, v in self.params.items() if k != self.auth_param}
        return str(httpx.URL(self.url, params=shown))


def build_request(
    req: ValidRequest, config: Mapping[str, str], api_key: str | None = None
) -> OutgoingRequest:
    """Merge caller and fixed parameters, fill placeholders, and add the API key.

    Args:
        req: A request that passed validate_request().
        config: Values for ${NAME} placeholders (the bridge's environment).
        api_key: The source's key, if it has one and it is set.
    """
    fixed = req.source.fixed_params_for(req.endpoint)
    filled, missing = expand_placeholders(fixed, dict(config))
    params: dict[str, ParamValue] = {**req.params, **filled}

    auth_param = req.source.auth.name if req.source.auth else None
    if api_key and auth_param:
        params[auth_param] = api_key

    return OutgoingRequest(
        source_id=req.source.id,
        endpoint_id=req.endpoint.id,
        url=req.source.base_url + req.path,
        params=params,
        fingerprint=fingerprint(req),
        auth_param=auth_param,
        notes=tuple(f"${{{name}}} is not configured; sent without it" for name in missing),
    )


def fingerprint(req: ValidRequest) -> str:
    """A stable id for a request, used to name its replay fixture (D-013).

    It covers the source, the concrete path, the caller's parameters (after type
    coercion, so "5" and 5 are the same request) and the fixed parameters as written
    in the catalog, with ${NAME} placeholders unfilled. So a recording made with an
    API key, or with another contact email, replays for the same request, while a
    catalog change to a fixed parameter (e.g. retmode) correctly invalidates it.
    The API key is never part of it.
    """
    fixed = req.source.fixed_params_for(req.endpoint)
    canonical = json.dumps(
        {"source": req.source.id, "path": req.path, "params": req.params, "fixed": fixed},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
