"""Talking to the deployed OpenRx on Floci from the eval tools (host side).

Uses the same environment as the Makefile: AWS_ENDPOINT_URL and dummy credentials.
"""

import json
import time
from typing import Any

import boto3
from botocore.config import Config

from openrx.bridge.fixtures import Mode

BRIDGE = "openrx-bridge"
EXECUTE_RUNNER = "openrx-execute-runner"
FIXTURE_BUCKET = "openrx-fixtures"


def lambda_client() -> Any:
    """A Lambda client for the deployed functions (runs can take a minute)."""
    return boto3.client("lambda", config=Config(read_timeout=120, retries={"max_attempts": 2}))


def s3_client() -> Any:
    """An S3 client; path-style addressing, which an emulator needs."""
    return boto3.client("s3", config=Config(s3={"addressing_style": "path"}))


def set_bridge_mode(lam: Any, mode: Mode) -> None:
    """Switch the bridge's fixture mode and wait until the change is live."""
    config = lam.get_function_configuration(FunctionName=BRIDGE)
    variables = {**config["Environment"]["Variables"], "OPENRX_FIXTURE_MODE": mode}
    lam.update_function_configuration(FunctionName=BRIDGE, Environment={"Variables": variables})
    for _ in range(120):
        status = lam.get_function_configuration(FunctionName=BRIDGE).get("LastUpdateStatus")
        if status in (None, "Successful"):
            return
        time.sleep(0.5)
    raise TimeoutError("the bridge's configuration did not update")


def run_code(lam: Any, code: str) -> dict[str, Any]:
    """Run code in the execute-runner and return its ToolResult."""
    payload = json.dumps({"code": code}).encode("utf-8")
    response = lam.invoke(FunctionName=EXECUTE_RUNNER, Payload=payload)
    return json.loads(response["Payload"].read())
