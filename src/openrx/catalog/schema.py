"""Schema for catalog files (README §8.3).

One YAML file per data source, validated against these models. The catalog is the
single source of truth: the model reads it through the `catalog_*` functions, and the
bridge checks every request against it (README FR-6). Some rules here are therefore
security rules, not just tidiness: only GET, only https, and paths that can't be
bent into another URL.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Lowercase snake_case: "openfda", "drug_event". Ids appear in model-written code,
# so they must be simple, unambiguous identifiers.
_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class _Strict(BaseModel):
    # extra="forbid": a typo such as `requried: true` is an error, not silently ignored.
    # frozen=True: catalog entries are read-only once loaded.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Auth(_Strict):
    """How the bridge adds an API key to requests for a source."""

    type: Literal["query_param"]
    name: str = Field(description="Query parameter that carries the key, e.g. `api_key`.")
    secret: str = Field(description="Secrets Manager secret holding the key.")
    optional: bool = Field(
        default=True, description="Requests still work without a key (lower rate limits)."
    )


class RateLimit(_Strict):
    """Upstream rate limit the bridge must stay under."""

    per_second: float = Field(gt=0)


class Param(_Strict):
    """One request parameter of an endpoint."""

    name: str = Field(min_length=1)
    # `in` is a Python keyword, so the field is `location`; YAML still says `in:`.
    location: Literal["query", "path"] = Field(default="query", alias="in")
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = False
    description: str = Field(min_length=1)
    example: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _path_params_are_required(self) -> "Param":
        if self.location == "path" and not self.required:
            raise ValueError(f"path parameter {self.name!r} must be required")
        return self


class Endpoint(_Strict):
    """One API operation a source offers."""

    id: str = Field(pattern=_ID_PATTERN)
    path: str = Field(
        description="Path relative to the source's base URL, e.g. `/studies/{nctId}`."
    )
    # Read-only by design: OpenRx never changes data upstream.
    method: Literal["GET"] = "GET"
    description: str = Field(min_length=1)
    params: tuple[Param, ...] = ()
    response_notes: str = ""
    # Optional in the schema so entries can be drafted; `catalog lint` requires it.
    example_response: Any = None

    @field_validator("path")
    @classmethod
    def _path_is_safe(cls, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("must start with '/'")
        if "://" in path or path.startswith("//"):
            raise ValueError("must be a path, not a URL")
        if "?" in path or "#" in path:
            raise ValueError("must not contain a query string or fragment; use params")
        if ".." in path.split("/"):
            raise ValueError("must not contain '..' segments")
        # Allow-list the characters, so encoded tricks (%2e%2e, backslashes) can't slip in.
        if not re.fullmatch(r"[A-Za-z0-9._~/{}-]+", path):
            raise ValueError("may only contain letters, digits, '._~-/' and {placeholders}")
        return path

    @model_validator(mode="after")
    def _params_match_path(self) -> "Endpoint":
        names = [p.name for p in self.params]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate parameter names: {duplicates}")

        placeholders = _PLACEHOLDER.findall(self.path)
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in placeholders):
            raise ValueError(f"invalid placeholder in path {self.path!r}")
        path_params = {p.name for p in self.params if p.location == "path"}
        if missing := sorted(set(placeholders) - path_params):
            raise ValueError(f"path placeholders without a path parameter: {missing}")
        if unused := sorted(path_params - set(placeholders)):
            raise ValueError(f"path parameters not used in the path: {unused}")
        return self


class Source(_Strict):
    """One data source: a base URL plus the endpoints we allow under it."""

    id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, description="Human-readable name, e.g. `openFDA`.")
    base_url: str
    auth: Auth | None = None
    rate_limit: RateLimit
    guide: str = Field(default="", description="Query syntax notes shown by catalog_guide().")
    endpoints: tuple[Endpoint, ...] = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def _base_url_is_https(cls, url: str) -> str:
        # The bridge derives its host allowlist from base URLs, so they must be plain.
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?", url):
            raise ValueError("must be an https URL with no query string, port or credentials")
        if url.endswith("/"):
            raise ValueError("must not end with '/'; endpoint paths start with '/'")
        return url

    @model_validator(mode="after")
    def _endpoint_ids_are_unique(self) -> "Source":
        ids = [e.id for e in self.endpoints]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate endpoint ids: {duplicates}")
        return self

    def endpoint(self, endpoint_id: str) -> Endpoint | None:
        """Return the endpoint with this id, or None."""
        return next((e for e in self.endpoints if e.id == endpoint_id), None)
