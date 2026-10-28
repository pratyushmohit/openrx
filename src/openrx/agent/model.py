"""The chat model: Bedrock's Converse API, on Floci locally, answered by Ollama (E1).

Configuration (environment):

    AWS_ENDPOINT_URL   Floci, e.g. http://127.0.0.1:4566 (the Makefile sets it)
    OPENRX_MODEL_ID    Bedrock model id (default openrx-local; Floci's proxy maps any id
                       to its default Ollama model, qwen3.5)
"""

import os
from collections.abc import Mapping

from langchain_aws import ChatBedrockConverse

DEFAULT_MODEL_ID = "openrx-local"


def make_model(env: Mapping[str, str] = os.environ) -> ChatBedrockConverse:
    """The agent's model: temperature 0, so runs are as repeatable as possible (NFR-5)."""
    return ChatBedrockConverse(
        model=env.get("OPENRX_MODEL_ID", DEFAULT_MODEL_ID),
        # langchain-aws picks request details by provider; Floci's proxy accepts the
        # plain Converse shape that "meta" uses (checked in M4 with tool calling).
        provider="meta",
        base_url=env.get("AWS_ENDPOINT_URL") or None,
        region_name=env.get("AWS_DEFAULT_REGION", "us-east-1"),
        temperature=0,
    )
