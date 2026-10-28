"""Generate a catalog source from an OpenAPI spec (`openrx catalog import-openapi`).

Hand-written and generated content stay in separate files (as in D-009):

    openapi_specs/<source>.yaml        hand-written: which operations, plus guide and notes
    openapi_specs/<source>.spec.yaml   a saved copy of the upstream spec (refresh: --fetch)
    sources/<source>.yaml              generated from the two above; never edited by hand

Generating from a saved spec makes the output reproducible, so a test can regenerate it
and fail if the committed file was edited or not regenerated.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openrx.catalog.loader import read_yaml
from openrx.catalog.schema import FixedParams, Source

GENERATED_HEADER = """\
# GENERATED FILE: do not edit. Change openapi_specs/{id}.yaml instead, then run
#   openrx catalog import-openapi {id}
# Generated from the {name} OpenAPI spec, version {version}.
"""


class OperationOverlay(BaseModel):
    """One upstream operation to import, with the parts a person writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(description="The spec's operationId, e.g. listStudies.")
    id: str = Field(description="Endpoint id in our catalog, e.g. search_studies.")
    description: str = Field(min_length=1)
    exclude_params: tuple[str, ...] = ()
    fixed_params: FixedParams = Field(default_factory=dict)
    response_notes: str = ""
    example_params: dict[str, str | int | float | bool] = Field(default_factory=dict)


class Overlay(BaseModel):
    """The hand-written input for one generated source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_url: str
    source: dict[str, Any] = Field(description="Source fields not in the spec: id, name, ...")
    operations: tuple[OperationOverlay, ...] = Field(min_length=1)


class OpenAPIImportError(Exception):
    """The spec or the overlay can't be turned into a catalog source."""


def overlay_path(root: Path, source_id: str) -> Path:
    """The hand-written overlay for a generated source."""
    return root / "openapi_specs" / f"{source_id}.yaml"


def spec_path(root: Path, source_id: str) -> Path:
    """The saved copy of the upstream spec."""
    return root / "openapi_specs" / f"{source_id}.spec.yaml"


def generated_text(root: Path, source_id: str) -> str:
    """The text `sources/<source_id>.yaml` should have, from the overlay and saved spec.

    Raises:
        FileNotFoundError: if the overlay or the saved spec is missing.
        OpenAPIImportError: if they can't be turned into a valid source.
    """
    try:
        overlay = Overlay.model_validate(read_yaml(overlay_path(root, source_id)))
    except ValidationError as e:
        raise OpenAPIImportError(f"{overlay_path(root, source_id).name}: {e}") from None
    spec = read_yaml(spec_path(root, source_id))
    if overlay.source.get("id") != source_id:
        raise OpenAPIImportError(f"overlay source id must be {source_id!r}")
    return render(generate(overlay, spec), spec)


def write_source(root: Path, source_id: str) -> Path:
    """Regenerate `sources/<source_id>.yaml` and return its path."""
    text = generated_text(root, source_id)
    out = root / "sources" / f"{source_id}.yaml"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def generate(overlay: Overlay, spec: dict[str, Any]) -> dict[str, Any]:
    """Build the source's catalog data from the overlay and the spec.

    Raises:
        OpenAPIImportError: if an operation or excluded parameter doesn't exist in the spec.
    """
    servers = spec.get("servers") or []
    if not servers:
        raise OpenAPIImportError("spec has no servers; can't tell the base URL")
    operations = _operations_by_id(spec)

    endpoints = []
    for op in overlay.operations:
        if op.operation not in operations:
            raise OpenAPIImportError(f"no operation {op.operation!r} in the spec")
        method, path, spec_op = operations[op.operation]
        if method != "get":
            raise OpenAPIImportError(f"{op.operation}: only GET operations can be imported")
        endpoints.append(_endpoint(op, path, spec_op, spec))

    data: dict[str, Any] = {
        "id": overlay.source["id"],
        "name": overlay.source["name"],
        "base_url": servers[0]["url"].rstrip("/"),
        **{k: v for k, v in overlay.source.items() if k not in ("id", "name")},
        "endpoints": endpoints,
    }
    try:
        Source.model_validate(data)  # never write a file the loader would reject
    except ValidationError as e:
        raise OpenAPIImportError(f"generated source is invalid: {e}") from None
    return data


def render(data: dict[str, Any], spec: dict[str, Any]) -> str:
    """The generated YAML file's text, with its do-not-edit header."""
    header = GENERATED_HEADER.format(
        id=data["id"], name=data["name"], version=spec.get("info", {}).get("version", "?")
    )
    body = yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=88)
    return header + "\n" + body


class _Dumper(yaml.SafeDumper):
    """SafeDumper that writes multi-line strings as readable `|` blocks."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def _operations_by_id(spec: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    found = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if isinstance(op, dict) and "operationId" in op:
                found[op["operationId"]] = (method, path, op)
    return found


def _endpoint(
    op: OperationOverlay, path: str, spec_op: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    spec_params = [_resolve(p, spec) for p in spec_op.get("parameters", [])]
    names = {p["name"] for p in spec_params}
    if unknown := sorted(set(op.exclude_params) - names):
        raise OpenAPIImportError(f"{op.operation}: exclude_params not in the spec: {unknown}")

    skip = set(op.exclude_params) | set(op.fixed_params)
    params = [_param(p, spec) for p in spec_params if p["name"] not in skip]

    endpoint: dict[str, Any] = {"id": op.id, "path": path, "description": op.description}
    if op.fixed_params:
        endpoint["fixed_params"] = dict(op.fixed_params)
    endpoint["params"] = params
    if op.example_params:
        endpoint["example_params"] = dict(op.example_params)
    if op.response_notes:
        endpoint["response_notes"] = op.response_notes
    return endpoint


def _param(spec_param: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    schema = _resolve(spec_param.get("schema", {}), spec)
    is_list = schema.get("type") == "array"
    item = _resolve(schema.get("items", {}), spec) if is_list else schema

    description = _clean(spec_param.get("description", "")) or spec_param["name"]
    if description[-1] not in ".!?":
        description += "."  # sentences follow
    if allowed := item.get("enum"):
        description += " Allowed: " + ", ".join(map(str, allowed)) + "."
    if is_list:
        description += " Several values: separate with |."

    param: dict[str, Any] = {"name": spec_param["name"]}
    if spec_param["in"] == "path":
        param["in"] = "path"
    kind = "string" if is_list else _TYPES.get(item.get("type", "string"), "string")
    if kind != "string":
        param["type"] = kind
    if spec_param.get("required"):
        param["required"] = True
    param["description"] = description
    if (example := _example(spec_param)) is not None:
        param["example"] = example
    return param


_TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}


def _example(spec_param: dict[str, Any]) -> str | int | float | bool | None:
    if "example" in spec_param:
        value = spec_param["example"]
    elif examples := spec_param.get("examples"):
        value = next(iter(examples.values())).get("value")
    else:
        return None
    if isinstance(value, list):
        return "|".join(map(str, value))
    return value if isinstance(value, str | int | float | bool) else None


def _resolve(node: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Follow a local "$ref": "#/components/..." pointer."""
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise OpenAPIImportError(f"only local $refs are supported, got {ref!r}")
        target: Any = spec
        for part in ref[2:].split("/"):
            target = target[part]
        node = target
    return node


def _clean(text: str) -> str:
    """Make a spec description readable as plain text.

    Markdown links become their text: the targets are relative website paths that a
    caller can't use. Whitespace is collapsed into one paragraph.
    """
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()
