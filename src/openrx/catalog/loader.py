"""Load catalog YAML files into validated `Source` objects.

Loading is where untrusted text becomes trusted data (see schema.py), so every problem
is reported with the file and the exact place in it. All files are checked before
failing, so one run shows every problem instead of one per run.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from openrx.catalog.schema import Source

# The catalog ships inside the package (D-008), next to this file.
SOURCES_DIR = Path(__file__).parent / "sources"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a catalog file."""

    file: str
    location: str
    message: str

    def __str__(self) -> str:
        """Format as `file: location: message`, one line, for terminal output."""
        where = f"{self.file}: {self.location}" if self.location else self.file
        return f"{where}: {self.message}"


class CatalogError(Exception):
    """The catalog could not be loaded. `problems` lists everything that is wrong."""

    def __init__(self, problems: list[Problem]) -> None:
        """Create the error from a non-empty list of problems."""
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


@dataclass(frozen=True)
class Catalog:
    """All sources, by id. Read-only."""

    sources: Mapping[str, Source]

    def source(self, source_id: str) -> Source | None:
        """Return the source with this id, or None."""
        return self.sources.get(source_id)

    def __iter__(self) -> Iterator[Source]:
        """Iterate over sources in id order."""
        return iter(self.sources[k] for k in sorted(self.sources))


def load_catalog(directory: Path = SOURCES_DIR) -> Catalog:
    """Load and validate every `*.yaml` file in `directory`.

    Raises:
        CatalogError: if any file is invalid, or there are no files at all.
    """
    sources, problems = _load_all(directory)
    if problems:
        raise CatalogError(problems)
    return Catalog(MappingProxyType(sources))


def lint_catalog(directory: Path = SOURCES_DIR) -> list[Problem]:
    """Check the catalog more strictly than loading does; return all problems.

    On top of everything `load_catalog` checks, every endpoint must have an example
    response (M1 exit criterion). The schema allows drafts without one.
    """
    sources, problems = _load_all(directory)
    for source in sources.values():
        for index, endpoint in enumerate(source.endpoints):
            if endpoint.example_response is None:
                problems.append(
                    Problem(
                        f"{source.id}.yaml",
                        f"endpoints[{index}] ({endpoint.id})",
                        "missing example_response",
                    )
                )
    return problems


def _load_all(directory: Path) -> tuple[dict[str, Source], list[Problem]]:
    files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    if not files:
        return {}, [Problem(str(directory), "", "no catalog files (*.yaml) found")]

    sources: dict[str, Source] = {}
    problems: list[Problem] = []
    for path in files:
        try:
            source = load_source(path)
        except CatalogError as e:
            problems.extend(e.problems)
            continue
        # Filenames are unique, so this also guarantees unique source ids.
        if source.id != path.stem:
            problems.append(
                Problem(path.name, "id", f"source id {source.id!r} must match the file name")
            )
            continue
        sources[source.id] = source
    return sources, problems


def load_source(path: Path) -> Source:
    """Load and validate one catalog file.

    Raises:
        CatalogError: if the file is not valid YAML or does not match the schema.
    """
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise CatalogError([Problem(path.name, "", f"invalid YAML: {_yaml_message(e)}")]) from None
    if not isinstance(data, dict):
        raise CatalogError(
            [Problem(path.name, "", "top level must be a mapping (key: value pairs)")]
        )

    try:
        return Source.model_validate(data)
    except ValidationError as e:
        raise CatalogError(
            [
                Problem(path.name, _location(err["loc"], data), _message(err))
                for err in _root_causes(e)
            ]
        ) from None


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys.

    Plain YAML loading silently keeps the last of two identical keys, so a duplicated
    `path:` would quietly override the first. SafeLoader (not yaml.Loader) also means
    YAML can never construct arbitrary Python objects.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _yaml_message(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    return f"{problem} (line {mark.line + 1})" if mark is not None else problem


def _root_causes(error: ValidationError) -> list[ErrorDetails]:
    """Drop knock-on errors.

    When an endpoint fails, Pydantic also reports "endpoints should have at least 1
    item" because no valid endpoint is left. That is a consequence, not a mistake, so
    skip a too_short error when a deeper error exists under the same location.
    """
    errors = error.errors()
    return [
        err
        for err in errors
        if not (
            err["type"] == "too_short"
            and any(
                len(o["loc"]) > len(err["loc"]) and o["loc"][: len(err["loc"])] == err["loc"]
                for o in errors
            )
        )
    ]


def _location(loc: tuple[int | str, ...], data: Any) -> str:
    """Render ("endpoints", 0, "path") as "endpoints[0] (drug_label).path".

    Adds the item's id after list indexes when there is one, so the reader doesn't
    have to count entries in a long file.
    """
    text = ""
    node = data
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
            node = node[part] if isinstance(node, list) and part < len(node) else None
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                text += f" ({node['id']})"
        else:
            text += f".{part}" if text else part
            node = node.get(part) if isinstance(node, dict) else None
    return text


def _message(err: ErrorDetails) -> str:
    if err["type"] == "extra_forbidden":
        return "unknown field (typo?)"
    if err["type"] == "missing":
        return "required field is missing"
    return err["msg"].removeprefix("Value error, ")
