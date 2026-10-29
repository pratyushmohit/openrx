"""How many tokens OpenRx's tool definitions cost the model (docs/design.md NFR-1, §6.2).

Counts what the server actually sends: its list_tools output (names, descriptions,
input schemas, annotations) as JSON, plus the instructions. The budget is 1,500
tokens. For comparison it also builds the definitions a classic one-tool-per-endpoint
server would expose for the same catalog.

Tokens are counted with tiktoken's o200k_base encoding, a common reference; the local
model (qwen3.5) uses its own tokenizer, so its counts differ somewhat. tiktoken is in
the dev and evals groups, not a runtime dependency, so it is imported only when used.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastmcp import Client, FastMCP

from openrx.catalog.loader import Catalog

TOKEN_BUDGET = 1_500
ENCODING = "o200k_base"


@dataclass(frozen=True)
class Footprint:
    """Token counts for one set of tool definitions."""

    tools: int
    instructions: int
    tool_count: int

    @property
    def total(self) -> int:
        """Tools plus instructions."""
        return self.tools + self.instructions


def count_tokens(text: str) -> int:
    """Tokens in `text` under the reference encoding."""
    import tiktoken

    return len(tiktoken.get_encoding(ENCODING).encode(text))


def tool_definitions(server: FastMCP) -> tuple[list[dict[str, Any]], str]:
    """The server's tools as sent over MCP, and its instructions."""

    async def list_them() -> tuple[list[dict[str, Any]], str]:
        async with Client(server) as client:
            tools = await client.list_tools()
            definitions = [t.model_dump(by_alias=True, exclude_none=True) for t in tools]
            return definitions, client.instructions or ""

    return asyncio.run(list_them())


def server_footprint(server: FastMCP) -> Footprint:
    """Token footprint of a running server's tool definitions and instructions."""
    definitions, instructions = tool_definitions(server)
    return Footprint(
        tools=count_tokens(json.dumps(definitions)),
        instructions=count_tokens(instructions),
        tool_count=len(definitions),
    )


def per_endpoint_definitions(catalog: Catalog) -> list[dict[str, Any]]:
    """What a one-tool-per-endpoint server would expose for `catalog`.

    Kept deliberately lean to make the comparison fair: each tool has the endpoint's
    description and its parameters' names, types and descriptions, and nothing else
    (no query guides, response notes or examples, which OpenRx keeps in the catalog).
    """
    json_types = {"string": "string", "integer": "integer", "number": "number"}
    definitions = []
    for source in catalog:
        for endpoint in source.endpoints:
            properties = {
                p.name: {"type": json_types.get(p.type, "boolean"), "description": p.description}
                for p in endpoint.params
            }
            definitions.append(
                {
                    "name": f"{source.id}_{endpoint.id}",
                    "description": f"{source.name}: {endpoint.description}",
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": [p.name for p in endpoint.params if p.required],
                    },
                }
            )
    return definitions


def per_endpoint_footprint(catalog: Catalog) -> Footprint:
    """Token footprint of the one-tool-per-endpoint alternative."""
    definitions = per_endpoint_definitions(catalog)
    return Footprint(
        tools=count_tokens(json.dumps(definitions)), instructions=0, tool_count=len(definitions)
    )
